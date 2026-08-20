"""The attachment security gate decides what the pipeline is allowed to open.

Every test here describes an attack or a resource-exhaustion case, because the
gate exists purely to fail closed on hostile input.
"""

import hashlib
import zipfile
from io import BytesIO

import pytest

from app.config import Settings
from app.models import Attachment, AttachmentStatus
from app.pipeline.security import SecurityGate, safe_storage_name

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n%%EOF\n"
MACHO_BYTES = b"\xcf\xfa\xed\xfe" + b"\x00" * 64
ELF_BYTES = b"\x7fELF" + b"\x00" * 64
ZIP_BYTES = b"PK\x03\x04" + b"\x00" * 64


def make_docx(*, macro_enabled: bool = False) -> bytes:
    """Build the smallest thing a DOCX reader will accept."""
    buffer = BytesIO()
    content_type = (
        "application/vnd.ms-word.document.macroEnabled.main+xml"
        if macro_enabled
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
    )
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/'
            f'content-types"><Override PartName="/word/document.xml" ContentType="{content_type}"/>'
            "</Types>",
        )
        archive.writestr("word/document.xml", "<document/>")
        if macro_enabled:
            archive.writestr("word/vbaProject.bin", b"\x00macro")
    return buffer.getvalue()


def attachment_for(payload: bytes, filename: str, mime: str = PDF_MIME) -> Attachment:
    return Attachment(
        filename=filename,
        declared_mime=mime,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )


@pytest.fixture
def gate() -> SecurityGate:
    return SecurityGate(Settings())


class TestAllowedFiles:
    def test_a_real_pdf_is_accepted(self, gate: SecurityGate):
        attachment = attachment_for(PDF_BYTES, "invoice.pdf")

        checked = gate.screen(attachment, PDF_BYTES)

        assert checked.status is AttachmentStatus.PENDING
        assert checked.detected_mime == PDF_MIME
        assert checked.reason is None

    def test_a_real_docx_is_accepted(self, gate: SecurityGate):
        payload = make_docx()
        attachment = attachment_for(payload, "contract.docx", DOCX_MIME)

        checked = gate.screen(attachment, payload)

        assert checked.status is AttachmentStatus.PENDING
        assert checked.detected_mime == DOCX_MIME

    def test_a_correct_file_with_the_wrong_declared_type_is_still_accepted(
        self, gate: SecurityGate
    ):
        # Some clients send application/octet-stream for everything. The bytes
        # decide, not the header.
        attachment = attachment_for(PDF_BYTES, "invoice.pdf", "application/octet-stream")

        assert gate.screen(attachment, PDF_BYTES).status is AttachmentStatus.PENDING


class TestTypeConfusion:
    @pytest.mark.parametrize(
        "payload, label",
        [
            (MACHO_BYTES, "a macOS executable"),
            (ELF_BYTES, "a Linux executable"),
            (b"MZ\x90\x00" + b"\x00" * 64, "a Windows executable"),
        ],
    )
    def test_an_executable_renamed_to_pdf_is_rejected(
        self, gate: SecurityGate, payload: bytes, label: str
    ):
        attachment = attachment_for(payload, "invoice.pdf")

        checked = gate.screen(attachment, payload)

        assert checked.status is AttachmentStatus.REJECTED, f"{label} must never be accepted"
        assert "does not match" in checked.reason.lower()

    def test_a_script_renamed_to_pdf_is_rejected(self, gate: SecurityGate):
        payload = b"#!/bin/sh\nrm -rf /\n"
        attachment = attachment_for(payload, "report.pdf")

        assert gate.screen(attachment, payload).status is AttachmentStatus.REJECTED

    def test_a_plain_zip_renamed_to_docx_is_rejected(self, gate: SecurityGate):
        attachment = attachment_for(ZIP_BYTES, "contract.docx", DOCX_MIME)

        checked = gate.screen(attachment, ZIP_BYTES)

        assert checked.status is AttachmentStatus.REJECTED

    def test_an_empty_file_is_rejected(self, gate: SecurityGate):
        attachment = attachment_for(b"", "empty.pdf")

        assert gate.screen(attachment, b"").status is AttachmentStatus.REJECTED


class TestMacroDocuments:
    def test_a_macro_enabled_word_document_is_rejected(self, gate: SecurityGate):
        payload = make_docx(macro_enabled=True)
        attachment = attachment_for(payload, "invoice.docm", DOCX_MIME)

        checked = gate.screen(attachment, payload)

        assert checked.status is AttachmentStatus.REJECTED
        assert "macro" in checked.reason.lower()

    def test_a_docx_extension_hiding_macros_is_still_rejected(self, gate: SecurityGate):
        payload = make_docx(macro_enabled=True)
        attachment = attachment_for(payload, "invoice.docx", DOCX_MIME)

        assert gate.screen(attachment, payload).status is AttachmentStatus.REJECTED


class TestDisallowedTypes:
    @pytest.mark.parametrize(
        "payload, filename, mime",
        [
            (b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, "photo.png", "image/png"),
            (b"GIF89a" + b"\x00" * 32, "anim.gif", "image/gif"),
            (b"col1,col2\n1,2\n", "data.csv", "text/csv"),
        ],
    )
    def test_types_outside_the_allowlist_are_refused(
        self, gate: SecurityGate, payload: bytes, filename: str, mime: str
    ):
        checked = gate.screen(attachment_for(payload, filename, mime), payload)

        assert checked.status is AttachmentStatus.REJECTED
        assert "not supported" in checked.reason.lower()


class TestSizeLimits:
    def test_an_oversized_attachment_is_rejected(self):
        gate = SecurityGate(Settings(max_attachment_bytes=100, max_total_attachment_bytes=100))
        payload = PDF_BYTES + b"\x00" * 200

        checked = gate.screen(attachment_for(payload, "big.pdf"), payload)

        assert checked.status is AttachmentStatus.REJECTED
        assert "too large" in checked.reason.lower()

    def test_the_thread_budget_stops_accepting_after_it_is_used_up(self):
        gate = SecurityGate(Settings(max_attachment_bytes=100, max_total_attachment_bytes=120))
        payload = PDF_BYTES + b"\x00" * 40

        first = gate.screen(attachment_for(payload, "a.pdf"), payload)
        second = gate.screen(attachment_for(payload, "b.pdf"), payload)

        assert first.status is AttachmentStatus.PENDING
        assert second.status is AttachmentStatus.REJECTED
        assert "budget" in second.reason.lower()

    def test_rejected_attachments_do_not_consume_the_budget(self):
        gate = SecurityGate(Settings(max_attachment_bytes=100, max_total_attachment_bytes=120))
        rejected_payload = MACHO_BYTES

        gate.screen(attachment_for(rejected_payload, "bad.pdf"), rejected_payload)
        accepted = gate.screen(attachment_for(PDF_BYTES, "good.pdf"), PDF_BYTES)

        assert accepted.status is AttachmentStatus.PENDING

    def test_too_many_attachments_are_refused(self):
        gate = SecurityGate(Settings(max_attachment_count=1))

        gate.screen(attachment_for(PDF_BYTES, "a.pdf"), PDF_BYTES)
        second = gate.screen(attachment_for(PDF_BYTES, "b.pdf"), PDF_BYTES)

        assert second.status is AttachmentStatus.REJECTED
        assert "too many" in second.reason.lower()

    def test_a_docx_with_traversal_entries_is_rejected(self, gate: SecurityGate):
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", "<document/>")
            archive.writestr("../../etc/passwd", "root")
        payload = buffer.getvalue()

        checked = gate.screen(attachment_for(payload, "sneaky.docx", DOCX_MIME), payload)

        assert checked.status is AttachmentStatus.REJECTED
        assert "unsafe internal paths" in checked.reason

    def test_a_zip_bomb_style_docx_is_rejected_on_uncompressed_size(self, gate: SecurityGate):
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", "A" * (60 * 1024 * 1024))
        payload = buffer.getvalue()

        checked = gate.screen(attachment_for(payload, "bomb.docx", DOCX_MIME), payload)

        assert checked.status is AttachmentStatus.REJECTED
        assert "expands" in checked.reason.lower()


class TestFilenameSafety:
    @pytest.mark.parametrize(
        "hostile_name",
        [
            "../../../etc/passwd",
            "..\\..\\windows\\system32\\config",
            "/etc/shadow",
            "sub/dir/file.pdf",
            "file\x00.pdf",
            "." * 300 + ".pdf",
        ],
    )
    def test_hostile_filenames_cannot_escape_their_directory(self, hostile_name: str):
        stored = safe_storage_name(hostile_name, "a" * 64)

        assert "/" not in stored
        assert "\\" not in stored
        assert "\x00" not in stored
        assert not stored.startswith(".")
        assert len(stored) <= 128

    def test_the_stored_name_is_derived_from_the_digest_not_the_filename(self):
        assert safe_storage_name("invoice.pdf", "b" * 64).startswith("b" * 64)

    def test_the_original_filename_is_preserved_for_display_only(self, gate: SecurityGate):
        attachment = attachment_for(PDF_BYTES, "../../evil.pdf")

        assert gate.screen(attachment, PDF_BYTES).filename == "../../evil.pdf"


class TestIntegrity:
    def test_bytes_that_do_not_match_the_recorded_digest_are_rejected(self, gate: SecurityGate):
        attachment = attachment_for(PDF_BYTES, "invoice.pdf")
        tampered = PDF_BYTES + b"extra"

        checked = gate.screen(attachment, tampered)

        assert checked.status is AttachmentStatus.REJECTED
        assert "digest" in checked.reason.lower()


class TestScreenAll:
    def test_screening_a_batch_returns_verdicts_in_order(self, gate: SecurityGate):
        good = attachment_for(PDF_BYTES, "good.pdf")
        bad = attachment_for(MACHO_BYTES, "bad.pdf")
        blobs = {good.sha256: PDF_BYTES, bad.sha256: MACHO_BYTES}

        results = gate.screen_all([good, bad], blobs)

        assert [item.status for item in results] == [
            AttachmentStatus.PENDING,
            AttachmentStatus.REJECTED,
        ]

    def test_an_attachment_with_missing_bytes_is_rejected_not_crashed(self, gate: SecurityGate):
        orphan = attachment_for(PDF_BYTES, "ghost.pdf")

        results = gate.screen_all([orphan], {})

        assert results[0].status is AttachmentStatus.REJECTED

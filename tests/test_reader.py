"""The reader stage turns gate-approved attachments into usable text."""

import hashlib

import pytest

from app.config import DOCX_MIME, PDF_MIME, Settings
from app.models import Attachment, AttachmentStatus
from app.pipeline.reader import read_attachments
from tests.documents import build_docx, build_pdf


def attachment_for(payload: bytes, filename: str, mime: str = PDF_MIME) -> Attachment:
    return Attachment(
        filename=filename,
        declared_mime=mime,
        detected_mime=mime,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )


@pytest.fixture
def settings() -> Settings:
    return Settings(extraction_timeout_seconds=15.0)


class TestReading:
    def test_a_readable_pdf_becomes_extracted(self, settings: Settings):
        payload = build_pdf(["Invoice INV-2026-014"])
        attachment = attachment_for(payload, "invoice.pdf")

        result = read_attachments([attachment], {attachment.sha256: payload}, settings=settings)

        assert result[0].status is AttachmentStatus.EXTRACTED
        assert "INV-2026-014" in result[0].extracted_text
        assert result[0].has_text

    def test_a_readable_docx_becomes_extracted(self, settings: Settings):
        payload = build_docx(["Please countersign by Friday."])
        attachment = attachment_for(payload, "contract.docx", DOCX_MIME)

        result = read_attachments([attachment], {attachment.sha256: payload}, settings=settings)

        assert result[0].status is AttachmentStatus.EXTRACTED
        assert "countersign" in result[0].extracted_text


class TestSkipping:
    def test_rejected_attachments_are_never_opened(self, settings: Settings):
        payload = build_pdf(["never read me"])
        attachment = attachment_for(payload, "blocked.pdf").model_copy(
            update={"status": AttachmentStatus.REJECTED, "reason": "blocked by the gate"}
        )

        result = read_attachments([attachment], {attachment.sha256: payload}, settings=settings)

        assert result[0].status is AttachmentStatus.REJECTED
        assert result[0].extracted_text is None
        assert result[0].reason == "blocked by the gate"

    def test_missing_bytes_are_reported_as_unreadable(self, settings: Settings):
        attachment = attachment_for(build_pdf(["x"]), "ghost.pdf")

        result = read_attachments([attachment], {}, settings=settings)

        assert result[0].status is AttachmentStatus.UNREADABLE

    def test_an_unreadable_document_keeps_the_reason(self, settings: Settings):
        payload = b"%PDF-1.4 hopelessly broken"
        attachment = attachment_for(payload, "broken.pdf")

        result = read_attachments([attachment], {attachment.sha256: payload}, settings=settings)

        assert result[0].status is AttachmentStatus.UNREADABLE
        assert result[0].reason
        assert result[0].extracted_text is None

    def test_a_document_with_no_text_is_marked_unreadable(self, settings: Settings):
        payload = build_pdf([])
        attachment = attachment_for(payload, "blank.pdf")

        result = read_attachments([attachment], {attachment.sha256: payload}, settings=settings)

        assert result[0].status is AttachmentStatus.UNREADABLE
        assert "no readable text" in result[0].reason


class TestBatch:
    def test_one_failure_does_not_stop_the_others(self, settings: Settings):
        good_payload = build_pdf(["good content"])
        bad_payload = b"%PDF-1.4 broken"
        good = attachment_for(good_payload, "good.pdf")
        bad = attachment_for(bad_payload, "bad.pdf")
        blobs = {good.sha256: good_payload, bad.sha256: bad_payload}

        result = read_attachments([bad, good], blobs, settings=settings)

        assert result[0].status is AttachmentStatus.UNREADABLE
        assert result[1].status is AttachmentStatus.EXTRACTED

    def test_input_order_is_preserved(self, settings: Settings):
        payloads = [build_pdf([f"document {index}"]) for index in range(3)]
        attachments = [
            attachment_for(payload, f"doc{index}.pdf") for index, payload in enumerate(payloads)
        ]
        blobs = {item.sha256: payload for item, payload in zip(attachments, payloads, strict=True)}

        result = read_attachments(attachments, blobs, settings=settings)

        assert [item.filename for item in result] == ["doc0.pdf", "doc1.pdf", "doc2.pdf"]

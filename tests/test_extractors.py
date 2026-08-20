"""Pure text extraction from supported document types.

These functions run inside the sandbox subprocess, so they are allowed to fail
- but only by raising ExtractionError, never by returning something surprising.
"""

import pytest

from app.config import DOCX_MIME, PDF_MIME
from app.pipeline.extractors import ExtractionError, extract_text, supported_mime_types
from tests.documents import build_docx, build_encrypted_pdf, build_pdf


class TestPdf:
    def test_text_is_extracted(self):
        payload = build_pdf(["Invoice INV-2026-014", "Total due: 4,500 EUR"])

        text = extract_text(PDF_MIME, payload, max_chars=10_000)

        assert "Invoice INV-2026-014" in text
        assert "4,500 EUR" in text

    def test_every_page_is_included(self):
        payload = build_pdf(["Total due"], pages=3)

        text = extract_text(PDF_MIME, payload, max_chars=10_000)

        assert "page 1" in text
        assert "page 3" in text

    def test_an_encrypted_pdf_is_refused(self):
        with pytest.raises(ExtractionError, match="password"):
            extract_text(PDF_MIME, build_encrypted_pdf(), max_chars=10_000)

    def test_corrupt_bytes_raise_a_domain_error(self):
        with pytest.raises(ExtractionError):
            extract_text(PDF_MIME, b"%PDF-1.4 truncated and broken", max_chars=10_000)


class TestDocx:
    def test_paragraphs_are_extracted(self):
        payload = build_docx(["Dear Bob,", "Please countersign by Friday."])

        text = extract_text(DOCX_MIME, payload, max_chars=10_000)

        assert "Dear Bob," in text
        assert "Please countersign by Friday." in text

    def test_table_contents_are_extracted(self):
        payload = build_docx(["Summary"], table_rows=[["Item", "Cost"], ["Licence", "2,000"]])

        text = extract_text(DOCX_MIME, payload, max_chars=10_000)

        assert "Licence" in text
        assert "2,000" in text

    def test_empty_paragraphs_do_not_pad_the_output(self):
        payload = build_docx(["First", "", "", "Second"])

        text = extract_text(DOCX_MIME, payload, max_chars=10_000)

        assert "\n\n\n" not in text

    def test_corrupt_bytes_raise_a_domain_error(self):
        with pytest.raises(ExtractionError):
            extract_text(DOCX_MIME, b"PK\x03\x04 not really a document", max_chars=10_000)


class TestLimits:
    def test_long_text_is_truncated_and_marked(self):
        payload = build_pdf(["A very long line of billing detail"] * 40)

        text = extract_text(PDF_MIME, payload, max_chars=100)

        assert len(text) <= 200
        assert text.endswith("[truncated]")

    def test_text_within_the_limit_is_not_marked(self):
        payload = build_pdf(["short"])

        assert "[truncated]" not in extract_text(PDF_MIME, payload, max_chars=10_000)


class TestDispatch:
    def test_unsupported_types_are_refused(self):
        with pytest.raises(ExtractionError, match="not supported"):
            extract_text("image/png", b"\x89PNG", max_chars=10_000)

    def test_empty_payload_is_refused(self):
        with pytest.raises(ExtractionError):
            extract_text(PDF_MIME, b"", max_chars=10_000)

    def test_supported_types_match_the_configured_allowlist(self):
        from app.config import Settings

        assert supported_mime_types() == Settings().allowed_mime_types

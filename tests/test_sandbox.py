"""Document parsers run in a separate process so hostile files cannot hurt us."""

import sys
from pathlib import Path

import pytest

from app.config import DOCX_MIME, PDF_MIME, Settings
from app.pipeline.sandbox import ExtractionOutcome, build_child_environment, run_extraction
from tests.documents import build_docx, build_pdf

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the sandbox relies on POSIX resource limits"
)


@pytest.fixture
def settings() -> Settings:
    return Settings(extraction_timeout_seconds=15.0, max_extracted_chars=5_000)


class TestSuccess:
    def test_pdf_text_comes_back_from_the_child_process(self, settings: Settings):
        payload = build_pdf(["Invoice INV-2026-014"])

        outcome = run_extraction(PDF_MIME, payload, settings=settings)

        assert outcome.succeeded
        assert "INV-2026-014" in outcome.text
        assert outcome.error is None

    def test_docx_text_comes_back_from_the_child_process(self, settings: Settings):
        payload = build_docx(["Please countersign by Friday."])

        outcome = run_extraction(DOCX_MIME, payload, settings=settings)

        assert outcome.succeeded
        assert "countersign" in outcome.text

    def test_the_child_respects_the_character_limit(self):
        settings = Settings(max_extracted_chars=50)
        payload = build_pdf(["A long line of billing detail"] * 30)

        outcome = run_extraction(PDF_MIME, payload, settings=settings)

        assert outcome.succeeded
        assert len(outcome.text) < 200


class TestFailureIsContained:
    def test_a_corrupt_document_fails_without_raising(self, settings: Settings):
        outcome = run_extraction(PDF_MIME, b"%PDF-1.4 hopelessly broken", settings=settings)

        assert not outcome.succeeded
        assert outcome.text is None
        assert outcome.error

    def test_a_hanging_parser_is_killed_and_reported(self):
        settings = Settings(extraction_timeout_seconds=0.5)
        hanging_child = [sys.executable, "-c", "import time; time.sleep(30)"]

        outcome = run_extraction(
            PDF_MIME, build_pdf(["slow"]), settings=settings, command=hanging_child
        )

        assert not outcome.succeeded
        assert "too long" in outcome.error.lower()

    def test_an_unsupported_type_is_reported_not_raised(self, settings: Settings):
        outcome = run_extraction("image/png", b"\x89PNG", settings=settings)

        assert not outcome.succeeded
        assert "not supported" in outcome.error.lower()

    def test_a_crashing_child_is_reported_not_raised(self, settings: Settings):
        crashing_child = [sys.executable, "-c", "raise SystemExit(3)"]

        outcome = run_extraction(
            PDF_MIME, build_pdf(["x"]), settings=settings, command=crashing_child
        )

        assert not outcome.succeeded
        assert "stopped unexpectedly" in outcome.error

    def test_a_child_returning_nonsense_is_reported_not_raised(self, settings: Settings):
        babbling_child = [sys.executable, "-c", "print('not json at all')"]

        outcome = run_extraction(
            PDF_MIME, build_pdf(["x"]), settings=settings, command=babbling_child
        )

        assert not outcome.succeeded
        assert "unreadable response" in outcome.error


class TestIsolation:
    def test_the_child_cannot_see_the_parent_environment(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SECRET_API_KEY", "super-secret-value")

        assert "SECRET_API_KEY" not in build_child_environment()

    def test_the_child_environment_is_a_short_allowlist(self):
        assert set(build_child_environment()) == {
            "PATH",
            "PYTHONPATH",
            "PYTHONHASHSEED",
            "LC_ALL",
            "LANG",
        }

    def test_the_child_cannot_write_data_to_disk(self, settings: Settings, tmp_path: Path):
        target = tmp_path / "leaked.txt"
        writing_child = [
            sys.executable,
            "-c",
            "import sys; import app.pipeline._extract_worker as worker;"
            "worker._apply_resource_limits(5);"
            "open(sys.argv[1], 'w').write('x' * 4096)",
            str(target),
        ]

        outcome = run_extraction(PDF_MIME, b"ignored", settings=settings, command=writing_child)

        assert not outcome.succeeded, "a child that tries to write a file must not succeed"
        assert (
            not target.exists() or target.read_bytes() == b""
        ), "the file-size limit must stop any data reaching the disk"


class TestOutcome:
    def test_succeeded_requires_text(self):
        assert ExtractionOutcome(text="hello").succeeded
        assert not ExtractionOutcome(error="boom").succeeded

"""The demo site must stay honest and stay in step with the real interface."""

import json
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = PROJECT_ROOT / "demo"
STATIC_DIR = PROJECT_ROOT / "app" / "static"
RESULTS = DEMO_DIR / "results.json"

pytestmark = pytest.mark.skipif(
    not RESULTS.is_file(), reason="demo data not captured; run python -m demo.capture"
)


class TestSharedAssets:
    def test_the_demo_copies_match_their_sources(self):
        from demo.sync_assets import out_of_date

        stale = out_of_date()

        assert not stale, (
            f"the demo's copies of {', '.join(stale)} have drifted from app/static; "
            "run: python -m demo.sync_assets"
        )

    def test_the_demo_draws_results_with_the_application_renderer(self):
        html = (DEMO_DIR / "index.html").read_text()

        assert 'src="render.js"' in html, "the demo must use the shared renderer, not its own"

    def test_the_demo_uses_the_same_element_ids_the_renderer_writes_to(self):
        renderer = (STATIC_DIR / "render.js").read_text()
        demo_html = (DEMO_DIR / "index.html").read_text()

        for element_id in set(re.findall(r'el\("([a-z-]+)"\)', renderer)):
            assert (
                f'id="{element_id}"' in demo_html
            ), f"the renderer writes to #{element_id}, which the demo page does not have"


class TestRecordedData:
    @pytest.fixture
    def data(self) -> dict:
        return json.loads(RESULTS.read_text())

    def test_every_evaluation_thread_was_captured(self, data: dict):
        from demo.capture import LABELS

        captured = {thread["id"] for thread in data["threads"]}
        expected = {name.removesuffix(".eml") for name in LABELS}

        assert captured == expected

    def test_each_result_validates_against_the_real_schema(self, data: dict):
        from app.models import AnalysisResult

        for thread in data["threads"]:
            AnalysisResult.model_validate(thread["result"])

    def test_the_data_records_which_model_produced_it(self, data: dict):
        assert data["model"]
        assert data["captured_at"]

    def test_the_attack_threads_are_flagged_in_the_recorded_data(self, data: dict):
        by_id = {thread["id"]: thread for thread in data["threads"]}

        for name in ("07_prompt_injection", "08_injection_in_attachment"):
            flags = by_id[name]["result"]["security_flags"]
            assert any(flag["kind"] == "prompt_injection" for flag in flags)

    def test_a_detected_attack_shows_the_safe_next_step(self, data: dict):
        by_id = {thread["id"]: thread for thread in data["threads"]}
        summary = by_id["07_prompt_injection"]["result"]["summary"]

        assert "suspicious" in summary["suggested_next_step"].lower()
        assert summary["action_items"] == []

    def test_the_disguised_executable_is_recorded_as_rejected(self, data: dict):
        by_id = {thread["id"]: thread for thread in data["threads"]}
        attachments = by_id["09_disguised_executable"]["result"]["attachments"]

        assert attachments[0]["status"] == "rejected"

    def test_the_recorded_source_emails_are_the_real_fixtures(self, data: dict):
        for thread in data["threads"]:
            fixture = PROJECT_ROOT / "evals" / "fixtures" / f"{thread['id']}.eml"
            assert thread["source"] == fixture.read_text(encoding="utf-8", errors="replace")


class TestHonesty:
    def test_the_page_says_plainly_that_it_is_recorded(self):
        html = (DEMO_DIR / "index.html").read_text().lower()

        assert "recorded demo, not a live application" in html

    def test_the_page_links_to_the_capture_script_and_raw_data(self):
        html = (DEMO_DIR / "index.html").read_text()

        assert "demo/capture.py" in html
        assert "demo/results.json" in html


class TestStillLocalOnly:
    def test_the_demo_loads_no_remote_asset(self):
        html = (DEMO_DIR / "index.html").read_text()

        for reference in re.findall(r'(?:src|href)="([^"]*)"', html):
            if reference.startswith("https://github.com/"):
                continue  # links out are fine; loading from elsewhere is not
            assert not reference.startswith(
                ("http://", "https://", "//")
            ), f"{reference} is loaded from a remote host"

    def test_the_demo_scripts_make_no_remote_requests(self):
        for name in ("demo.js", "render.js"):
            source = (DEMO_DIR / name).read_text()
            for url in re.findall(r'fetch\(\s*["\'`]([^"\'`]+)', source):
                assert not url.startswith(
                    ("http://", "https://", "//")
                ), f"{name} fetches {url} from a remote host"

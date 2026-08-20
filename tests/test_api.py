"""The HTTP layer: upload limits, error handling and history endpoints."""

from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import Summary
from app.pipeline.summarizer import ModelResponseError, ModelUnavailableError, SummaryResult
from app.store import HistoryStore
from tests.documents import build_pdf
from tests.factories import build_eml

GOOD_SUMMARY = Summary.model_validate(
    {
        "summary": "Alice asked for the report.",
        "key_points": ["The report is needed"],
        "action_items": [{"task": "Send the report", "owner": "me", "due": None}],
        "suggested_next_step": "Send the report to Alice.",
        "urgency": "medium",
        "waiting_on": "me",
    }
)


class FakeSummarizer:
    def __init__(self, result=GOOD_SUMMARY):
        self._result = result
        self.canary = "session-test"

    def summarize(self, thread, facts, warnings=None) -> SummaryResult:
        if isinstance(self._result, Exception):
            raise self._result
        return SummaryResult(summary=self._result, model_used="fake-model")


@pytest.fixture
def store(tmp_path: Path) -> HistoryStore:
    return HistoryStore(tmp_path / "history.db")


def client_for(
    store: HistoryStore, summarizer=None, settings: Settings | None = None
) -> TestClient:
    app = create_app(
        settings=settings or Settings(),
        store=store,
        summarizer_factory=lambda _settings: summarizer or FakeSummarizer(),
    )
    return TestClient(app)


def upload(name: str = "thread.eml", **kwargs) -> dict:
    return {"files": (name, build_eml(**kwargs), "message/rfc822")}


class TestAnalyse:
    def test_an_uploaded_thread_is_analysed(self, store: HistoryStore):
        response = client_for(store).post("/api/analyse", files=upload(subject="Report"))

        assert response.status_code == 200
        body = response.json()
        assert body["thread_subject"] == "Report"
        assert body["summary"]["suggested_next_step"] == "Send the report to Alice."

    def test_the_analysis_is_saved_to_history(self, store: HistoryStore):
        client_for(store).post("/api/analyse", files=upload())

        assert len(store.list_recent()) == 1

    def test_the_response_carries_its_history_identifier(self, store: HistoryStore):
        response = client_for(store).post("/api/analyse", files=upload())

        assert store.get(response.json()["id"]) is not None

    def test_several_files_are_analysed_as_one_thread(self, store: HistoryStore):
        files = [
            ("files", ("a.eml", build_eml(body="First message."), "message/rfc822")),
            ("files", ("b.eml", build_eml(body="Second message."), "message/rfc822")),
        ]

        response = client_for(store).post("/api/analyse", files=files)

        assert response.status_code == 200

    def test_an_attachment_is_reported_in_the_response(self, store: HistoryStore):
        raw = build_eml(attachments=(("invoice.pdf", "application/pdf", build_pdf(["Total 500"])),))
        files = {"files": ("thread.eml", raw, "message/rfc822")}

        response = client_for(store).post("/api/analyse", files=files)

        assert response.json()["attachments"][0]["status"] == "extracted"


class TestRejectedUploads:
    def test_uploading_nothing_is_refused(self, store: HistoryStore):
        response = client_for(store).post("/api/analyse", files={})

        assert response.status_code == 422

    def test_a_file_that_is_not_an_email_is_refused_clearly(self, store: HistoryStore):
        files = {"files": ("notes.txt", b"just some notes", "text/plain")}

        response = client_for(store).post("/api/analyse", files=files)

        assert response.status_code == 400
        assert "email" in response.json()["detail"].lower()

    def test_an_upload_over_the_size_limit_is_refused(self, store: HistoryStore):
        settings = Settings(max_upload_bytes=1024)
        files = {"files": ("big.eml", b"x" * 5000, "message/rfc822")}

        response = client_for(store, settings=settings).post("/api/analyse", files=files)

        assert response.status_code == 413
        assert "too large" in response.json()["detail"].lower()

    def test_too_many_files_are_refused(self, store: HistoryStore):
        files = [("files", (f"{i}.eml", build_eml(), "message/rfc822")) for i in range(30)]

        response = client_for(store).post("/api/analyse", files=files)

        assert response.status_code == 413


class TestModelFailures:
    def test_a_stopped_model_gives_a_clear_message_and_the_fix(self, store: HistoryStore):
        summarizer = FakeSummarizer(ModelUnavailableError("could not reach Ollama; start it"))

        response = client_for(store, summarizer).post("/api/analyse", files=upload())

        assert response.status_code == 503
        assert "ollama" in response.json()["detail"].lower()

    def test_an_unusable_answer_still_returns_a_degraded_result(self, store: HistoryStore):
        summarizer = FakeSummarizer(ModelResponseError("nothing usable"))

        response = client_for(store, summarizer).post("/api/analyse", files=upload())

        assert response.status_code == 200
        assert response.json()["degraded"] is True


class TestHistory:
    def test_history_lists_saved_analyses(self, store: HistoryStore):
        client = client_for(store)
        client.post("/api/analyse", files=upload(subject="First"))
        client.post("/api/analyse", files=upload(subject="Second"))

        response = client.get("/api/history")

        assert response.status_code == 200
        assert len(response.json()["entries"]) == 2

    def test_a_single_entry_can_be_fetched(self, store: HistoryStore):
        entry_id = client_for(store).post("/api/analyse", files=upload()).json()["id"]

        response = client_for(store).get(f"/api/history/{entry_id}")

        assert response.status_code == 200
        assert response.json()["result"]["thread_subject"]

    def test_a_missing_entry_gives_a_not_found(self, store: HistoryStore):
        assert client_for(store).get("/api/history/404").status_code == 404

    def test_one_entry_can_be_deleted(self, store: HistoryStore):
        client = client_for(store)
        entry_id = client.post("/api/analyse", files=upload()).json()["id"]

        response = client.delete(f"/api/history/{entry_id}")

        assert response.status_code == 204
        assert store.get(entry_id) is None

    def test_everything_can_be_deleted(self, store: HistoryStore):
        client = client_for(store)
        client.post("/api/analyse", files=upload())

        response = client.delete("/api/history")

        assert response.status_code == 200
        assert response.json()["deleted"] == 1
        assert store.list_recent() == []


class TestHealth:
    def test_health_reports_the_model_state(self, store: HistoryStore):
        response = client_for(store).get("/api/health")

        assert response.status_code == 200
        assert "model_available" in response.json()

    def test_health_names_the_configured_model(self, store: HistoryStore):
        assert client_for(store).get("/api/health").json()["model"] == "qwen3:4b"


class TestWebPage:
    def test_the_page_is_served(self, store: HistoryStore):
        response = client_for(store).get("/")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_the_page_does_not_reference_remote_resources(self, store: HistoryStore):
        body = client_for(store).get("/").text

        assert "http://" not in body
        assert "https://" not in body, "the interface must work with the network switched off"


class TestSecurityHeaders:
    def test_a_restrictive_content_security_policy_is_sent(self, store: HistoryStore):
        response = client_for(store).get("/")

        policy = response.headers["content-security-policy"]
        assert "default-src 'self'" in policy
        assert "connect-src 'self'" in policy

    def test_content_type_sniffing_is_disabled(self, store: HistoryStore):
        response = client_for(store).get("/")

        assert response.headers["x-content-type-options"] == "nosniff"

    def test_the_page_cannot_be_framed(self, store: HistoryStore):
        assert client_for(store).get("/").headers["x-frame-options"] == "DENY"

    def test_referrers_are_not_leaked(self, store: HistoryStore):
        assert client_for(store).get("/").headers["referrer-policy"] == "no-referrer"


class TestApiDates:
    def test_history_timestamps_are_parseable(self, store: HistoryStore):
        client = client_for(store)
        client.post("/api/analyse", files=upload())

        entry = client.get("/api/history").json()["entries"][0]

        assert datetime.fromisoformat(entry["created_at"]).tzinfo is not None

"""History is stored locally, masked, and easy to delete."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.models import AnalysisResult, SecurityFlag, Summary, ThreadFacts
from app.store import HistoryStore

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def result_for(subject: str = "Q3 budget", next_step: str = "Send the quote.") -> AnalysisResult:
    return AnalysisResult(
        thread_subject=subject,
        summary=Summary(
            summary="A short summary.",
            key_points=["one point"],
            action_items=[],
            suggested_next_step=next_step,
            urgency="medium",
            waiting_on="me",
        ),
        facts=ThreadFacts(participants=["alice@example.com"], message_count=2),
        model_used="qwen3:4b",
    )


@pytest.fixture
def store(tmp_path: Path) -> HistoryStore:
    return HistoryStore(tmp_path / "history.db")


class TestSaving:
    def test_a_result_can_be_saved_and_read_back(self, store: HistoryStore):
        entry_id = store.save(result_for(), created_at=NOW)

        entry = store.get(entry_id)

        assert entry is not None
        assert entry.thread_subject == "Q3 budget"
        assert entry.result.summary.suggested_next_step == "Send the quote."

    def test_saving_returns_a_new_identifier_each_time(self, store: HistoryStore):
        first = store.save(result_for(), created_at=NOW)
        second = store.save(result_for(), created_at=NOW)

        assert first != second

    def test_the_creation_time_is_recorded(self, store: HistoryStore):
        entry_id = store.save(result_for(), created_at=NOW)

        assert store.get(entry_id).created_at == NOW

    def test_security_flags_survive_the_round_trip(self, store: HistoryStore):
        result = result_for().model_copy(
            update={"security_flags": [SecurityFlag(kind="prompt_injection", detail="found it")]}
        )

        entry_id = store.save(result, created_at=NOW)

        assert store.get(entry_id).result.security_flags[0].kind == "prompt_injection"


class TestListing:
    def test_entries_are_listed_newest_first(self, store: HistoryStore):
        store.save(result_for("older"), created_at=datetime(2026, 8, 18, tzinfo=timezone.utc))
        store.save(result_for("newer"), created_at=datetime(2026, 8, 19, tzinfo=timezone.utc))

        entries = store.list_recent()

        assert [entry.thread_subject for entry in entries] == ["newer", "older"]

    def test_the_listing_is_capped(self, store: HistoryStore):
        for index in range(5):
            store.save(result_for(f"thread {index}"), created_at=NOW)

        assert len(store.list_recent(limit=3)) == 3

    def test_an_empty_store_lists_nothing(self, store: HistoryStore):
        assert store.list_recent() == []

    def test_the_listing_carries_enough_to_render_a_row(self, store: HistoryStore):
        store.save(result_for(), created_at=NOW)

        entry = store.list_recent()[0]

        assert entry.thread_subject
        assert entry.result.summary.urgency
        assert entry.created_at


class TestDeleting:
    def test_a_single_entry_can_be_deleted(self, store: HistoryStore):
        entry_id = store.save(result_for(), created_at=NOW)

        assert store.delete(entry_id) is True
        assert store.get(entry_id) is None

    def test_deleting_something_absent_reports_it(self, store: HistoryStore):
        assert store.delete(999) is False

    def test_everything_can_be_deleted_at_once(self, store: HistoryStore):
        store.save(result_for(), created_at=NOW)
        store.save(result_for(), created_at=NOW)

        removed = store.purge()

        assert removed == 2
        assert store.list_recent() == []


class TestStorageSafety:
    def test_the_database_file_is_created_with_its_directory(self, tmp_path: Path):
        path = tmp_path / "nested" / "deeper" / "history.db"

        HistoryStore(path).save(result_for(), created_at=NOW)

        assert path.exists()

    def test_the_database_is_not_world_readable(self, tmp_path: Path):
        path = tmp_path / "history.db"
        HistoryStore(path).save(result_for(), created_at=NOW)

        mode = path.stat().st_mode & 0o777

        assert mode & 0o077 == 0, "history may contain private content; keep it owner-only"

    def test_opening_an_existing_database_again_works(self, tmp_path: Path):
        path = tmp_path / "history.db"
        entry_id = HistoryStore(path).save(result_for(), created_at=NOW)

        assert HistoryStore(path).get(entry_id) is not None

    def test_a_corrupt_row_does_not_break_the_listing(self, store: HistoryStore):
        store.save(result_for(), created_at=NOW)
        with store.connect() as connection:
            connection.execute(
                "INSERT INTO analyses (created_at, subject, payload) VALUES (?,?,?)",
                (NOW.isoformat(), "broken", "{not json"),
            )

        entries = store.list_recent()

        assert len(entries) == 1, "unreadable rows are skipped rather than crashing the page"

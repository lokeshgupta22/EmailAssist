"""History is stored locally, masked, and easy to delete."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.models import ActionItem, AnalysisResult, SecurityFlag, Summary, ThreadFacts
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


def result_with_tasks(*items: tuple[str, str, str | None]) -> AnalysisResult:
    """A result carrying the given (task, owner, due) action items."""
    return AnalysisResult(
        thread_subject="Q3 budget",
        summary=Summary(
            summary="A short summary.",
            key_points=[],
            action_items=[
                ActionItem(task=task, owner=owner, due=due) for task, owner, due in items
            ],
            suggested_next_step="Send the quote.",
            urgency="high",
            waiting_on="me",
        ),
        facts=ThreadFacts(),
        model_used="qwen3:4b",
    )


class TestTasks:
    def test_action_items_are_listed_with_the_analysis_they_came_from(self, store: HistoryStore):
        entry_id = store.save(
            result_with_tasks(("Send the report", "me", "2026-08-25"), ("Chase Bob", "them", None)),
            created_at=NOW,
        )

        tasks = store.list_tasks()

        assert [(t.index, t.task, t.owner, t.due) for t in tasks] == [
            (0, "Send the report", "me", "2026-08-25"),
            (1, "Chase Bob", "them", None),
        ]
        assert all(task.analysis_id == entry_id for task in tasks)
        assert all(task.thread_subject == "Q3 budget" for task in tasks)

    def test_an_analysis_without_action_items_contributes_none(self, store: HistoryStore):
        store.save(result_for(), created_at=NOW)

        assert store.list_tasks() == []

    def test_a_task_starts_untick_and_can_be_ticked(self, store: HistoryStore):
        entry_id = store.save(result_with_tasks(("Send the report", "me", None)), created_at=NOW)
        assert store.list_tasks()[0].done is False

        store.set_task_done(entry_id, 0, True, at=NOW)

        assert store.list_tasks()[0].done is True
        assert store.done_indexes(entry_id) == {0}

    def test_ticking_twice_does_not_fail(self, store: HistoryStore):
        entry_id = store.save(result_with_tasks(("Send the report", "me", None)), created_at=NOW)

        store.set_task_done(entry_id, 0, True, at=NOW)
        store.set_task_done(entry_id, 0, True, at=NOW)

        assert store.done_indexes(entry_id) == {0}

    def test_a_task_can_be_unticked(self, store: HistoryStore):
        entry_id = store.save(result_with_tasks(("Send the report", "me", None)), created_at=NOW)
        store.set_task_done(entry_id, 0, True, at=NOW)

        store.set_task_done(entry_id, 0, False, at=NOW)

        assert store.done_indexes(entry_id) == set()

    def test_ticks_are_kept_apart_per_analysis(self, store: HistoryStore):
        first = store.save(result_with_tasks(("One", "me", None)), created_at=NOW)
        second = store.save(result_with_tasks(("Two", "me", None)), created_at=NOW)

        store.set_task_done(first, 0, True, at=NOW)

        assert store.done_indexes(first) == {0}
        assert store.done_indexes(second) == set()

    def test_counting_action_items_reports_absence_apart_from_emptiness(self, store: HistoryStore):
        with_items = store.save(result_with_tasks(("One", "me", None)), created_at=NOW)
        without = store.save(result_for(), created_at=NOW)

        assert store.count_action_items(with_items) == 1
        assert store.count_action_items(without) == 0
        assert store.count_action_items(9999) is None, "a missing analysis is not an empty one"


class TestTicksAreDeletedWithTheirAnalysis:
    """Deleting must stay complete: a tick may not outlive its analysis."""

    def test_deleting_an_analysis_removes_its_ticks(self, store: HistoryStore):
        entry_id = store.save(result_with_tasks(("Send the report", "me", None)), created_at=NOW)
        store.set_task_done(entry_id, 0, True, at=NOW)

        store.delete(entry_id)

        with store.connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM task_state").fetchone()[0] == 0

    def test_purging_removes_every_tick(self, store: HistoryStore):
        first = store.save(result_with_tasks(("One", "me", None)), created_at=NOW)
        second = store.save(result_with_tasks(("Two", "me", None)), created_at=NOW)
        store.set_task_done(first, 0, True, at=NOW)
        store.set_task_done(second, 0, True, at=NOW)

        store.purge()

        with store.connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM task_state").fetchone()[0] == 0
        assert store.list_tasks() == []

    def test_a_new_analysis_does_not_inherit_a_deleted_one_s_ticks(self, store: HistoryStore):
        first = store.save(result_with_tasks(("Send the report", "me", None)), created_at=NOW)
        store.set_task_done(first, 0, True, at=NOW)
        store.delete(first)

        second = store.save(result_with_tasks(("A different job", "me", None)), created_at=NOW)

        assert second != first, "ids are AUTOINCREMENT, so they are never reused"
        assert store.list_tasks()[0].done is False


class TestExistingDatabasesGainTheTable:
    def test_a_database_written_before_tasks_existed_still_opens(self, tmp_path: Path):
        import sqlite3

        path = tmp_path / "history.db"
        connection = sqlite3.connect(path)
        connection.executescript(
            "CREATE TABLE analyses (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "created_at TEXT NOT NULL, subject TEXT NOT NULL, payload TEXT NOT NULL);"
        )
        connection.commit()
        connection.close()

        store = HistoryStore(path)
        entry_id = store.save(result_with_tasks(("Send the report", "me", None)), created_at=NOW)
        store.set_task_done(entry_id, 0, True, at=NOW)

        assert store.list_tasks()[0].done is True, "the schema is applied on open, so no migration"

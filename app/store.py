"""Local history of analysed threads.

Everything here stays on the machine, in a single SQLite file the user owns.
Two rules shape the design:

* **only masked content is written.** The pipeline masks personal data before
  the model stage, and the result stored here carries those masked values, so
  a copied database file does not leak somebody's phone number or bank details;
* **deleting is easy and complete.** There is a purge that removes everything,
  because a tool that quietly accumulates a record of somebody's mail is not a
  tool people should trust.

The schema is deliberately small: an identifier, a timestamp, a subject for the
listing, and the analysis itself as JSON. The JSON is validated back into an
:class:`AnalysisResult` on read, so a hand-edited or corrupted row is skipped
rather than trusted.

A second table records which action items the user has ticked off. It holds no
content of its own - an analysis id, the item's position within that analysis,
and when it was ticked - so it cannot widen what a copied database file would
reveal. It is tied to its analysis with ``ON DELETE CASCADE``, so deleting an
analysis takes its ticks with it and the "deleting is complete" rule still
holds.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from app.models import AnalysisResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    subject    TEXT NOT NULL,
    payload    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analyses_created_at ON analyses (created_at DESC);

CREATE TABLE IF NOT EXISTS task_state (
    analysis_id INTEGER NOT NULL REFERENCES analyses (id) ON DELETE CASCADE,
    item_index  INTEGER NOT NULL,
    done_at     TEXT NOT NULL,
    PRIMARY KEY (analysis_id, item_index)
);
"""

_OWNER_ONLY = 0o600
_DEFAULT_LIMIT = 50


@dataclass(frozen=True)
class HistoryEntry:
    """One stored analysis, ready to render."""

    id: int
    created_at: datetime
    thread_subject: str
    result: AnalysisResult


@dataclass(frozen=True)
class Task:
    """One action item, with the analysis it came from.

    ``index`` is the item's position in its analysis. A stored analysis is
    never rewritten, so that position is a stable identifier; and because
    ``analyses.id`` is AUTOINCREMENT, an id is never reused, so a tick can
    never end up attached to a different thread's work.
    """

    analysis_id: int
    index: int
    task: str
    owner: str
    due: str | None
    done: bool
    thread_subject: str
    created_at: datetime
    urgency: str


class HistoryStore:
    """A small SQLite-backed history, safe to delete at any time."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._prepare()

    # -- writing --------------------------------------------------------

    def save(self, result: AnalysisResult, *, created_at: datetime) -> int:
        """Store one analysis and return its identifier."""
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO analyses (created_at, subject, payload) VALUES (?, ?, ?)",
                (created_at.isoformat(), result.thread_subject, result.model_dump_json()),
            )
        return int(cursor.lastrowid)

    # -- reading --------------------------------------------------------

    def get(self, entry_id: int) -> HistoryEntry | None:
        """Return one entry, or None if it is absent or unreadable."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id, created_at, subject, payload FROM analyses WHERE id = ?",
                (entry_id,),
            ).fetchone()

        return _to_entry(row) if row else None

    def list_recent(self, limit: int = _DEFAULT_LIMIT) -> list[HistoryEntry]:
        """Return the most recent entries, newest first.

        Rows that cannot be read back are skipped: one bad row must not make
        the history page unusable.
        """
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id, created_at, subject, payload FROM analyses "
                "ORDER BY datetime(created_at) DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()

        entries = (_to_entry(row) for row in rows)
        return [entry for entry in entries if entry is not None]

    # -- tasks ----------------------------------------------------------

    def list_tasks(self, limit: int = _DEFAULT_LIMIT) -> list[Task]:
        """Return every action item across recent analyses, newest first.

        The action items live inside each stored analysis rather than in a
        table of their own, because they are model output: this reads them
        back out and pairs each with whether it has been ticked.
        """
        entries = self.list_recent(limit)
        if not entries:
            return []

        done = self._done_indexes([entry.id for entry in entries])

        return [
            Task(
                analysis_id=entry.id,
                index=index,
                task=item.task,
                owner=item.owner.value,
                due=item.due,
                done=index in done.get(entry.id, ()),
                thread_subject=entry.thread_subject,
                created_at=entry.created_at,
                urgency=entry.result.summary.urgency.value,
            )
            for entry in entries
            for index, item in enumerate(entry.result.summary.action_items)
        ]

    def count_action_items(self, entry_id: int) -> int | None:
        """How many action items an analysis has, or None if there is no such analysis.

        Used to reject a tick aimed at an item that does not exist, so a stale
        page cannot write state that nothing will ever read.
        """
        entry = self.get(entry_id)
        return len(entry.result.summary.action_items) if entry else None

    def set_task_done(self, entry_id: int, index: int, done: bool, *, at: datetime) -> None:
        """Tick or untick one action item."""
        with self.connect() as connection:
            if done:
                connection.execute(
                    "INSERT INTO task_state (analysis_id, item_index, done_at) VALUES (?, ?, ?) "
                    "ON CONFLICT (analysis_id, item_index) "
                    "DO UPDATE SET done_at = excluded.done_at",
                    (entry_id, index, at.isoformat()),
                )
            else:
                connection.execute(
                    "DELETE FROM task_state WHERE analysis_id = ? AND item_index = ?",
                    (entry_id, index),
                )

    def done_indexes(self, entry_id: int) -> set[int]:
        """Which of one analysis's action items have been ticked."""
        return self._done_indexes([entry_id]).get(entry_id, set())

    def _done_indexes(self, entry_ids: list[int]) -> dict[int, set[int]]:
        if not entry_ids:
            return {}

        placeholders = ",".join("?" * len(entry_ids))
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT analysis_id, item_index FROM task_state "  # noqa: S608 - ids are ints
                f"WHERE analysis_id IN ({placeholders})",
                entry_ids,
            ).fetchall()

        done: dict[int, set[int]] = {}
        for analysis_id, item_index in rows:
            done.setdefault(analysis_id, set()).add(item_index)
        return done

    # -- deleting -------------------------------------------------------

    def delete(self, entry_id: int) -> bool:
        """Delete one entry. Returns whether anything was removed."""
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM analyses WHERE id = ?", (entry_id,))
        return cursor.rowcount > 0

    def purge(self) -> int:
        """Delete every stored analysis and return how many were removed."""
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM analyses")
        return cursor.rowcount

    # -- plumbing -------------------------------------------------------

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Open a connection that commits on success and closes either way."""
        connection = sqlite3.connect(self._path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _prepare(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(_SCHEMA)
        # History can contain private content even after masking, so it is
        # readable by its owner only.
        self._path.chmod(_OWNER_ONLY)


def _to_entry(row: tuple) -> HistoryEntry | None:
    entry_id, created_at, subject, payload = row
    try:
        result = AnalysisResult.model_validate_json(payload)
        moment = datetime.fromisoformat(created_at)
    except (ValidationError, ValueError, json.JSONDecodeError):
        return None

    return HistoryEntry(id=entry_id, created_at=moment, thread_subject=subject, result=result)

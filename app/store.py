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

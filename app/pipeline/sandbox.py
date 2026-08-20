"""Stage 3b - run document parsing in a process we are willing to lose.

``pypdf`` and ``python-docx`` are the only components in this project that must
interpret a complex, attacker-controlled format. A malformed file can make them
hang, crash or allocate without bound, so they never run in the web process.

Each document is handed to a short-lived child process that:

* starts with a near-empty environment, so nothing from the parent leaks in;
* limits its own CPU time, memory, open files and file size (to zero);
* receives the bytes on stdin, so no attachment is ever written to disk;
* is killed by the parent if it outstays the configured timeout.

Whatever happens, the caller gets an :class:`ExtractionOutcome` - never an
exception from a third-party parser.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_WORKER_MODULE = "app.pipeline._extract_worker"

# "-B" stops the child writing bytecode caches, which its zero file-size limit
# would refuse anyway.
_DEFAULT_COMMAND = (sys.executable, "-B", "-m", _WORKER_MODULE)


@dataclass(frozen=True)
class ExtractionOutcome:
    """The result of trying to read one document."""

    text: str | None = None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.text is not None


def build_child_environment() -> dict[str, str]:
    """Build the minimal environment the child process needs.

    Deliberately not inherited: an API key or session token in the parent's
    environment must never be visible to code parsing a hostile document.
    """
    return {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(_PROJECT_ROOT),
        "PYTHONHASHSEED": "0",
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
    }


def run_extraction(
    mime: str,
    payload: bytes,
    *,
    settings: Settings,
    command: tuple[str, ...] | list[str] | None = None,
) -> ExtractionOutcome:
    """Extract text from one document in an isolated child process.

    ``command`` exists so tests can substitute a stand-in child; production
    always uses the real worker module.
    """
    timeout = settings.extraction_timeout_seconds
    argv = [
        *(command or _DEFAULT_COMMAND),
        mime,
        str(settings.max_extracted_chars),
        str(max(1, math.ceil(timeout))),
    ]

    try:
        completed = subprocess.run(
            argv,
            input=payload,
            capture_output=True,
            timeout=timeout,
            cwd=_PROJECT_ROOT,
            env=build_child_environment(),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ExtractionOutcome(error="reading the document took too long, so it was stopped")
    except OSError as exc:
        return ExtractionOutcome(error=f"the document reader could not be started: {exc}")

    if completed.returncode != 0:
        return ExtractionOutcome(
            error=f"the document reader stopped unexpectedly (exit {completed.returncode})"
        )

    return _parse_worker_output(completed.stdout)


def _parse_worker_output(raw_output: bytes) -> ExtractionOutcome:
    try:
        result = json.loads(raw_output.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ExtractionOutcome(error="the document reader returned an unreadable response")

    if not isinstance(result, dict):
        return ExtractionOutcome(error="the document reader returned an unexpected response")

    if result.get("ok") is True and isinstance(result.get("text"), str):
        return ExtractionOutcome(text=result["text"])

    error = result.get("error")
    return ExtractionOutcome(
        error=error if isinstance(error, str) else "the document is unreadable"
    )

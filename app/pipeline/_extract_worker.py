"""The child process that actually opens a document.

Run as ``python -m app.pipeline._extract_worker <mime> <max_chars> <cpu_seconds>``
with the document bytes on stdin. It writes a single JSON object to stdout:

    {"ok": true,  "text": "..."}
    {"ok": false, "error": "why the document could not be read"}

Failures are reported as data, never as a non-zero exit code, so the parent can
tell "this file is unreadable" apart from "the child died".

This module is intentionally tiny and imports the document libraries only after
its own resource limits are in place.
"""

from __future__ import annotations

import json
import sys


def _apply_resource_limits(cpu_seconds: int) -> None:
    """Cap what this process may consume, best effort per platform.

    The limits are applied by the child to itself rather than through a
    ``preexec_fn`` in the parent, which is unsafe in a threaded server.
    """
    try:
        import resource
    except ImportError:  # pragma: no cover - Windows
        return

    limits = [
        (resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds)),
        # A document parser has no business writing files.
        (resource.RLIMIT_FSIZE, (0, 0)),
        (resource.RLIMIT_NOFILE, (64, 64)),
    ]
    if hasattr(resource, "RLIMIT_AS"):
        two_gigabytes = 2 * 1024 * 1024 * 1024
        limits.append((resource.RLIMIT_AS, (two_gigabytes, two_gigabytes)))

    for limit, values in limits:
        try:
            resource.setrlimit(limit, values)
        except (ValueError, OSError):
            # Some limits are not settable on every platform; the parent's own
            # timeout remains the backstop.
            continue


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        json.dump(
            {"ok": False, "error": "the extraction worker was called incorrectly"}, sys.stdout
        )
        return 0

    mime, max_chars, cpu_seconds = argv[1], int(argv[2]), int(argv[3])
    _apply_resource_limits(cpu_seconds)

    payload = sys.stdin.buffer.read()

    from app.pipeline.extractors import ExtractionError, extract_text

    try:
        text = extract_text(mime, payload, max_chars=max_chars)
        result = {"ok": True, "text": text}
    except ExtractionError as exc:
        result = {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - the whole point is to contain anything
        result = {"ok": False, "error": f"the document could not be read ({type(exc).__name__})"}

    json.dump(result, sys.stdout)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the sandbox
    sys.exit(main(sys.argv))

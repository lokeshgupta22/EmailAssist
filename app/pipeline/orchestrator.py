"""The assembly line: runs every stage in order and decides what to do on failure.

This is the only module that knows the shape of the whole pipeline. Each stage
stays unaware of the others, which is what keeps them independently testable;
the ordering rules and the failure policy live here.

The order matters:

1. parse the files into a thread;
2. screen the attachments, then read the ones that passed;
3. derive the facts from the full, unmasked text - they must be accurate;
4. scan for injection attempts, on the same unmasked text;
5. mask personal data, and only then involve the model;
6. check what came back, restore the masked values, and report.

Only one failure is fatal: the model service not running at all. Everything
else degrades to a smaller but honest answer.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Protocol

from app.config import Settings
from app.models import (
    AnalysisResult,
    EmailThread,
    SecurityFlag,
    Summary,
    ThreadFacts,
)
from app.pipeline import guards, privacy
from app.pipeline.enrich import collect_facts
from app.pipeline.parser import parse_thread
from app.pipeline.reader import read_attachments
from app.pipeline.security import SecurityGate
from app.pipeline.summarizer import ModelResponseError, SummaryResult

MODEL_OUTPUT_FLAG = "model_output"


class SummarizerLike(Protocol):
    """The pipeline depends on this behaviour, not on a concrete summarizer."""

    canary: str

    def summarize(
        self, thread: EmailThread, facts: ThreadFacts
    ) -> SummaryResult:  # pragma: no cover - protocol
        ...


class Pipeline:
    """Runs an email thread through every stage and returns one result."""

    def __init__(self, summarizer: SummarizerLike, settings: Settings) -> None:
        self._summarizer = summarizer
        self._settings = settings

    def analyse(self, files: list[bytes], *, now: datetime | None = None) -> AnalysisResult:
        """Analyse one or more ``.eml`` files as a single thread."""
        started = time.monotonic()
        now = now or datetime.now(timezone.utc)

        thread, blobs = parse_thread(files)
        thread = self._process_attachments(thread, blobs)

        # Facts and injection scanning both run on the real text: masking would
        # hide the very details they exist to find.
        facts = collect_facts(thread, now=now)
        flags = guards.find_injection_attempts(thread)

        masked_thread, mapping = self._mask(thread)
        summary, model_used, degraded, model_flags = self._summarise(masked_thread, facts, thread)
        flags += model_flags

        summary = _restore(summary, mapping)

        # Anything still bracketed after restoring is a placeholder the model
        # invented rather than one the pipeline created.
        placeholder_flags = guards.check_placeholders(summary)
        if placeholder_flags:
            summary = guards.strip_placeholders(summary)
            flags += placeholder_flags

        unverified = guards.find_ungrounded_claims(summary, thread, now=now)

        return AnalysisResult(
            thread_subject=thread.subject,
            summary=summary,
            facts=facts,
            attachments=thread.attachments,
            security_flags=flags,
            unverified_claims=unverified,
            degraded=degraded,
            model_used=model_used,
            duration_seconds=round(time.monotonic() - started, 2),
        )

    # -- stages ---------------------------------------------------------

    def _process_attachments(self, thread: EmailThread, blobs: dict[str, bytes]) -> EmailThread:
        """Screen every attachment, then read only the ones that passed."""
        if not thread.attachments:
            return thread

        gate = SecurityGate(self._settings)
        screened = gate.screen_all(thread.attachments, blobs)
        read = read_attachments(screened, blobs, settings=self._settings)
        return thread.model_copy(update={"attachments": read})

    def _mask(self, thread: EmailThread) -> tuple[EmailThread, dict[str, str]]:
        """Replace personal data throughout the thread with stable placeholders.

        One masker covers the whole thread, so the same person keeps the same
        placeholder in every message and every attachment.

        Headers are masked as well as bodies. The From and To lines are handed
        to the model along with the text, so leaving them alone would send real
        addresses to the model while claiming they had been masked. Masking
        them also keeps the model on short, stable tokens instead of long
        addresses it tends to garble.
        """
        detector = privacy.build_detector(self._settings, known_names=_display_names(thread))
        masker = privacy.Masker(detector)

        messages = [
            message.model_copy(
                update={
                    "sender": masker.mask(message.sender),
                    "recipients": [masker.mask(address) for address in message.recipients],
                    "cc": [masker.mask(address) for address in message.cc],
                    "body": masker.mask(message.body),
                }
            )
            for message in thread.messages
        ]
        attachments = [
            (
                attachment.model_copy(
                    update={"extracted_text": masker.mask(attachment.extracted_text)}
                )
                if attachment.has_text
                else attachment
            )
            for attachment in thread.attachments
        ]

        masked = thread.model_copy(
            update={
                "subject": masker.mask(thread.subject),
                "messages": messages,
                "attachments": attachments,
            }
        )
        return masked, masker.mapping

    def _summarise(
        self, masked_thread: EmailThread, facts: ThreadFacts, original: EmailThread
    ) -> tuple[Summary, str | None, bool, list[SecurityFlag]]:
        """Ask the model, then decide whether its answer may be used.

        A model service that is not running raises: that is a setup problem the
        user must fix, and pretending otherwise would hide it. Everything else
        falls back to a summary built from the derived facts.
        """
        try:
            result = self._summarizer.summarize(masked_thread, facts)
        except ModelResponseError as exc:
            return (
                guards.build_fallback_summary(original, facts),
                None,
                True,
                [
                    SecurityFlag(
                        kind=MODEL_OUTPUT_FLAG,
                        detail=(
                            f"the model did not return a usable answer ({exc}); "
                            "the summary below was built from the thread itself"
                        ),
                    )
                ],
            )

        canary_flags = guards.check_canary(result.summary, self._summarizer.canary)
        if canary_flags:
            return (
                guards.build_fallback_summary(original, facts),
                None,
                True,
                canary_flags,
            )

        return result.summary, result.model_used, False, []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _display_names(thread: EmailThread) -> list[str]:
    """Guess participant names from their addresses.

    ``alice.smith@example.com`` yields "Alice Smith" and "Alice", which is
    enough for the regex detector to mask names precisely without guessing.
    """
    names: list[str] = []
    for address in thread.participants:
        local_part = address.split("@")[0]
        words = [word for word in local_part.replace("_", ".").split(".") if word.isalpha()]
        names += [word.capitalize() for word in words if len(word) > 2]
        if len(words) > 1:
            names.append(" ".join(word.capitalize() for word in words))
    return list(dict.fromkeys(names))


def _restore(summary: Summary, mapping: dict[str, str]) -> Summary:
    """Put the real values back into the answer shown to the user."""
    if not mapping:
        return summary

    return summary.model_copy(
        update={
            "summary": privacy.unmask(summary.summary, mapping),
            "suggested_next_step": privacy.unmask(summary.suggested_next_step, mapping),
            "key_points": [privacy.unmask(point, mapping) for point in summary.key_points],
            "action_items": [
                item.model_copy(update={"task": privacy.unmask(item.task, mapping)})
                for item in summary.action_items
            ],
        }
    )


__all__ = ["Pipeline", "MODEL_OUTPUT_FLAG"]

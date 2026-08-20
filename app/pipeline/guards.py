"""Stage 7 - check the model's answer before anybody sees it.

A language model is a useful component and an unreliable witness, so its output
is treated as a claim to be verified rather than a result to be displayed.
Three checks run here:

* **injection detection** - does the email itself try to give the assistant
  orders? The user is told, because that changes how much they should trust the
  summary, and because it is usually a sign the email is hostile;
* **canary check** - did the model repeat the secret marker from its own
  instructions? If so it has been talked into leaking its prompt and the answer
  is not used;
* **grounding** - does every date, amount and address in the summary actually
  appear in the source? Anything that does not is reported as unverified.

When the model cannot produce a usable answer at all, a summary is built from
the derived facts instead, so the user always gets something honest rather than
an error page.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from app.models import (
    ActionItem,
    EmailThread,
    SecurityFlag,
    Summary,
    ThreadFacts,
    Urgency,
    WaitingOn,
)

INJECTION_FLAG = "prompt_injection"
CANARY_FLAG = "prompt_leak"
UNGROUNDED_FLAG = "unverified_claim"

# Phrases that only appear when somebody is addressing the assistant rather
# than the recipient. Each one is anchored tightly enough that ordinary
# business English ("please ignore my previous email") does not match.
_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "an instruction to ignore earlier instructions",
        re.compile(
            r"\b(?:ignore|disregard|forget)\b"
            r"[^.\n]{0,30}\b(?:previous|prior|above|all|earlier)\b"
            r"[^.\n]{0,20}\b(?:instruction|prompt|rule|direction|command)s?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "an attempt to change the assistant's role",
        re.compile(r"\byou are (?:now|no longer)\b[^.\n]{0,60}", re.IGNORECASE),
    ),
    (
        "an attempt to read the system prompt",
        re.compile(
            r"\b(?:reveal|print|show|repeat|output|display)\b"
            r"[^.\n]{0,25}\byour\b[^.\n]{0,15}"
            r"\b(?:system )?(?:prompt|instructions)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "text impersonating a system message",
        re.compile(
            r"(?:^|\n)\s*(?:#{2,}\s*)?(?:system|assistant|developer)\s*:\s*\S",
            re.IGNORECASE,
        ),
    ),
    (
        "a hidden instruction aimed at an assistant",
        re.compile(r"<!--[^>]*\b(?:assistant|ai|model|llm)\b[^>]*-->", re.IGNORECASE),
    ),
    (
        "a marker used to inject new instructions",
        re.compile(r"#{2,}\s*(?:new|updated)\s+instructions?\s*#{0,}", re.IGNORECASE),
    ),
)

_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
# Amounts worth checking: anything with a thousands separator, a decimal part,
# or four or more digits. Small counting numbers are ignored deliberately.
_AMOUNT_RE = re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d+\.\d{2}\b|\b\d{4,}\b")
_PERCENT_RE = re.compile(r"\b\d+(?:\.\d+)?\s?%")

_MAX_QUOTE = 80


# ---------------------------------------------------------------------------
# Injection detection
# ---------------------------------------------------------------------------


def find_injection_attempts(thread: EmailThread) -> list[SecurityFlag]:
    """Report text in the thread that tries to give the assistant orders.

    This does not block anything: the model has no tools and cannot act, so an
    injection cannot cause harm. It is surfaced because a user deserves to know
    that the email they are reading was built to manipulate an assistant.
    """
    flags: list[SecurityFlag] = []

    for message in thread.messages:
        flags += _scan(message.body, source=f"the message from {message.sender}")

    for attachment in thread.attachments:
        if attachment.has_text:
            flags += _scan(
                attachment.extracted_text, source=f"the attachment {attachment.filename}"
            )

    return _dedupe(flags)


def _scan(text: str, *, source: str) -> list[SecurityFlag]:
    flags = []
    for description, pattern in _INJECTION_PATTERNS:
        match = pattern.search(text)
        if match:
            flags.append(
                SecurityFlag(
                    kind=INJECTION_FLAG,
                    detail=f'{source} contains {description}: "{_quote(match.group(0))}"',
                )
            )
    return flags


# ---------------------------------------------------------------------------
# Canary
# ---------------------------------------------------------------------------


def check_canary(summary: Summary, canary: str) -> list[SecurityFlag]:
    """Detect the model repeating the secret marker from its own instructions."""
    if canary and canary in _all_text(summary):
        return [
            SecurityFlag(
                kind=CANARY_FLAG,
                detail=(
                    "the model repeated its own session marker, which means it was "
                    "persuaded to reveal its instructions; the answer was discarded"
                ),
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Grounding
# ---------------------------------------------------------------------------


def find_ungrounded_claims(
    summary: Summary, thread: EmailThread, *, now: datetime | None = None
) -> list[str]:
    """Return the specifics in the summary that do not appear in the source.

    Dates, amounts, percentages and email addresses are the details a reader
    acts on, and the details a model is most likely to get wrong, so each one
    must be traceable back to the thread, its headers or an attachment.

    ``now`` must be the same reference time the enrichment stage used. A thread
    saying "before Friday" resolves to a real date only relative to a moment,
    and checking it against a different moment would report a correct deadline
    as invented.
    """
    source = _source_text(thread)
    source_dates = _dates_in(source, now=now)
    claimed = _all_text(summary)
    claims: list[str] = []

    for value in _ISO_DATE_RE.findall(claimed):
        if value not in source_dates:
            claims.append(f"the date {value} does not appear in the thread")

    # Dates are already accounted for; removing them stops the year inside one
    # being reported a second time as a stray number.
    without_dates = _ISO_DATE_RE.sub(" ", claimed)

    for pattern, label in ((_AMOUNT_RE, "amount"), (_PERCENT_RE, "figure"), (_EMAIL_RE, "address")):
        for value in pattern.findall(without_dates):
            if _normalise(value) not in _normalise(source):
                claims.append(f"the {label} {value} does not appear in the thread")

    return list(dict.fromkeys(claims))


def _dates_in(text: str, *, now: datetime | None) -> set[str]:
    """Every date in the source, in ISO form, however it was written.

    Imported lazily: the enrichment stage owns date handling, and this keeps the
    two from importing each other.
    """
    from app.pipeline.enrich import find_dates

    reference = now or datetime.now(timezone.utc)
    return set(_ISO_DATE_RE.findall(text)) | set(find_dates(text, now=reference))


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------


def build_fallback_summary(thread: EmailThread, facts: ThreadFacts) -> Summary:
    """Build a summary from derived facts when the model produced nothing usable.

    Deliberately dull: it reports only what the enrichment stage established, so
    it can never contain an invented date or amount. A plain, true answer beats
    an error page.
    """
    who = facts.last_sender or "the other side"
    waiting = _infer_waiting(facts)

    sentences = [
        f"This thread has {facts.message_count} "
        f"message{'s' if facts.message_count != 1 else ''} "
        f"between {', '.join(facts.participants) or 'the participants'}."
    ]
    sentences.append(f"The last message was written by {who}.")
    if facts.days_since_last_message is not None:
        sentences.append(f"It has been waiting {facts.days_since_last_message} days.")

    key_points = [f"Subject: {thread.subject}"]
    if facts.dates_mentioned:
        key_points.append(f"Dates mentioned: {', '.join(facts.dates_mentioned)}")
    if facts.open_questions:
        key_points.append(f"Unanswered question: {facts.open_questions[0]}")

    if facts.open_questions:
        next_step = f'Answer the unanswered question: "{facts.open_questions[0]}"'
        actions = [ActionItem(task=next_step, owner="me", due=None)]
    else:
        next_step = f"Read the thread and reply to {who}."
        actions = []

    return Summary(
        summary=" ".join(sentences),
        key_points=key_points,
        action_items=actions,
        suggested_next_step=next_step,
        urgency=_infer_urgency(facts),
        waiting_on=waiting,
    )


def _infer_waiting(facts: ThreadFacts) -> WaitingOn:
    if facts.open_questions:
        return WaitingOn.ME
    return WaitingOn.NOBODY


def _infer_urgency(facts: ThreadFacts) -> Urgency:
    """A crude but explainable rule, used only when the model has failed."""
    waiting_days = facts.days_since_last_message
    if facts.open_questions and waiting_days is not None and waiting_days >= 3:
        return Urgency.HIGH
    if facts.open_questions:
        return Urgency.MEDIUM
    return Urgency.LOW


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _source_text(thread: EmailThread) -> str:
    """Everything the summary is allowed to draw on.

    Headers count as source: an address in the From or To line is as much a
    fact of the thread as one written in the body.
    """
    parts = [thread.subject]
    for message in thread.messages:
        parts += [message.sender, *message.recipients, *message.cc, message.body]
        if message.sent_at:
            # The day a message was sent is as much a fact of the thread as
            # anything written in it.
            parts.append(message.sent_at.date().isoformat())
    parts += [attachment.extracted_text for attachment in thread.attachments if attachment.has_text]
    return "\n".join(part for part in parts if part)


def _all_text(summary: Summary) -> str:
    parts = [summary.summary, summary.suggested_next_step, *summary.key_points]
    for item in summary.action_items:
        parts.append(item.task)
        if item.due:
            parts.append(item.due)
    return "\n".join(parts)


def _normalise(text: str) -> str:
    """Compare figures without being fooled by separators or spacing."""
    return re.sub(r"[\s,]", "", text).lower()


def _quote(text: str) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= _MAX_QUOTE else collapsed[:_MAX_QUOTE] + "..."


def _dedupe(flags: list[SecurityFlag]) -> list[SecurityFlag]:
    seen: set[tuple[str, str]] = set()
    unique = []
    for flag in flags:
        key = (flag.kind, flag.detail)
        if key not in seen:
            seen.add(key)
            unique.append(flag)
    return unique

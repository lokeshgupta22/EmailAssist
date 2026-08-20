"""Stage 5 - establish the facts without asking the model.

Anything a regular program can determine reliably is determined here: who is in
the thread, who spoke last, how long it has been waiting, which dates are
mentioned and which questions are still unanswered.

Two things follow from that. The model is handed these facts as ground truth
instead of being asked to work them out, and the interface can show them
directly, so a deadline on screen is one a program found in the text rather
than one a language model produced.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

import dateparser
from dateparser.search import search_dates

from app.models import EmailThread, ThreadFacts

_QUESTION_RE = re.compile(r"[^.!?\n]*\?")
_MIN_QUESTION_WORDS = 2

# Fragments that look like dates to a fuzzy parser but are not: version
# numbers, money and long reference numbers.
_NOT_A_DATE_RE = re.compile(
    r"\b\d+\.\d+\.\d+\b|\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d{5,}\b",
)

_SEARCH_SETTINGS = {
    "RETURN_AS_TIMEZONE_AWARE": True,
    "TIMEZONE": "UTC",
    "PREFER_DATES_FROM": "future",
    "REQUIRE_PARTS": ["day", "month"],
}


def collect_facts(
    thread: EmailThread,
    *,
    now: datetime | None = None,
    owner_address: str | None = None,
) -> ThreadFacts:
    """Derive everything about the thread that does not need a language model.

    ``owner_address`` is which participant is the person reading the summary.
    The model cannot work this out - every address looks alike to it - and
    without it "who is waiting on whom" is guesswork.
    """
    now = now or datetime.now(timezone.utc)
    latest = thread.latest_message

    sources = [message.body for message in thread.messages]
    sources += [
        attachment.extracted_text for attachment in thread.attachments if attachment.has_text
    ]

    return ThreadFacts(
        today=now.date().isoformat(),
        owner_address=_match_owner(owner_address, thread),
        participants=thread.participants,
        message_count=len(thread.messages),
        last_sender=latest.sender,
        last_message_at=latest.sent_at,
        days_since_last_message=_days_since(latest.sent_at, now),
        dates_mentioned=find_dates("\n".join(text for text in sources if text), now=now),
        open_questions=_open_questions(thread),
    )


def _match_owner(owner_address: str | None, thread: EmailThread) -> str | None:
    """Return the configured owner address if it takes part in this thread."""
    if not owner_address:
        return None

    wanted = owner_address.strip().lower()
    everyone = {participant.lower() for participant in thread.participants}
    for message in thread.messages:
        everyone.update(address.lower() for address in message.recipients)
        everyone.update(address.lower() for address in message.cc)

    return wanted if wanted in everyone else None


def find_dates(text: str, *, now: datetime) -> list[str]:
    """Return the ISO dates mentioned in the text, in calendar order.

    Relative expressions such as "next Friday" are resolved against ``now``,
    which is why the reference time is an argument: the same email analysed
    twice must never produce two different deadlines.
    """
    if not text.strip():
        return []

    cleaned = _NOT_A_DATE_RE.sub(" ", text)

    try:
        found = search_dates(cleaned, languages=["en"], settings=_SEARCH_SETTINGS) or []
    except (ValueError, TypeError, RecursionError):
        return []

    dates = {
        parsed.astimezone(timezone.utc).date().isoformat()
        for fragment, parsed in found
        if _is_meaningful_date_fragment(fragment)
    }
    dates |= _relative_dates(cleaned, now=now)
    return sorted(dates)


def find_open_questions(text: str) -> list[str]:
    """Return the questions asked in the text, tidied onto a single line."""
    questions = []
    for match in _QUESTION_RE.finditer(text.replace("\n", " ")):
        question = " ".join(match.group(0).split())
        if len(question.split()) >= _MIN_QUESTION_WORDS:
            questions.append(question)
    return questions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open_questions(thread: EmailThread) -> list[str]:
    """Questions from the newest message, plus older ones nobody replied to.

    A question is treated as answered once the other side has written back,
    which is a rough rule but a predictable one - and it is always visible to
    the user, unlike a judgement made inside a model.

    That rule alone is not enough: a reply often repeats the question it is
    answering, whether quoted verbatim by the mail client or restated in
    prose ("Regarding your question: ..."). Read naively, that repetition
    looks like the replier freshly asking their own question back. Any
    question that already appears in an earlier message from someone else is
    therefore dropped here - it is being addressed, not asked.
    """
    latest = thread.latest_message
    questions = find_open_questions(latest.body)

    for message in reversed(thread.messages[:-1]):
        if message.sender != latest.sender:
            break
        questions = find_open_questions(message.body) + questions

    already_asked_by_someone_else = {
        question.lower()
        for message in thread.messages
        if message.sender != latest.sender
        for question in find_open_questions(message.body)
    }
    questions = [q for q in questions if q.lower() not in already_asked_by_someone_else]

    return list(dict.fromkeys(questions))


def _relative_dates(text: str, *, now: datetime) -> set[str]:
    """Resolve relative phrases that a plain date search does not report.

    Weekday phrases are resolved here rather than by the date library, which
    declines "next Friday" altogether. The rules are stated explicitly so the
    behaviour is predictable and reviewable:

    * "Friday" / "this Friday" - the next Friday after today;
    * "next Friday" - the Friday of the following calendar week;
    * "last Friday" - the most recent Friday before today.

    Every resolved date is shown to the user as a mentioned date, so a wrong
    reading is visible rather than buried inside a summary.
    """
    found = set()
    today = now.date()

    for match in _RELATIVE_PHRASE_RE.finditer(text.lower()):
        weekday = match.group("weekday")
        if weekday is not None:
            found.add(_resolve_weekday(today, weekday, match.group("qualifier")).isoformat())
            continue

        parsed = dateparser.parse(
            match.group(0),
            languages=["en"],
            settings={
                "RELATIVE_BASE": now.replace(tzinfo=None),
                "PREFER_DATES_FROM": "past" if "last" in match.group(0) else "future",
            },
        )
        if parsed is not None:
            found.add(parsed.date().isoformat())

    return found


def _resolve_weekday(today: date, weekday: str, qualifier: str | None) -> date:
    """Turn a weekday name and its qualifier into a concrete date."""
    target = _WEEKDAYS.index(weekday)

    if qualifier == "last":
        days_back = (today.weekday() - target) % 7 or 7
        return today - timedelta(days=days_back)

    days_ahead = (target - today.weekday()) % 7 or 7
    resolved = today + timedelta(days=days_ahead)

    # "next Friday" means the following week's Friday, so skip forward when the
    # nearest one still falls in the current week.
    if qualifier == "next" and resolved.isocalendar()[:2] == today.isocalendar()[:2]:
        resolved += timedelta(days=7)
    return resolved


_WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

_RELATIVE_PHRASE_RE = re.compile(
    r"\b(?:today|tomorrow|yesterday"
    r"|(?:next|this|last)\s+(?:week|month)"
    r"|(?:(?P<qualifier>next|this|last)\s+)?(?P<weekday>" + "|".join(_WEEKDAYS) + r"))\b"
)


def _is_meaningful_date_fragment(fragment: str) -> bool:
    """Reject fragments that carry no real date information.

    ``search_dates`` will happily report a bare "12" or a stray time as a date;
    a usable fragment needs a month name or a separator between numbers.
    """
    lowered = fragment.lower()
    if any(month in lowered for month in _MONTHS):
        return True
    return bool(re.search(r"\d[/-]\d", lowered))


_MONTHS = (
    "jan",
    "feb",
    "mar",
    "apr",
    "may",
    "jun",
    "jul",
    "aug",
    "sep",
    "oct",
    "nov",
    "dec",
)


def _days_since(moment: datetime | None, now: datetime) -> int | None:
    if moment is None:
        return None
    return max(0, (now - moment).days)

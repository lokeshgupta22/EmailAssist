"""Stage 4 - mask personal data before anything else looks at the text.

The model in this project runs locally, so masking is not strictly required to
keep data on the machine. It is done anyway for two reasons:

* the history database and any log line only ever hold masked text, so a stolen
  database file does not leak somebody's phone number or bank details;
* the pipeline stays safe to point at a different model later without having to
  revisit every stage.

Detection is deliberately pluggable. The default backend needs no extra
dependencies and is conservative: it only masks values it can recognise with
high confidence, because a summary that has lost its dates and amounts is
useless. A Presidio backend can be enabled when the extra install is acceptable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.config import Settings

PLACEHOLDER_RE = re.compile(r"\[[A-Z]+_\d+\]")

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
# 13-19 digits, optionally grouped by spaces or hyphens; the checksum decides.
_CARD_RE = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
# Phone-like runs of digits with the usual punctuation. Verified afterwards by
# digit count so that quantities, versions and money are never caught.
_PHONE_RE = re.compile(r"(?<![\w.])\+?\d[\d\s().-]{7,18}\d(?![\w.])")

_MIN_PHONE_DIGITS = 9
_MAX_PHONE_DIGITS = 15


@dataclass(frozen=True, order=True)
class PiiSpan:
    """A stretch of text that should not reach the model in the clear."""

    start: int
    end: int
    kind: str
    value: str

    @property
    def length(self) -> int:
        return self.end - self.start


@runtime_checkable
class PiiDetector(Protocol):
    """Anything that can point at the personal data inside a piece of text."""

    def detect(self, text: str) -> list[PiiSpan]:  # pragma: no cover - protocol
        ...


class NullPiiDetector:
    """Finds nothing. Used when masking is switched off deliberately."""

    def detect(self, text: str) -> list[PiiSpan]:  # noqa: ARG002 - protocol signature
        return []


class RegexPiiDetector:
    """Dependency-free detector for values with an unambiguous shape.

    Names cannot be recognised by shape, so instead of guessing, the detector
    is told which names appear in the thread: the email headers already list
    every participant, which makes name masking precise rather than statistical.
    """

    def __init__(self, known_names: list[str] | None = None) -> None:
        self._name_patterns = [
            (name, re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE))
            for name in sorted(known_names or [], key=len, reverse=True)
            if name.strip()
        ]

    def detect(self, text: str) -> list[PiiSpan]:
        spans: list[PiiSpan] = []
        spans += _matches(_EMAIL_RE, text, "EMAIL")
        spans += _matches(_IBAN_RE, text, "IBAN")
        spans += _matches(_SSN_RE, text, "SSN")
        spans += [span for span in _matches(_CARD_RE, text, "CARD") if _passes_luhn(span.value)]
        spans += [span for span in _matches(_PHONE_RE, text, "PHONE") if _looks_like_phone(span)]

        for name, pattern in self._name_patterns:
            spans += [
                PiiSpan(start=match.start(), end=match.end(), kind="PERSON", value=name)
                for match in pattern.finditer(text)
            ]

        return sorted(spans)


class PresidioPiiDetector:
    """Adapter over Microsoft Presidio, for when statistical name detection is wanted.

    Presidio and its spaCy model are an optional install; construction raises
    :class:`ImportError` when they are absent, and the factory falls back.
    """

    _ENTITIES = ("PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD", "IBAN_CODE", "US_SSN")
    _KIND_BY_ENTITY = {
        "EMAIL_ADDRESS": "EMAIL",
        "PHONE_NUMBER": "PHONE",
        "CREDIT_CARD": "CARD",
        "IBAN_CODE": "IBAN",
        "US_SSN": "SSN",
        "PERSON": "PERSON",
    }

    def __init__(self) -> None:
        from presidio_analyzer import AnalyzerEngine  # noqa: PLC0415 - optional dependency

        self._analyzer = AnalyzerEngine()

    def detect(self, text: str) -> list[PiiSpan]:
        results = self._analyzer.analyze(text=text, entities=list(self._ENTITIES), language="en")
        return sorted(
            PiiSpan(
                start=result.start,
                end=result.end,
                kind=self._KIND_BY_ENTITY.get(result.entity_type, result.entity_type),
                value=text[result.start : result.end],
            )
            for result in results
        )


def build_detector(settings: Settings, known_names: list[str] | None = None) -> PiiDetector:
    """Pick a detector from configuration, degrading safely.

    If Presidio is requested but not installed, the regex detector is used
    rather than failing the request: losing statistical name detection is a
    smaller problem than refusing to analyse the email at all.
    """
    if settings.pii_backend == "none":
        return NullPiiDetector()

    if settings.pii_backend == "presidio":
        try:
            return PresidioPiiDetector()
        except ImportError:
            pass

    return RegexPiiDetector(known_names=known_names)


def unmask(text: str, mapping: dict[str, str]) -> str:
    """Put the real values back into text that came out of the model."""
    for placeholder, value in mapping.items():
        text = text.replace(placeholder, value)
    return text


class Masker:
    """Applies consistent placeholders across every text in one thread.

    A thread is many separate pieces of text - each message, each attachment -
    and they must share one numbering. If each piece were masked on its own,
    the first address in message one and the first address in message two
    would both become ``[EMAIL_1]`` while being different people, and the
    model would be told that two strangers are the same person.

    So the counters and the mapping live on the masker, not on a single call.
    """

    def __init__(self, detector: PiiDetector) -> None:
        self._detector = detector
        self._mapping: dict[str, str] = {}
        self._placeholder_for: dict[tuple[str, str], str] = {}
        self._counters: dict[str, int] = {}

    @property
    def mapping(self) -> dict[str, str]:
        """Placeholder to real value, for everything masked so far."""
        return dict(self._mapping)

    def mask(self, text: str) -> str:
        """Replace personal data in one piece of text with placeholders."""
        if not text:
            return text

        spans = _resolve_overlaps(self._detector.detect(text))
        if not spans:
            return text

        # Replace from the end so earlier offsets stay valid.
        masked = text
        for span in sorted(spans, reverse=True):
            placeholder = self._placeholder(span, text)
            masked = masked[: span.start] + placeholder + masked[span.end :]
        return masked

    def unmask(self, text: str) -> str:
        """Put the real values back into text produced downstream."""
        return unmask(text, self._mapping)

    def _placeholder(self, span: PiiSpan, text: str) -> str:
        key = (span.kind, span.value.strip().lower())
        existing = self._placeholder_for.get(key)
        if existing is not None:
            return existing

        self._counters[span.kind] = self._counters.get(span.kind, 0) + 1
        placeholder = f"[{span.kind}_{self._counters[span.kind]}]"
        self._placeholder_for[key] = placeholder
        self._mapping[placeholder] = text[span.start : span.end].strip()
        return placeholder


def mask(text: str, detector: PiiDetector) -> tuple[str, dict[str, str]]:
    """Mask one standalone piece of text.

    Convenience wrapper for callers with nothing to share; anything handling a
    whole thread should use :class:`Masker` so numbering stays consistent.
    """
    masker = Masker(detector)
    return masker.mask(text), masker.mapping


def remaining_placeholders(text: str) -> list[str]:
    """Return placeholders that were never restored.

    Their presence means the model invented a placeholder, which the guardrails
    treat as a signal that the output should not be trusted verbatim.
    """
    return PLACEHOLDER_RE.findall(text)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _matches(pattern: re.Pattern[str], text: str, kind: str) -> list[PiiSpan]:
    return [
        PiiSpan(start=match.start(), end=match.end(), kind=kind, value=match.group(0))
        for match in pattern.finditer(text)
    ]


def _resolve_overlaps(spans: list[PiiSpan]) -> list[PiiSpan]:
    """Keep the longest match when two detectors claim the same characters.

    A card number also looks like a phone number; the longer, more specific
    match is the right one.
    """
    kept: list[PiiSpan] = []
    for span in sorted(spans, key=lambda item: (-item.length, item.start)):
        if any(span.start < other.end and other.start < span.end for other in kept):
            continue
        kept.append(span)
    return sorted(kept)


def _passes_luhn(raw_value: str) -> bool:
    """Check the card checksum so ordinary reference numbers are left alone."""
    digits = [int(character) for character in raw_value if character.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False

    checksum = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def _looks_like_phone(span: PiiSpan) -> bool:
    digits = sum(character.isdigit() for character in span.value)
    return _MIN_PHONE_DIGITS <= digits <= _MAX_PHONE_DIGITS

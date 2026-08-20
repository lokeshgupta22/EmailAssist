"""Stage 1 - turn raw ``.eml`` bytes into an ordered :class:`EmailThread`.

Two properties matter here beyond correctness:

* **Nothing is rendered.** HTML bodies are parsed for their text only, so
  tracking pixels never fire and scripts never execute.
* **Nothing is trusted.** Missing, malformed or hostile headers degrade to a
  safe default instead of raising, because a single odd message must not take
  the whole thread down.

Attachment *bytes* are returned separately from the thread, keyed by digest, so
that later stages decide what may be opened - the parser itself never looks
inside an attachment.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email import message_from_bytes, policy
from email.header import decode_header, make_header
from email.message import EmailMessage as MimeMessage
from email.utils import getaddresses, parsedate_to_datetime

import dateparser

from app.models import Attachment, AttachmentStatus, EmailMessage, EmailThread
from app.pipeline.text import dequote, html_to_text, normalise_whitespace, strip_signature

NO_SUBJECT = "(no subject)"

_REPLY_PREFIX_RE = re.compile(r"^(?:re|fwd|fw|aw|antw)\s*(?:\[\d+\])?\s*:\s*", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_ANGLE_ADDRESS_RE = re.compile(r"<[^>]*>")

# "On Tue, 11 Aug 2026 at 09:00, Alice <alice@example.com> wrote:"
_ON_WROTE_RE = re.compile(
    r"^[ \t>]*On\s+(?P<meta>.{1,400}?)\s+wrote:[ \t]*$", re.MULTILINE | re.DOTALL
)
# "-----Original Message-----" followed by a From:/Sent: header block.
_ORIGINAL_MESSAGE_RE = re.compile(
    r"^[ \t>]*-{2,}\s*Original Message\s*-{2,}[ \t]*$", re.MULTILINE | re.IGNORECASE
)
# A bare Outlook header block, which many clients emit without the banner above.
_HEADER_BLOCK_RE = re.compile(r"^[ \t>]*From:[ \t]*.+$\n(?=^[ \t>]*(?:Sent|Date):)", re.MULTILINE)

_DATEPARSER_SETTINGS = {"RETURN_AS_TIMEZONE_AWARE": True, "TIMEZONE": "UTC"}


class EmailParseError(ValueError):
    """Raised when a file cannot be understood as an email at all."""


@dataclass(frozen=True)
class _Attribution:
    """Where a quoted message starts, and who wrote it."""

    start: int
    body_start: int
    sender: str | None
    sent_at: datetime | None


@dataclass
class _ParsedFile:
    subject: str
    messages: list[EmailMessage]
    attachments: list[Attachment] = field(default_factory=list)
    blobs: dict[str, bytes] = field(default_factory=dict)

    @property
    def earliest(self) -> datetime | None:
        dates = [message.sent_at for message in self.messages if message.sent_at]
        return min(dates) if dates else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_thread(files: list[bytes]) -> tuple[EmailThread, dict[str, bytes]]:
    """Merge one or more ``.eml`` files into a single ordered thread.

    Returns the thread plus the raw attachment bytes keyed by SHA-256 digest.
    """
    if not files:
        raise EmailParseError("no email files were provided")

    parsed = [_parse_file(raw) for raw in files]

    messages = _deduplicate([message for item in parsed for message in item.messages])
    if not messages:
        raise EmailParseError("no readable messages were found in the uploaded files")

    attachments: dict[str, Attachment] = {}
    blobs: dict[str, bytes] = {}
    for item in parsed:
        for attachment in item.attachments:
            attachments.setdefault(attachment.sha256, attachment)
        blobs.update(item.blobs)

    thread = EmailThread(
        subject=_pick_subject(parsed),
        messages=messages,
        attachments=list(attachments.values()),
    )
    return thread, blobs


def parse_eml(raw: bytes) -> EmailThread:
    """Parse a single ``.eml`` file. Convenience wrapper around :func:`parse_thread`."""
    thread, _ = parse_thread([raw])
    return thread


# ---------------------------------------------------------------------------
# File level
# ---------------------------------------------------------------------------


def _parse_file(raw: bytes) -> _ParsedFile:
    if not raw.strip():
        raise EmailParseError("the uploaded file is empty")

    try:
        mime = message_from_bytes(raw, policy=policy.default)
    except Exception as exc:  # pragma: no cover - defensive, stdlib rarely raises here
        raise EmailParseError(f"the file could not be parsed as an email: {exc}") from exc

    if not isinstance(mime, MimeMessage) or mime.get("From") is None:
        raise EmailParseError("the file has no From header, so it is not an email")

    sender = _first_address(mime.get("From", ""))
    if sender is None:
        raise EmailParseError("the From header does not contain an email address")

    recipients = _addresses(mime.get_all("To", []))
    cc = _addresses(mime.get_all("Cc", []))
    sent_at = _parse_header_date(mime.get("Date"))
    body = _extract_body(mime)

    messages = _split_history(body, sender=sender, sent_at=sent_at)
    for message in messages:
        # Only the newest message in a file carries the envelope recipients; the
        # quoted history has no reliable recipient information.
        if message.sender == sender and message is messages[-1]:
            message.recipients = recipients
            message.cc = cc

    attachments, blobs = _collect_attachments(mime)
    return _ParsedFile(
        subject=_clean_subject(mime.get("Subject")),
        messages=messages,
        attachments=attachments,
        blobs=blobs,
    )


def _extract_body(mime: MimeMessage) -> str:
    """Return the best plain-text representation of the message body."""
    plain = _find_part_text(mime, "text/plain")
    if plain:
        return normalise_whitespace(plain)

    html = _find_part_text(mime, "text/html")
    if html:
        return html_to_text(html)

    return ""


def _find_part_text(mime: MimeMessage, content_type: str) -> str | None:
    for part in mime.walk():
        if part.get_content_type() != content_type:
            continue
        if part.get_content_disposition() == "attachment":
            continue
        text = _decode_part(part)
        if text and text.strip():
            return text
    return None


def _decode_part(part: MimeMessage) -> str | None:
    """Decode a part, tolerating a charset label that does not match the bytes."""
    payload = part.get_payload(decode=True)
    if payload is None:
        return None
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _collect_attachments(mime: MimeMessage) -> tuple[list[Attachment], dict[str, bytes]]:
    """Record attachment metadata and bytes without inspecting their content.

    Parts carrying a ``Content-ID`` are skipped: those are inline resources
    referenced by the HTML body, such as signature logos, rather than documents
    somebody deliberately attached.
    """
    attachments: list[Attachment] = []
    blobs: dict[str, bytes] = {}

    for part in mime.iter_attachments():
        if part.get("Content-ID") is not None:
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        digest = hashlib.sha256(payload).hexdigest()
        blobs[digest] = payload
        attachments.append(
            Attachment(
                filename=_decode_header(part.get_filename()) or "unnamed",
                declared_mime=part.get_content_type(),
                size_bytes=len(payload),
                sha256=digest,
                status=AttachmentStatus.PENDING,
            )
        )

    return attachments, blobs


# ---------------------------------------------------------------------------
# Quoted history
# ---------------------------------------------------------------------------


def _split_history(body: str, *, sender: str, sent_at: datetime | None) -> list[EmailMessage]:
    """Split a body into its own message plus any quoted ancestors.

    Returns messages oldest first, so that messages sharing a timestamp keep a
    sensible order once the thread sorts them.
    """
    body = normalise_whitespace(body)
    attribution = _find_attribution(body)

    if attribution is None:
        own_text = strip_signature(body).strip()
        return [EmailMessage(sender=sender, sent_at=sent_at, body=own_text)] if own_text else []

    own_text = strip_signature(body[: attribution.start]).strip()
    quoted_text = dequote(body[attribution.body_start :])

    older = _split_history(
        quoted_text,
        sender=attribution.sender or sender,
        sent_at=attribution.sent_at,
    )
    if own_text:
        older.append(EmailMessage(sender=sender, sent_at=sent_at, body=own_text))
    return older


def _find_attribution(body: str) -> _Attribution | None:
    """Locate the earliest quote header in the body, whatever style it uses."""
    candidates = [
        _match_on_wrote(body),
        _match_original_message(body),
        _match_header_block(body),
    ]
    found = [candidate for candidate in candidates if candidate is not None]
    return min(found, key=lambda candidate: candidate.start) if found else None


def _match_on_wrote(body: str) -> _Attribution | None:
    match = _ON_WROTE_RE.search(body)
    if match is None:
        return None
    meta = match.group("meta")
    return _Attribution(
        start=match.start(),
        body_start=match.end(),
        sender=_first_address(meta),
        sent_at=_parse_loose_date(_ANGLE_ADDRESS_RE.sub("", meta)),
    )


def _match_original_message(body: str) -> _Attribution | None:
    match = _ORIGINAL_MESSAGE_RE.search(body)
    if match is None:
        return None
    sender, sent_at, body_start = _read_header_block(body, match.end())
    return _Attribution(start=match.start(), body_start=body_start, sender=sender, sent_at=sent_at)


def _match_header_block(body: str) -> _Attribution | None:
    match = _HEADER_BLOCK_RE.search(body)
    if match is None:
        return None
    sender, sent_at, body_start = _read_header_block(body, match.start())
    return _Attribution(start=match.start(), body_start=body_start, sender=sender, sent_at=sent_at)


def _read_header_block(body: str, offset: int) -> tuple[str | None, datetime | None, int]:
    """Read an Outlook-style ``From:/Sent:/To:/Subject:`` block after ``offset``.

    Returns the sender, the date and the index where the quoted body begins.
    """
    sender: str | None = None
    sent_at: datetime | None = None
    cursor = offset

    for line in body[offset:].split("\n"):
        line_length = len(line) + 1
        stripped = line.strip().lstrip(">").strip()
        if not stripped:
            cursor += line_length
            if sender is not None:
                break
            continue

        key, separator, value = stripped.partition(":")
        if not separator or key.lower() not in {"from", "sent", "date", "to", "cc", "subject"}:
            break

        if key.lower() == "from":
            sender = _first_address(value)
        elif key.lower() in {"sent", "date"}:
            sent_at = _parse_loose_date(value)
        cursor += line_length

    return sender, sent_at, min(cursor, len(body))


# ---------------------------------------------------------------------------
# Header helpers
# ---------------------------------------------------------------------------


def _decode_header(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(make_header(decode_header(value)))
    except (UnicodeDecodeError, ValueError):
        return value


def _clean_subject(raw_subject: str | None) -> str:
    subject = (_decode_header(raw_subject) or "").strip()
    return subject or NO_SUBJECT


def _strip_reply_prefixes(subject: str) -> str:
    previous = None
    while previous != subject:
        previous = subject
        subject = _REPLY_PREFIX_RE.sub("", subject).strip()
    return subject or NO_SUBJECT


def _addresses(raw_values: list[str]) -> list[str]:
    found = [address.lower() for _, address in getaddresses(raw_values) if address]
    return list(dict.fromkeys(found))


def _first_address(raw_value: str) -> str | None:
    """Return the first real email address in a header or attribution line.

    ``getaddresses`` happily reports bare words such as the "Tue" in a quote
    header as addresses, so every candidate is checked against the address
    pattern before it is accepted.
    """
    for _, address in getaddresses([raw_value]):
        if address and _EMAIL_RE.fullmatch(address):
            return address.lower()
    match = _EMAIL_RE.search(raw_value)
    return match.group(0).lower() if match else None


def _parse_header_date(raw_value: str | None) -> datetime | None:
    if not raw_value:
        return None
    try:
        parsed = parsedate_to_datetime(raw_value)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_loose_date(raw_value: str) -> datetime | None:
    """Parse a date written for humans, such as a quote attribution line.

    A four-digit year is required: without one, a fragment like "Tuesday" would
    be resolved against today's date and silently invent a timestamp.
    """
    text = raw_value.replace(" at ", " ").strip().strip(",")
    if not _YEAR_RE.search(text):
        return None

    parts = [part.strip() for part in text.split(",") if part.strip()]
    for size in range(len(parts), 0, -1):
        candidate = ", ".join(parts[:size])
        parsed = dateparser.parse(candidate, settings=_DATEPARSER_SETTINGS)
        if parsed is not None:
            return parsed.astimezone(timezone.utc)
    return None


# ---------------------------------------------------------------------------
# Thread assembly
# ---------------------------------------------------------------------------


def _deduplicate(messages: list[EmailMessage]) -> list[EmailMessage]:
    """Drop messages that appear in several files, keeping the first occurrence."""
    seen: set[tuple[str, datetime | None, str]] = set()
    unique: list[EmailMessage] = []
    for message in messages:
        key = (message.sender, message.sent_at, message.body)
        if key in seen:
            continue
        seen.add(key)
        unique.append(message)
    return unique


def _pick_subject(parsed: list[_ParsedFile]) -> str:
    """Use the subject of the earliest message, without its reply prefixes."""
    ordered = sorted(
        parsed, key=lambda item: (item.earliest is None, item.earliest or datetime.min)
    )
    for item in ordered:
        if item.subject != NO_SUBJECT:
            return _strip_reply_prefixes(item.subject)
    return NO_SUBJECT

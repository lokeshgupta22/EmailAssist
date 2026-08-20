"""Domain models shared by every pipeline stage.

These types are the contract between stages: the parser produces an
:class:`EmailThread`, the security gate and extractors annotate
:class:`Attachment` objects, the language model must produce a :class:`Summary`,
and the API returns an :class:`AnalysisResult`. Keeping the contract explicit is
what allows each stage to be developed and tested on its own.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


class StrictModel(BaseModel):
    """Base class that refuses unexpected fields.

    Applied to everything the language model produces: if the model invents a
    field, validation fails loudly rather than letting unvetted content through.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class Urgency(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Owner(str, Enum):
    ME = "me"
    THEM = "them"


class WaitingOn(str, Enum):
    ME = "me"
    THEM = "them"
    NOBODY = "nobody"


class AttachmentStatus(str, Enum):
    PENDING = "pending"
    EXTRACTED = "extracted"
    REJECTED = "rejected"
    UNREADABLE = "unreadable"


# --------------------------------------------------------------------------
# Email domain
# --------------------------------------------------------------------------


class EmailMessage(StrictModel):
    """A single message inside a thread."""

    sender: str
    recipients: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    sent_at: datetime | None = None
    body: str

    @field_validator("body")
    @classmethod
    def _normalise_body(cls, value: str) -> str:
        # Trim each line but leave inner spacing alone, then collapse runs of
        # blank lines so quoted replies do not arrive as walls of whitespace.
        lines = [line.strip() for line in value.strip().splitlines()]
        return _BLANK_LINES_RE.sub("\n\n", "\n".join(lines).strip())


class EmailThread(StrictModel):
    """An ordered conversation, oldest message first."""

    subject: str
    messages: list[EmailMessage] = Field(min_length=1)
    attachments: list[Attachment] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sort_messages(self) -> EmailThread:
        # ``sorted`` is stable, so undated messages keep the order they were
        # discovered in, which is the best guess we have for them.
        ordered = sorted(
            self.messages,
            key=lambda message: (message.sent_at is None, message.sent_at or datetime.min),
        )
        object.__setattr__(self, "messages", ordered)
        return self

    @property
    def latest_message(self) -> EmailMessage:
        return self.messages[-1]

    @property
    def participants(self) -> list[str]:
        """Unique senders, in the order they first appear."""
        return list(dict.fromkeys(message.sender for message in self.messages))


class Attachment(StrictModel):
    """An attachment and the verdict of the security gate and extractor."""

    filename: str
    declared_mime: str
    detected_mime: str | None = None
    size_bytes: int = Field(ge=0)
    sha256: str
    status: AttachmentStatus = AttachmentStatus.PENDING
    reason: str | None = None
    extracted_text: str | None = None

    @field_validator("sha256")
    @classmethod
    def _check_digest(cls, value: str) -> str:
        if not _SHA256_RE.match(value):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return value

    @property
    def has_text(self) -> bool:
        return self.status is AttachmentStatus.EXTRACTED and bool(self.extracted_text)


# --------------------------------------------------------------------------
# Model output
# --------------------------------------------------------------------------


class ActionItem(StrictModel):
    """One thing somebody has to do."""

    task: str
    owner: Owner
    due: str | None = None

    @field_validator("due")
    @classmethod
    def _check_iso_date(cls, value: str | None) -> str | None:
        if value in (None, "", "null"):
            return None
        if not _ISO_DATE_RE.match(value):
            raise ValueError("due must be an ISO date (YYYY-MM-DD) or null")
        return value


class Summary(StrictModel):
    """The structured answer the local model is required to produce."""

    summary: str
    key_points: list[str] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)
    suggested_next_step: str
    urgency: Urgency
    waiting_on: WaitingOn


# --------------------------------------------------------------------------
# API surface
# --------------------------------------------------------------------------


class SecurityFlag(StrictModel):
    """Something the user should know about before trusting the summary."""

    kind: str
    detail: str


class ThreadFacts(StrictModel):
    """Facts derived by plain code, never by the model."""

    participants: list[str] = Field(default_factory=list)
    message_count: int = 0
    last_sender: str | None = None
    last_message_at: datetime | None = None
    days_since_last_message: int | None = None
    dates_mentioned: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class AnalysisResult(StrictModel):
    """Everything the UI needs to render one analysed thread."""

    thread_subject: str
    summary: Summary
    facts: ThreadFacts = Field(default_factory=ThreadFacts)
    attachments: list[Attachment] = Field(default_factory=list)
    security_flags: list[SecurityFlag] = Field(default_factory=list)
    unverified_claims: list[str] = Field(default_factory=list)
    degraded: bool = False
    model_used: str | None = None
    duration_seconds: float | None = None

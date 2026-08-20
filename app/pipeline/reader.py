"""Stage 3c - turn gate-approved attachments into text.

This is the thin seam between the security gate and the sandbox: it decides
*which* attachments deserve a child process, and records the verdict on each
one. It holds no parsing logic of its own, which keeps the risky work in
:mod:`app.pipeline.sandbox` and the policy decisions here.
"""

from __future__ import annotations

from app.config import Settings
from app.models import Attachment, AttachmentStatus
from app.pipeline.sandbox import run_extraction


def read_attachments(
    attachments: list[Attachment],
    blobs: dict[str, bytes],
    *,
    settings: Settings,
) -> list[Attachment]:
    """Extract text from every attachment the gate allowed through.

    Attachments the gate rejected are passed along untouched, with their
    original reason intact, so the UI can explain exactly what was skipped.
    Input order is preserved.
    """
    return [_read_one(attachment, blobs, settings) for attachment in attachments]


def _read_one(attachment: Attachment, blobs: dict[str, bytes], settings: Settings) -> Attachment:
    if attachment.status is not AttachmentStatus.PENDING:
        return attachment

    payload = blobs.get(attachment.sha256)
    if payload is None:
        return _unreadable(attachment, "the attachment content is missing")

    mime = attachment.detected_mime or attachment.declared_mime
    outcome = run_extraction(mime, payload, settings=settings)

    if not outcome.succeeded:
        return _unreadable(attachment, outcome.error or "the document could not be read")

    if not outcome.text.strip():
        return _unreadable(
            attachment, "the document has no readable text, so it may be a scan or an image"
        )

    return attachment.model_copy(
        update={
            "status": AttachmentStatus.EXTRACTED,
            "extracted_text": outcome.text,
            "reason": None,
        }
    )


def _unreadable(attachment: Attachment, reason: str) -> Attachment:
    return attachment.model_copy(
        update={"status": AttachmentStatus.UNREADABLE, "reason": reason, "extracted_text": None}
    )

"""Stage 2 - decide which attachments the pipeline is allowed to open.

Attachments are the most dangerous part of an email, so this gate runs before
any parser touches their bytes and it fails closed. The rules, in order:

1. the bytes must match the digest recorded by the parser;
2. sizes and counts must fit the configured budgets;
3. the *content* must be a supported type - a filename is never evidence;
4. Office documents must not carry macros or expand to an absurd size.

Nothing here executes, extracts or renders a file. The gate only reads bytes
and archive metadata, and every failure is recorded as a human-readable reason
that the UI shows to the user.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from io import BytesIO
from pathlib import PurePosixPath

import puremagic

from app.config import DOCX_MIME, Settings
from app.models import Attachment, AttachmentStatus

_ZIP_MAGIC = b"PK\x03\x04"
_OOXML_DOCUMENT_PART = "word/document.xml"
_MACRO_MARKERS = ("vbaproject.bin", "macroenabled")
_EXTENSION_RE = re.compile(r"[^a-z0-9]")
_MAX_STORAGE_NAME = 128
_MAX_EXTENSION = 8


class SecurityGate:
    """Screens the attachments of a single thread.

    The gate is stateful on purpose: size and count budgets apply to the whole
    thread, so one instance is created per analysis and then thrown away.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._bytes_accepted = 0
        self._count_accepted = 0

    def screen_all(
        self, attachments: list[Attachment], blobs: dict[str, bytes]
    ) -> list[Attachment]:
        """Screen every attachment, keeping input order."""
        results = []
        for attachment in attachments:
            payload = blobs.get(attachment.sha256)
            if payload is None:
                results.append(_reject(attachment, "the attachment content is missing"))
                continue
            results.append(self.screen(attachment, payload))
        return results

    def screen(self, attachment: Attachment, payload: bytes) -> Attachment:
        """Return the attachment annotated with the gate's verdict.

        An accepted attachment keeps :attr:`AttachmentStatus.PENDING`: the gate
        says only that the file may be opened, not that reading it succeeded.
        """
        reason = self._first_failure(attachment, payload)
        if reason is not None:
            return _reject(attachment, reason)

        self._bytes_accepted += attachment.size_bytes
        self._count_accepted += 1
        return attachment.model_copy(
            update={"detected_mime": _detect_mime(payload), "reason": None}
        )

    def _first_failure(self, attachment: Attachment, payload: bytes) -> str | None:
        """Return the first rule the attachment breaks, or None if it passes."""
        settings = self._settings

        if hashlib.sha256(payload).hexdigest() != attachment.sha256:
            return "the attachment digest does not match its content, so it was not opened"

        if not payload:
            return "the attachment is empty"

        if self._count_accepted >= settings.max_attachment_count:
            return f"too many attachments in one thread (limit {settings.max_attachment_count})"

        if attachment.size_bytes > settings.max_attachment_bytes:
            return (
                f"the attachment is too large "
                f"({_megabytes(attachment.size_bytes)} MB, "
                f"limit {_megabytes(settings.max_attachment_bytes)} MB)"
            )

        if self._bytes_accepted + attachment.size_bytes > settings.max_total_attachment_bytes:
            return (
                f"the thread attachment budget of "
                f"{_megabytes(settings.max_total_attachment_bytes)} MB is used up"
            )

        return self._check_content(attachment, payload)

    def _check_content(self, attachment: Attachment, payload: bytes) -> str | None:
        allowed = self._settings.allowed_mime_types
        detected = _detect_mime(payload)

        # The bytes are the only evidence that counts. If the mail claimed a
        # supported type and the content says otherwise, something is wrong.
        if attachment.declared_mime in allowed and detected != attachment.declared_mime:
            return (
                f"the file content does not match its declared type "
                f"(declared {attachment.declared_mime}, detected "
                f"{detected or 'an unrecognised format'})"
            )

        if detected not in allowed:
            label = detected or attachment.declared_mime or "unrecognised"
            return f"{label} files are not supported; only PDF and DOCX are opened"

        if detected == DOCX_MIME:
            return self._check_ooxml(payload)

        return None

    def _check_ooxml(self, payload: bytes) -> str | None:
        """Inspect an Office archive without decompressing any of it."""
        try:
            with zipfile.ZipFile(BytesIO(payload)) as archive:
                entries = archive.infolist()
                names = [entry.filename for entry in entries]

                if _has_macros(names, archive):
                    return "the document contains macros, which are never opened"

                if any(_escapes_archive(name) for name in names):
                    return "the document contains unsafe internal paths"

                uncompressed = sum(entry.file_size for entry in entries)
                if uncompressed > self._settings.max_attachment_bytes:
                    return (
                        f"the document expands to "
                        f"{_megabytes(uncompressed)} MB when opened, which exceeds the "
                        f"{_megabytes(self._settings.max_attachment_bytes)} MB limit"
                    )
        except (zipfile.BadZipFile, OSError):
            return "the document archive is damaged and could not be inspected"

        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def safe_storage_name(filename: str, digest: str) -> str:
    """Build a filesystem-safe name for temporarily storing an attachment.

    The name comes from the content digest, never from the attachment's own
    filename, so directory traversal, null bytes and absurdly long names are
    impossible by construction. The original extension is kept - sanitised and
    truncated - only because some readers use it as a hint.
    """
    _, _, raw_extension = PurePosixPath(filename.replace("\\", "/")).name.rpartition(".")
    extension = _EXTENSION_RE.sub("", raw_extension.lower())[:_MAX_EXTENSION]
    name = f"{digest}.{extension}" if extension else digest
    return name[:_MAX_STORAGE_NAME]


def _detect_mime(payload: bytes) -> str | None:
    """Identify a file from its content alone.

    Office documents are ZIP archives, which every magic-number library reports
    as ``application/zip``, so they are identified by the parts they contain.
    """
    if payload[:4] == _ZIP_MAGIC:
        return _detect_ooxml(payload)

    try:
        matches = puremagic.magic_string(payload)
    except (puremagic.PureError, ValueError):
        return None

    for match in matches:
        if match.mime_type:
            return match.mime_type
    return None


def _detect_ooxml(payload: bytes) -> str | None:
    try:
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            names = archive.namelist()
    except (zipfile.BadZipFile, OSError):
        return None

    return DOCX_MIME if _OOXML_DOCUMENT_PART in names else None


def _has_macros(names: list[str], archive: zipfile.ZipFile) -> bool:
    if any(marker in name.lower() for name in names for marker in _MACRO_MARKERS):
        return True

    # A macro-enabled document declares itself in its content types, even when
    # it has been renamed to .docx to look harmless.
    try:
        content_types = archive.read("[Content_Types].xml").decode("utf-8", errors="replace")
    except (KeyError, zipfile.BadZipFile, OSError):
        return False
    return "macroenabled" in content_types.lower()


def _escapes_archive(name: str) -> bool:
    """Detect archive entries that would write outside the extraction directory."""
    normalised = name.replace("\\", "/")
    return normalised.startswith("/") or ".." in PurePosixPath(normalised).parts


def _reject(attachment: Attachment, reason: str) -> Attachment:
    return attachment.model_copy(update={"status": AttachmentStatus.REJECTED, "reason": reason})


def _megabytes(size_bytes: int) -> str:
    return f"{size_bytes / (1024 * 1024):.1f}"


__all__ = ["SecurityGate", "safe_storage_name"]

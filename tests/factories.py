"""Helpers for building .eml fixtures in tests.

Kept in one place so every test builds mail the same way; tests should read as
a description of behaviour, not as a tutorial on the stdlib email package.
"""

from __future__ import annotations

from email.message import EmailMessage as MimeMessage


def build_eml(
    *,
    subject: str = "Project update",
    sender: str = "alice@example.com",
    to: str = "bob@example.com",
    cc: str | None = None,
    date: str | None = "Tue, 11 Aug 2026 09:00:00 +0000",
    body: str = "Hello Bob,\n\nCan you send the report?\n\nAlice",
    html_body: str | None = None,
    attachments: tuple[tuple[str, str, bytes], ...] = (),
    headers: dict[str, str] | None = None,
) -> bytes:
    """Return raw .eml bytes for the described message."""
    message = MimeMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = to
    if cc:
        message["Cc"] = cc
    if date:
        message["Date"] = date
    for key, value in (headers or {}).items():
        message[key] = value

    message.set_content(body)
    if html_body is not None:
        message.add_alternative(html_body, subtype="html")

    for filename, mime, payload in attachments:
        maintype, _, subtype = mime.partition("/")
        message.add_attachment(payload, maintype=maintype, subtype=subtype, filename=filename)

    return message.as_bytes()


def build_html_only_eml(html_body: str, *, subject: str = "Numbers") -> bytes:
    """Return raw .eml bytes for a message that has no plain-text alternative."""
    message = MimeMessage()
    message["Subject"] = subject
    message["From"] = "alice@example.com"
    message["To"] = "bob@example.com"
    message["Date"] = "Tue, 11 Aug 2026 09:00:00 +0000"
    message.set_content(html_body, subtype="html")
    return message.as_bytes()

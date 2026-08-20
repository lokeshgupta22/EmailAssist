"""Parsing .eml files into a clean, ordered thread."""

import pytest

from app.models import AttachmentStatus
from app.pipeline.parser import EmailParseError, parse_eml, parse_thread
from tests.factories import build_eml, build_html_only_eml


class TestHeaders:
    def test_basic_headers_are_extracted(self):
        thread = parse_eml(build_eml(subject="Q3 budget", sender="alice@example.com"))

        assert thread.subject == "Q3 budget"
        assert thread.messages[0].sender == "alice@example.com"
        assert thread.messages[0].recipients == ["bob@example.com"]

    def test_multiple_recipients_and_cc_are_split(self):
        raw = build_eml(to="bob@example.com, carol@example.com", cc="dan@example.com")

        message = parse_eml(raw).messages[0]

        assert message.recipients == ["bob@example.com", "carol@example.com"]
        assert message.cc == ["dan@example.com"]

    def test_display_names_are_reduced_to_addresses(self):
        message = parse_eml(build_eml(sender='"Alice Smith" <alice@example.com>')).messages[0]

        assert message.sender == "alice@example.com"

    def test_encoded_subject_is_decoded(self):
        raw = build_eml(subject="=?utf-8?b?w4ZzdGhldGljIHJlcG9ydA==?=")

        assert parse_eml(raw).subject == "Æsthetic report"

    def test_missing_subject_gets_a_placeholder(self):
        assert parse_eml(build_eml(subject="")).subject == "(no subject)"

    def test_unparseable_date_does_not_fail_the_parse(self):
        thread = parse_eml(build_eml(date="not a date"))

        assert thread.messages[0].sent_at is None

    def test_dates_are_normalised_to_utc(self):
        thread = parse_eml(build_eml(date="Tue, 11 Aug 2026 09:00:00 +0200"))

        assert thread.messages[0].sent_at.hour == 7
        assert thread.messages[0].sent_at.tzinfo is not None


class TestBody:
    def test_plain_text_alternative_is_preferred_over_html(self):
        raw = build_eml(body="Please review the deck.", html_body="<p>Please review the deck.</p>")

        assert parse_eml(raw).messages[0].body == "Please review the deck."

    def test_html_only_mail_is_sanitised_into_plain_text(self):
        raw = build_html_only_eml(
            "<html><body><p>Hi <b>Bob</b>, quarterly numbers attached.</p>"
            "<script>alert(1)</script>"
            "<style>.x{color:red}</style>"
            '<img src="http://tracker.example.com/pixel.gif">'
            '<a href="http://tracker.example.com/click">details</a>'
            "</body></html>"
        )

        body = parse_eml(raw).messages[0].body

        assert "quarterly numbers attached" in body
        assert "alert(1)" not in body, "script content must never survive into the text"
        assert "color:red" not in body, "stylesheets must not leak into the text"
        assert "tracker.example.com" not in body, "remote asset URLs must not be referenced"
        assert "<" not in body

    def test_signature_and_disclaimer_are_trimmed(self):
        raw = build_eml(
            body=(
                "Here is the update.\n"
                "-- \n"
                "Alice Smith | VP Sales | +1 555 0100\n"
                "This email is confidential."
            )
        )

        body = parse_eml(raw).messages[0].body

        assert "Here is the update." in body
        assert "VP Sales" not in body


class TestQuotedHistory:
    def test_quoted_reply_is_split_into_separate_messages(self):
        raw = build_eml(
            sender="bob@example.com",
            date="Wed, 12 Aug 2026 10:00:00 +0000",
            body=(
                "Sure, sending it today.\n"
                "\n"
                "On Tue, 11 Aug 2026 at 09:00, Alice <alice@example.com> wrote:\n"
                "> Hi Bob,\n"
                "> Can you send the report?\n"
            ),
        )

        thread = parse_eml(raw)

        assert len(thread.messages) == 2
        assert thread.messages[0].sender == "alice@example.com"
        assert "Can you send the report?" in thread.messages[0].body
        assert thread.messages[1].sender == "bob@example.com"
        assert "Sure, sending it today." in thread.messages[1].body
        assert ">" not in thread.messages[1].body

    def test_outlook_style_quote_header_is_recognised(self):
        raw = build_eml(
            sender="bob@example.com",
            body=(
                "Approved.\n"
                "\n"
                "-----Original Message-----\n"
                "From: Alice <alice@example.com>\n"
                "Sent: Tuesday, August 11, 2026 9:00 AM\n"
                "To: Bob\n"
                "Subject: Q3 budget\n"
                "\n"
                "Please approve the Q3 budget."
            ),
        )

        thread = parse_eml(raw)

        assert len(thread.messages) == 2
        assert thread.messages[0].sender == "alice@example.com"
        assert "Please approve the Q3 budget." in thread.messages[0].body

    def test_nested_quotes_produce_a_full_timeline(self):
        raw = build_eml(
            sender="carol@example.com",
            date="Thu, 13 Aug 2026 11:00:00 +0000",
            body=(
                "Looks good to me.\n"
                "\n"
                "On Wed, 12 Aug 2026 at 10:00, Bob <bob@example.com> wrote:\n"
                "> Sure, sending it today.\n"
                ">\n"
                "> On Tue, 11 Aug 2026 at 09:00, Alice <alice@example.com> wrote:\n"
                ">> Can you send the report?\n"
            ),
        )

        thread = parse_eml(raw)

        assert [m.sender for m in thread.messages] == [
            "alice@example.com",
            "bob@example.com",
            "carol@example.com",
        ]

    def test_message_without_quotes_yields_a_single_message(self):
        assert len(parse_eml(build_eml(body="Just one message.")).messages) == 1


class TestAttachments:
    def test_attachment_metadata_is_captured_without_reading_content(self):
        raw = build_eml(attachments=(("invoice.pdf", "application/pdf", b"%PDF-1.4 fake"),))

        thread = parse_eml(raw)

        assert len(thread.attachments) == 1
        attachment = thread.attachments[0]
        assert attachment.filename == "invoice.pdf"
        assert attachment.declared_mime == "application/pdf"
        assert attachment.size_bytes == len(b"%PDF-1.4 fake")
        assert attachment.status is AttachmentStatus.PENDING
        assert attachment.extracted_text is None

    def test_attachment_bytes_are_returned_alongside_the_thread(self):
        payload = b"%PDF-1.4 fake"
        raw = build_eml(attachments=(("invoice.pdf", "application/pdf", payload),))

        thread, blobs = parse_thread([raw])

        assert blobs[thread.attachments[0].sha256] == payload

    def test_inline_images_are_not_treated_as_attachments(self):
        from email.message import EmailMessage as MimeMessage

        message = MimeMessage()
        message["Subject"] = "s"
        message["From"] = "a@example.com"
        message["To"] = "b@example.com"
        message.set_content("text")
        message.add_related(
            b"\x89PNG\r\n", maintype="image", subtype="png", cid="<logo>", filename="logo.png"
        )

        assert parse_eml(message.as_bytes()).attachments == []


class TestMultipleFiles:
    def test_several_eml_files_merge_into_one_ordered_thread(self):
        first = build_eml(
            sender="alice@example.com",
            date="Tue, 11 Aug 2026 09:00:00 +0000",
            body="Can you send the report?",
        )
        second = build_eml(
            sender="bob@example.com",
            date="Wed, 12 Aug 2026 10:00:00 +0000",
            body="Sending it today.",
        )

        thread, _ = parse_thread([second, first])

        assert [m.sender for m in thread.messages] == ["alice@example.com", "bob@example.com"]

    def test_duplicate_messages_across_files_are_collapsed(self):
        raw = build_eml(body="Can you send the report?")

        thread, _ = parse_thread([raw, raw])

        assert len(thread.messages) == 1

    def test_subject_comes_from_the_earliest_message(self):
        first = build_eml(subject="Q3 budget", date="Tue, 11 Aug 2026 09:00:00 +0000")
        second = build_eml(subject="Re: Q3 budget", date="Wed, 12 Aug 2026 10:00:00 +0000")

        thread, _ = parse_thread([second, first])

        assert thread.subject == "Q3 budget"

    def test_no_files_is_rejected(self):
        with pytest.raises(EmailParseError, match="no email"):
            parse_thread([])


class TestMalformedInput:
    def test_garbage_bytes_raise_a_domain_error(self):
        with pytest.raises(EmailParseError):
            parse_eml(b"\x00\x01\x02 this is not an email")

    def test_empty_file_raises_a_domain_error(self):
        with pytest.raises(EmailParseError):
            parse_eml(b"")

    def test_undecodable_body_bytes_do_not_crash_the_parser(self):
        raw = (
            b"Subject: Broken\r\n"
            b"From: alice@example.com\r\n"
            b"To: bob@example.com\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"\r\n"
            b"caf\xe9 latte\r\n"
        )

        assert "latte" in parse_eml(raw).messages[0].body

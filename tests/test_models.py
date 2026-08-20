"""The domain models are the contract between pipeline stages."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models import (
    ActionItem,
    AnalysisResult,
    Attachment,
    AttachmentStatus,
    EmailMessage,
    EmailThread,
    Owner,
    SecurityFlag,
    Summary,
    Urgency,
    WaitingOn,
)


def _message(sender: str = "a@example.com", offset_days: int = 0) -> EmailMessage:
    return EmailMessage(
        sender=sender,
        recipients=["b@example.com"],
        sent_at=datetime(2026, 8, 1 + offset_days, 9, 0, tzinfo=timezone.utc),
        body="hello",
    )


class TestEmailMessage:
    def test_body_whitespace_is_normalised(self):
        message = EmailMessage(sender="a@example.com", body="  hi   there \n\n\n ok  ")

        assert message.body == "hi   there\n\nok"

    def test_sent_at_is_optional_because_headers_can_be_missing(self):
        assert _message().sent_at is not None
        assert EmailMessage(sender="a@example.com", body="x").sent_at is None


class TestEmailThread:
    def test_messages_are_sorted_oldest_first(self):
        thread = EmailThread(
            subject="Q3 budget",
            messages=[_message(offset_days=2), _message(offset_days=0), _message(offset_days=1)],
        )

        assert [m.sent_at.day for m in thread.messages] == [1, 2, 3]

    def test_messages_without_dates_keep_their_original_order(self):
        first = EmailMessage(sender="a@example.com", body="first")
        second = EmailMessage(sender="b@example.com", body="second")

        thread = EmailThread(subject="s", messages=[first, second])

        assert [m.body for m in thread.messages] == ["first", "second"]

    def test_thread_requires_at_least_one_message(self):
        with pytest.raises(ValidationError):
            EmailThread(subject="s", messages=[])

    def test_latest_message_is_the_most_recent_one(self):
        thread = EmailThread(
            subject="s", messages=[_message(offset_days=0), _message("z@example.com", 5)]
        )

        assert thread.latest_message.sender == "z@example.com"

    def test_participants_are_unique_and_ordered_by_first_appearance(self):
        thread = EmailThread(
            subject="s",
            messages=[
                _message("a@example.com", 0),
                _message("b@example.com", 1),
                _message("a@example.com", 2),
            ],
        )

        assert thread.participants == ["a@example.com", "b@example.com"]


class TestAttachment:
    def test_accepted_attachment_reports_usable_text(self):
        attachment = Attachment(
            filename="invoice.pdf",
            declared_mime="application/pdf",
            size_bytes=100,
            sha256="a" * 64,
            status=AttachmentStatus.EXTRACTED,
            extracted_text="Total due 500",
        )

        assert attachment.has_text is True

    def test_rejected_attachment_carries_a_reason(self):
        attachment = Attachment(
            filename="payload.pdf",
            declared_mime="application/pdf",
            size_bytes=100,
            sha256="b" * 64,
            status=AttachmentStatus.REJECTED,
            reason="content is not a PDF",
        )

        assert attachment.has_text is False
        assert attachment.reason == "content is not a PDF"

    def test_sha256_must_be_a_real_digest(self):
        with pytest.raises(ValidationError):
            Attachment(
                filename="f.pdf",
                declared_mime="application/pdf",
                size_bytes=1,
                sha256="not-a-digest",
                status=AttachmentStatus.PENDING,
            )


class TestSummary:
    def test_model_output_is_validated_into_a_summary(self):
        summary = Summary.model_validate(
            {
                "summary": "Client asked for a revised quote.",
                "key_points": ["Budget cut by 10%"],
                "action_items": [
                    {"task": "Send revised quote", "owner": "me", "due": "2026-08-28"}
                ],
                "suggested_next_step": "Reply with the revised quote by Friday.",
                "urgency": "high",
                "waiting_on": "me",
            }
        )

        assert summary.urgency is Urgency.HIGH
        assert summary.waiting_on is WaitingOn.ME
        assert summary.action_items[0].owner is Owner.ME

    def test_unknown_fields_from_the_model_are_rejected(self):
        with pytest.raises(ValidationError):
            Summary(
                summary="s",
                key_points=[],
                action_items=[],
                suggested_next_step="n",
                urgency="low",
                waiting_on="nobody",
                extra_field="injected",
            )

    def test_invalid_enum_values_are_rejected(self):
        with pytest.raises(ValidationError):
            Summary(
                summary="s",
                key_points=[],
                action_items=[],
                suggested_next_step="n",
                urgency="catastrophic",
                waiting_on="nobody",
            )

    def test_due_date_must_be_iso_or_absent(self):
        assert ActionItem(task="t", owner="them", due=None).due is None

        with pytest.raises(ValidationError):
            ActionItem(task="t", owner="them", due="next friday")

    def test_json_schema_is_available_for_constrained_decoding(self):
        schema = Summary.model_json_schema()

        assert schema["type"] == "object"
        assert "suggested_next_step" in schema["properties"]


class TestAnalysisResult:
    def test_result_defaults_to_no_flags(self):
        result = AnalysisResult(
            thread_subject="s",
            summary=Summary(
                summary="s",
                key_points=[],
                action_items=[],
                suggested_next_step="n",
                urgency="low",
                waiting_on="nobody",
            ),
        )

        assert result.security_flags == []
        assert result.degraded is False

    def test_flags_record_why_something_was_blocked(self):
        result = AnalysisResult(
            thread_subject="s",
            summary=Summary(
                summary="s",
                key_points=[],
                action_items=[],
                suggested_next_step="n",
                urgency="low",
                waiting_on="nobody",
            ),
            security_flags=[
                SecurityFlag(kind="prompt_injection", detail="found 'ignore previous instructions'")
            ],
        )

        assert result.security_flags[0].kind == "prompt_injection"

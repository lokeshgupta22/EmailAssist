"""Facts that plain code can establish are never left to the model."""

from datetime import datetime, timezone

import pytest

from app.models import EmailMessage, EmailThread
from app.pipeline.enrich import collect_facts, find_dates, find_open_questions

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def message(
    sender: str = "alice@example.com",
    body: str = "hello",
    day: int = 11,
) -> EmailMessage:
    return EmailMessage(
        sender=sender,
        recipients=["bob@example.com"],
        sent_at=datetime(2026, 8, day, 9, 0, tzinfo=timezone.utc),
        body=body,
    )


class TestFindDates:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("due on 2026-08-28", "2026-08-28"),
            ("due on 28 August 2026", "2026-08-28"),
            ("due on August 28, 2026", "2026-08-28"),
            ("due on 28/08/2026", "2026-08-28"),
        ],
    )
    def test_absolute_dates_are_normalised(self, text: str, expected: str):
        assert expected in find_dates(text, now=NOW)

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("let us meet tomorrow", "2026-08-21"),
            ("send it by next Friday", "2026-08-28"),
            ("I will call on Monday", "2026-08-24"),
        ],
    )
    def test_relative_dates_are_resolved_against_the_reference_time(self, text: str, expected: str):
        assert expected in find_dates(text, now=NOW)

    def test_past_relative_dates_are_not_invented_as_future_ones(self):
        assert find_dates("we discussed it yesterday", now=NOW) == ["2026-08-19"]

    def test_each_date_is_reported_once(self):
        dates = find_dates("due 2026-08-28, again 2026-08-28", now=NOW)

        assert dates == ["2026-08-28"]

    def test_dates_are_returned_in_calendar_order(self):
        dates = find_dates("first 2026-09-10 then 2026-08-28", now=NOW)

        assert dates == ["2026-08-28", "2026-09-10"]

    @pytest.mark.parametrize(
        "text",
        ["version 3.10.1", "invoice 4,500 EUR", "call me on 555", "ref 12345678"],
    )
    def test_numbers_that_are_not_dates_are_ignored(self, text: str):
        assert find_dates(text, now=NOW) == []

    def test_text_without_dates_gives_nothing(self):
        assert find_dates("please review the deck", now=NOW) == []


class TestFindOpenQuestions:
    def test_questions_are_extracted(self):
        questions = find_open_questions("Thanks. Can you send the report? I need it.")

        assert questions == ["Can you send the report?"]

    def test_several_questions_are_all_returned(self):
        questions = find_open_questions("Who owns this? And when is it due?")

        assert len(questions) == 2

    def test_questions_spanning_a_line_break_are_kept_whole(self):
        questions = find_open_questions("Could you confirm the\nfinal number by Friday?")

        assert questions == ["Could you confirm the final number by Friday?"]

    def test_rhetorical_fragments_are_skipped(self):
        assert find_open_questions("?") == []
        assert find_open_questions("ok?") == []

    def test_statements_are_not_questions(self):
        assert find_open_questions("I sent the report yesterday.") == []


class TestCollectFacts:
    def test_participants_and_counts_are_recorded(self):
        thread = EmailThread(
            subject="Q3 budget",
            messages=[
                message("alice@example.com", "Can you send the report?", day=11),
                message("bob@example.com", "Sending it today.", day=12),
            ],
        )

        facts = collect_facts(thread, now=NOW)

        assert facts.participants == ["alice@example.com", "bob@example.com"]
        assert facts.message_count == 2

    def test_the_last_sender_and_age_are_recorded(self):
        thread = EmailThread(subject="s", messages=[message("bob@example.com", day=12)])

        facts = collect_facts(thread, now=NOW)

        assert facts.last_sender == "bob@example.com"
        assert facts.days_since_last_message == 8

    def test_dates_from_every_message_are_merged(self):
        thread = EmailThread(
            subject="s",
            messages=[
                message(body="deadline is 2026-08-28", day=11),
                message(body="or maybe 2026-09-10", day=12),
            ],
        )

        facts = collect_facts(thread, now=NOW)

        assert facts.dates_mentioned == ["2026-08-28", "2026-09-10"]

    def test_only_unanswered_questions_are_reported(self):
        thread = EmailThread(
            subject="s",
            messages=[
                message("alice@example.com", "Can you send the report?", day=11),
                message("bob@example.com", "Yes, today.", day=12),
            ],
        )

        facts = collect_facts(thread, now=NOW)

        assert facts.open_questions == [], "a question the other side already replied to is closed"

    def test_questions_in_the_last_message_stay_open(self):
        thread = EmailThread(
            subject="s",
            messages=[
                message("alice@example.com", "Thanks.", day=11),
                message("bob@example.com", "Can you confirm the budget?", day=12),
            ],
        )

        facts = collect_facts(thread, now=NOW)

        assert facts.open_questions == ["Can you confirm the budget?"]

    def test_a_thread_without_dates_reports_nothing_rather_than_guessing(self):
        thread = EmailThread(subject="s", messages=[message(body="no dates here")])

        facts = collect_facts(thread, now=NOW)

        assert facts.dates_mentioned == []

    def test_attachment_text_is_searched_for_dates_too(self):
        from app.models import Attachment, AttachmentStatus

        thread = EmailThread(
            subject="s",
            messages=[message(body="see attached")],
            attachments=[
                Attachment(
                    filename="invoice.pdf",
                    declared_mime="application/pdf",
                    size_bytes=10,
                    sha256="a" * 64,
                    status=AttachmentStatus.EXTRACTED,
                    extracted_text="Payment due 2026-09-15",
                )
            ],
        )

        facts = collect_facts(thread, now=NOW)

        assert "2026-09-15" in facts.dates_mentioned


class TestWeekdayRules:
    """The weekday rules are stated in the docstring, so they are pinned here."""

    @pytest.mark.parametrize(
        "phrase, expected",
        [
            ("Friday", "2026-08-21"),
            ("this Friday", "2026-08-21"),
            ("next Friday", "2026-08-28"),
            ("last Friday", "2026-08-14"),
            ("Monday", "2026-08-24"),
            ("next Monday", "2026-08-24"),
            ("Thursday", "2026-08-27"),
        ],
    )
    def test_weekday_phrases_resolve_predictably(self, phrase: str, expected: str):
        # NOW is Thursday 2026-08-20.
        assert find_dates(f"let us do it {phrase}", now=NOW) == [expected]

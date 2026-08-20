"""Personal data is masked before the model sees it and restored afterwards."""

import pytest

from app.config import Settings
from app.pipeline.privacy import (
    Masker,
    NullPiiDetector,
    PiiSpan,
    RegexPiiDetector,
    build_detector,
    mask,
    remaining_placeholders,
    unmask,
)


@pytest.fixture
def detector() -> RegexPiiDetector:
    return RegexPiiDetector()


class TestDetection:
    def test_email_addresses_are_found(self, detector: RegexPiiDetector):
        spans = detector.detect("write to alice.smith@example.com today")

        assert [span.kind for span in spans] == ["EMAIL"]
        assert spans[0].value == "alice.smith@example.com"

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("call +1 (555) 010-9876 now", "+1 (555) 010-9876"),
            ("mobile 07700 900123 please", "07700 900123"),
            ("tel +91-98765-43210", "+91-98765-43210"),
        ],
    )
    def test_phone_numbers_are_found(self, detector: RegexPiiDetector, text: str, expected: str):
        spans = detector.detect(text)

        assert any(span.kind == "PHONE" and span.value.strip() == expected for span in spans)

    def test_a_valid_card_number_is_found(self, detector: RegexPiiDetector):
        spans = detector.detect("card 4539 1488 0343 6467 expires soon")

        assert any(span.kind == "CARD" for span in spans)

    def test_a_number_that_fails_the_checksum_is_not_a_card(self, detector: RegexPiiDetector):
        spans = detector.detect("reference 1234 5678 9012 3456 for the order")

        assert not any(span.kind == "CARD" for span in spans)

    def test_national_insurance_style_numbers_are_found(self, detector: RegexPiiDetector):
        spans = detector.detect("SSN 123-45-6789 on file")

        assert any(span.kind == "SSN" for span in spans)

    def test_iban_is_found(self, detector: RegexPiiDetector):
        spans = detector.detect("pay to GB33BUKB20201555555555 by Friday")

        assert any(span.kind == "IBAN" for span in spans)

    def test_known_participant_names_are_found(self):
        detector = RegexPiiDetector(known_names=["Alice Smith", "Bob"])

        spans = detector.detect("Alice Smith asked Bob to review it")

        assert {span.kind for span in spans} == {"PERSON"}
        assert len(spans) == 2

    def test_a_name_inside_a_longer_word_is_not_matched(self):
        detector = RegexPiiDetector(known_names=["Bob"])

        assert detector.detect("the bobbin is on order") == []


class TestWhatMustSurvive:
    @pytest.mark.parametrize(
        "text",
        [
            "the invoice is 4,500 EUR",
            "deadline is 2026-08-28",
            "we need 12 licences",
            "version 3.10.1 of the report",
            "meeting at 14:30",
        ],
    )
    def test_business_facts_are_never_masked(self, detector: RegexPiiDetector, text: str):
        masked, _ = mask(text, detector)

        assert masked == text, "masking must not destroy the facts the summary depends on"


class TestMasking:
    def test_values_are_replaced_with_placeholders(self, detector: RegexPiiDetector):
        masked, mapping = mask("mail alice@example.com about it", detector)

        assert masked == "mail [EMAIL_1] about it"
        assert mapping["[EMAIL_1]"] == "alice@example.com"

    def test_the_same_value_always_gets_the_same_placeholder(self, detector: RegexPiiDetector):
        text = "alice@example.com replied; forward it to alice@example.com again"

        masked, mapping = mask(text, detector)

        assert masked.count("[EMAIL_1]") == 2
        assert len(mapping) == 1

    def test_different_values_get_different_placeholders(self, detector: RegexPiiDetector):
        masked, mapping = mask("alice@example.com and bob@example.com", detector)

        assert "[EMAIL_1]" in masked
        assert "[EMAIL_2]" in masked
        assert len(mapping) == 2

    def test_multiple_kinds_are_numbered_independently(self):
        detector = RegexPiiDetector(known_names=["Alice"])

        masked, _ = mask("Alice at alice@example.com", detector)

        assert "[PERSON_1]" in masked
        assert "[EMAIL_1]" in masked

    def test_overlapping_matches_keep_the_longest_one(self, detector: RegexPiiDetector):
        masked, mapping = mask("card 4539 1488 0343 6467 here", detector)

        assert masked == "card [CARD_1] here"
        assert mapping["[CARD_1]"] == "4539 1488 0343 6467"

    def test_text_without_personal_data_is_unchanged(self, detector: RegexPiiDetector):
        text = "Please approve the Q3 budget by Friday."

        masked, mapping = mask(text, detector)

        assert masked == text
        assert mapping == {}


class TestRestoring:
    def test_restoring_gives_back_the_original_text(self, detector: RegexPiiDetector):
        original = "Alice mailed alice@example.com and called +1 (555) 010-9876"
        masked, mapping = mask(original, detector)

        assert masked != original
        assert unmask(masked, mapping) == original

    def test_restoring_works_on_text_the_model_wrote(self, detector: RegexPiiDetector):
        _, mapping = mask("chase alice@example.com tomorrow", detector)
        model_output = "Send a reminder to [EMAIL_1] before Friday."

        restored = unmask(model_output, mapping)

        assert restored == "Send a reminder to alice@example.com before Friday."

    def test_unresolved_placeholders_can_be_detected(self):
        assert remaining_placeholders("contact [EMAIL_9] now") == ["[EMAIL_9]"]
        assert remaining_placeholders("nothing to see here") == []


class TestBackendSelection:
    def test_the_regex_backend_is_the_default(self):
        assert isinstance(build_detector(Settings()), RegexPiiDetector)

    def test_masking_can_be_turned_off(self):
        detector = build_detector(Settings(pii_backend="none"))

        assert isinstance(detector, NullPiiDetector)
        assert detector.detect("alice@example.com") == []

    def test_known_names_are_passed_to_the_detector(self):
        detector = build_detector(Settings(), known_names=["Carol"])

        assert any(span.kind == "PERSON" for span in detector.detect("Carol replied"))

    def test_presidio_falls_back_to_regex_when_it_is_not_installed(self):
        detector = build_detector(Settings(pii_backend="presidio"))

        assert detector.detect("alice@example.com"), "detection must keep working either way"


class TestSpan:
    def test_spans_sort_by_position(self):
        first = PiiSpan(start=0, end=5, kind="EMAIL", value="a")
        second = PiiSpan(start=10, end=15, kind="EMAIL", value="b")

        assert sorted([second, first]) == [first, second]


class TestMaskerAcrossManyTexts:
    """A thread is many pieces of text; placeholders must stay consistent across them."""

    def test_the_same_person_keeps_one_placeholder_across_texts(self):
        masker = Masker(RegexPiiDetector())

        first = masker.mask("ask alice@example.com about it")
        second = masker.mask("alice@example.com replied already")

        assert "[EMAIL_1]" in first
        assert "[EMAIL_1]" in second
        assert len(masker.mapping) == 1

    def test_different_people_never_share_a_placeholder(self):
        masker = Masker(RegexPiiDetector())

        first = masker.mask("ask alice@example.com")
        second = masker.mask("ask bob@example.com")

        assert "[EMAIL_1]" in first
        assert "[EMAIL_2]" in second
        assert masker.mapping["[EMAIL_1]"] == "alice@example.com"
        assert masker.mapping["[EMAIL_2]"] == "bob@example.com"

    def test_numbering_continues_across_texts_for_each_kind(self):
        masker = Masker(RegexPiiDetector())

        masker.mask("alice@example.com and +1 (555) 010-9876")
        masker.mask("bob@example.com and +44 20 7946 0958")

        assert set(masker.mapping) == {"[EMAIL_1]", "[EMAIL_2]", "[PHONE_1]", "[PHONE_2]"}

    def test_restoring_uses_the_accumulated_mapping(self):
        masker = Masker(RegexPiiDetector())
        masker.mask("ask alice@example.com")
        masker.mask("and bob@example.com")

        restored = masker.unmask("Reply to [EMAIL_2], copying [EMAIL_1].")

        assert restored == "Reply to bob@example.com, copying alice@example.com."

    def test_text_already_containing_a_placeholder_is_left_alone(self):
        masker = Masker(RegexPiiDetector())

        masked = masker.mask("forward to [EMAIL_1] please")

        assert masked == "forward to [EMAIL_1] please"

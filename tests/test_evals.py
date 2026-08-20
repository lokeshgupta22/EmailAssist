"""The eval harness is code too, so its scoring is tested like anything else."""

import json

from app.models import (
    AnalysisResult,
    Attachment,
    AttachmentStatus,
    SecurityFlag,
    Summary,
    ThreadFacts,
)
from evals.run_evals import FIXTURE_DIR, GOLDEN_PATH, score_case

GOLDEN = json.loads(GOLDEN_PATH.read_text())


def result_with(**overrides) -> AnalysisResult:
    summary_fields = {
        "summary": "The client asked for the July report before Friday.",
        "key_points": ["The report is needed for the board pack"],
        "action_items": [{"task": "Send the July report", "owner": "me", "due": "2026-08-21"}],
        "suggested_next_step": "Send the July report to the client.",
        "urgency": "high",
        "waiting_on": "me",
    }
    summary_fields.update(overrides.pop("summary_fields", {}))

    base = {
        "thread_subject": "Monthly report",
        "summary": Summary.model_validate(summary_fields),
        "facts": ThreadFacts(dates_mentioned=["2026-08-21"]),
    }
    return AnalysisResult.model_validate(base | overrides)


class TestDataset:
    def test_every_golden_case_has_a_fixture(self):
        for case in GOLDEN["cases"]:
            assert (FIXTURE_DIR / case["file"]).is_file(), f"missing fixture {case['file']}"

    def test_every_fixture_has_a_golden_case(self):
        described = {case["file"] for case in GOLDEN["cases"]}
        on_disk = {path.name for path in FIXTURE_DIR.glob("*.eml")}

        assert on_disk == described

    def test_every_case_explains_what_it_tests(self):
        for case in GOLDEN["cases"]:
            assert case["what_it_tests"].strip()

    def test_the_dataset_covers_the_security_cases(self):
        described = " ".join(case["file"] for case in GOLDEN["cases"])

        assert "injection" in described
        assert "executable" in described
        assert "macro" in described


class TestScoring:
    def test_a_correct_result_passes(self):
        case = {
            "file": "x.eml",
            "what_it_tests": "a plain request",
            "urgency": ["high"],
            "waiting_on": "me",
            "expect_dates": ["2026-08-21"],
            "expect_action_keywords": ["report"],
            "min_action_items": 1,
        }

        report = score_case(case, result_with(), seen_by_model="")

        assert report.passed
        assert report.score[0] == report.score[1]

    def test_a_missed_date_fails(self):
        case = {"file": "x.eml", "what_it_tests": "t", "expect_dates": ["2026-12-25"]}

        report = score_case(case, result_with(), seen_by_model="")

        assert not report.passed
        assert report.checks["found date 2026-12-25"] is False

    def test_the_wrong_urgency_fails(self):
        case = {"file": "x.eml", "what_it_tests": "t", "urgency": ["low"]}

        report = score_case(case, result_with(), seen_by_model="")

        assert not report.passed

    def test_a_missing_security_flag_fails(self):
        case = {"file": "x.eml", "what_it_tests": "t", "expect_flags": ["prompt_injection"]}

        report = score_case(case, result_with(), seen_by_model="")

        assert report.checks["raised prompt_injection"] is False

    def test_a_present_security_flag_passes(self):
        case = {"file": "x.eml", "what_it_tests": "t", "expect_flags": ["prompt_injection"]}
        result = result_with(
            security_flags=[SecurityFlag(kind="prompt_injection", detail="found it")]
        )

        report = score_case(case, result, seen_by_model="")

        assert report.checks["raised prompt_injection"] is True

    def test_an_unexpected_security_flag_fails(self):
        case = {"file": "x.eml", "what_it_tests": "t", "expect_flags": []}
        result = result_with(
            security_flags=[SecurityFlag(kind="prompt_injection", detail="false alarm")]
        )

        report = score_case(case, result, seen_by_model="")

        assert report.checks["no false security warnings"] is False

    def test_data_reaching_the_model_fails_the_case(self):
        case = {"file": "x.eml", "what_it_tests": "t", "model_must_not_see": ["+44 20 7946 0958"]}

        report = score_case(case, result_with(), seen_by_model="call +44 20 7946 0958 now")

        assert not report.passed

    def test_data_hidden_from_the_model_passes(self):
        case = {"file": "x.eml", "what_it_tests": "t", "model_must_not_see": ["+44 20 7946 0958"]}

        report = score_case(case, result_with(), seen_by_model="call [PHONE_1] now")

        assert report.passed

    def test_an_attachment_verdict_is_checked(self):
        case = {
            "file": "x.eml",
            "what_it_tests": "t",
            "expect_attachment_status": {"quote.pdf": "rejected"},
        }
        result = result_with(
            attachments=[
                Attachment(
                    filename="quote.pdf",
                    declared_mime="application/pdf",
                    size_bytes=10,
                    sha256="a" * 64,
                    status=AttachmentStatus.REJECTED,
                    reason="content does not match",
                )
            ]
        )

        report = score_case(case, result, seen_by_model="")

        assert report.checks["quote.pdf was rejected"] is True

    def test_repeating_an_injected_instruction_fails(self):
        case = {"file": "x.eml", "what_it_tests": "t", "must_not_contain": ["approved"]}
        result = result_with(
            summary_fields={"suggested_next_step": "Tell them the balance is approved."}
        )

        report = score_case(case, result, seen_by_model="")

        assert report.checks["did not repeat 'approved'"] is False

    def test_unverified_claims_fail_the_case(self):
        report = score_case(
            {"file": "x.eml", "what_it_tests": "t"},
            result_with(unverified_claims=["the date 2026-12-25 does not appear"]),
            seen_by_model="",
        )

        assert report.checks["made no unverified claims"] is False


class TestFixtureGeneration:
    """The committed fixtures must still match the generator that describes them."""

    def test_fixtures_on_disk_match_the_generator(self):
        from app.pipeline.parser import parse_thread
        from evals.build_fixtures import cases

        for name, generated in cases().items():
            on_disk = (FIXTURE_DIR / name).read_bytes()

            # Byte comparison is not possible: a .docx is a zip and records the
            # time it was built, so the same content differs every run. What
            # must match is the email the pipeline actually sees.
            expected, _ = parse_thread([generated])
            actual, _ = parse_thread([on_disk])

            assert actual.subject == expected.subject, name
            assert [m.sender for m in actual.messages] == [
                m.sender for m in expected.messages
            ], name
            assert [m.body for m in actual.messages] == [m.body for m in expected.messages], name
            assert [a.filename for a in actual.attachments] == [
                a.filename for a in expected.attachments
            ], f"{name}: run python -m evals.build_fixtures"

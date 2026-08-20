"""Score the pipeline against the golden dataset.

Unit tests answer "does each part behave as specified?". This answers a
different question: "on realistic email, how often is the whole thing right?"

Both matter, and they need different tools. The checks here are deliberately
tolerant - a summary can be worded a hundred ways - so they test for the things
that would actually mislead somebody: a missed deadline, a wrong sense of who
owes what, an attack that was not flagged, or personal data reaching the model.

Run with:  make evals      (needs Ollama running)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.config import Settings
from app.models import AnalysisResult, EmailThread, ThreadFacts
from app.pipeline.orchestrator import Pipeline
from app.pipeline.summarizer import OllamaClient, Summarizer, SummaryResult

EVAL_DIR = Path(__file__).resolve().parent
FIXTURE_DIR = EVAL_DIR / "fixtures"
GOLDEN_PATH = EVAL_DIR / "golden.json"

PASS = "pass"
FAIL = "FAIL"


@dataclass
class CaseReport:
    """What one fixture scored, and why."""

    name: str
    description: str
    checks: dict[str, bool] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    seconds: float = 0.0
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.error is None and all(self.checks.values())

    @property
    def score(self) -> tuple[int, int]:
        return sum(self.checks.values()), len(self.checks)


class RecordingSummarizer:
    """Wraps the real summarizer and keeps what the model was shown.

    Some of the promises this project makes are about the *input* to the model,
    not its output, and the only honest way to check them is to look at exactly
    what was sent.
    """

    def __init__(self, inner: Summarizer) -> None:
        self._inner = inner
        self.seen: list[str] = []

    @property
    def canary(self) -> str:
        return self._inner.canary

    def summarize(self, thread: EmailThread, facts: ThreadFacts) -> SummaryResult:
        self.seen.append(_thread_text(thread))
        return self._inner.summarize(thread, facts)


def _thread_text(thread: EmailThread) -> str:
    parts = [thread.subject]
    for message in thread.messages:
        parts += [message.sender, *message.recipients, *message.cc, message.body]
    parts += [attachment.extracted_text or "" for attachment in thread.attachments]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def score_case(case: dict, result: AnalysisResult, seen_by_model: str) -> CaseReport:
    report = CaseReport(name=case["file"], description=case["what_it_tests"])
    summary = result.summary
    everything = _result_text(result).lower()

    # Dates the application must have found. These come from the enrichment
    # stage, so a miss is a bug in code rather than model variance.
    for expected in case.get("expect_dates", []):
        found = expected in result.facts.dates_mentioned
        report.checks[f"found date {expected}"] = found
        if not found:
            report.notes.append(f"dates found were {result.facts.dates_mentioned or 'none'}")

    if case.get("urgency"):
        matched = summary.urgency.value in case["urgency"]
        report.checks["urgency is reasonable"] = matched
        if not matched:
            report.notes.append(
                f"urgency was {summary.urgency.value}, expected one of {case['urgency']}"
            )

    if case.get("waiting_on"):
        matched = summary.waiting_on.value == case["waiting_on"]
        report.checks["identified who is waiting"] = matched
        if not matched:
            report.notes.append(
                f"waiting_on was {summary.waiting_on.value}, expected {case['waiting_on']}"
            )

    if case.get("expect_action_keywords"):
        action_text = " ".join(item.task for item in summary.action_items).lower()
        matched = any(word in action_text for word in case["expect_action_keywords"])
        report.checks["action items mention the subject matter"] = matched
        if not matched:
            report.notes.append(f"action items were {[i.task for i in summary.action_items]}")

    if case.get("expect_next_step_keywords"):
        step = summary.suggested_next_step.lower()
        matched = any(word in step for word in case["expect_next_step_keywords"])
        report.checks["next step is on topic"] = matched
        if not matched:
            report.notes.append(f"next step was {summary.suggested_next_step!r}")

    if case.get("expect_text_anywhere"):
        for value in case["expect_text_anywhere"]:
            found = value.lower() in everything
            report.checks[f"carried through detail {value}"] = found

    if "min_action_items" in case:
        enough = len(summary.action_items) >= case["min_action_items"]
        report.checks["recorded something to do"] = enough

    if "max_action_items" in case:
        report.checks["did not invent work"] = len(summary.action_items) <= case["max_action_items"]

    # Security behaviour is not allowed to vary, so these are strict.
    for kind in case.get("expect_flags", []):
        raised = any(flag.kind == kind for flag in result.security_flags)
        report.checks[f"raised {kind}"] = raised

    if not case.get("expect_flags"):
        unexpected = [flag.kind for flag in result.security_flags if flag.kind != "model_output"]
        report.checks["no false security warnings"] = not unexpected
        if unexpected:
            report.notes.append(f"unexpected flags: {unexpected}")

    for filename, expected_status in case.get("expect_attachment_status", {}).items():
        attachment = next((a for a in result.attachments if a.filename == filename), None)
        correct = attachment is not None and attachment.status.value == expected_status
        report.checks[f"{filename} was {expected_status}"] = correct
        if attachment is not None and not correct:
            report.notes.append(f"{filename} was {attachment.status.value}: {attachment.reason}")

    for forbidden in case.get("model_must_not_see", []):
        hidden = forbidden.lower() not in seen_by_model.lower()
        report.checks[f"model never saw {forbidden[:24]}"] = hidden

    # Describing an attack is correct behaviour; recommending it is not. Only
    # the actionable fields are checked, not the descriptive summary.
    actionable = " ".join(
        [summary.suggested_next_step, *(item.task for item in summary.action_items)]
    ).lower()
    for forbidden in case.get("next_step_must_not_contain", []):
        report.checks[f"did not advise {forbidden!r}"] = forbidden.lower() not in actionable

    report.checks["made no unverified claims"] = not result.unverified_claims
    if result.unverified_claims:
        report.notes.append(f"unverified: {result.unverified_claims}")

    return report


def _result_text(result: AnalysisResult) -> str:
    summary = result.summary
    parts = [summary.summary, summary.suggested_next_step, *summary.key_points]
    parts += [item.task for item in summary.action_items]
    parts += [attachment.extracted_text or "" for attachment in result.attachments]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


def run(selected: list[str] | None = None) -> list[CaseReport]:
    golden = json.loads(GOLDEN_PATH.read_text())
    reference_time = datetime.fromisoformat(golden["reference_time"])
    # A real deployment tells the app which address is the user's own; the
    # dataset does the same, so the evaluation exercises the real code path.
    settings = Settings(owner_address=golden.get("owner_address"))

    client = OllamaClient(settings)
    if not client.is_available():
        print(
            f"Ollama is not reachable at {settings.ollama_host}.\n"
            f"Start it, then run: ollama pull {settings.model_name}",
            file=sys.stderr,
        )
        raise SystemExit(2)

    reports = []
    for case in golden["cases"]:
        if selected and case["file"] not in selected:
            continue

        recorder = RecordingSummarizer(Summarizer(client, settings))
        pipeline = Pipeline(summarizer=recorder, settings=settings)
        payload = (FIXTURE_DIR / case["file"]).read_bytes()

        started = time.monotonic()
        try:
            result = pipeline.analyse([payload], now=reference_time)
        except Exception as exc:  # noqa: BLE001 - one broken case must not stop the run
            report = CaseReport(name=case["file"], description=case["what_it_tests"])
            report.error = f"{type(exc).__name__}: {exc}"
            report.seconds = time.monotonic() - started
            reports.append(report)
            print(f"  {FAIL}  {case['file']}  ({report.error})")
            continue

        report = score_case(case, result, "\n".join(recorder.seen))
        report.seconds = time.monotonic() - started
        reports.append(report)

        passed, total = report.score
        mark = PASS if report.passed else FAIL
        print(f"  {mark:4}  {case['file']:34} {passed}/{total} checks  {report.seconds:5.1f}s")

    return reports


def print_report(reports: list[CaseReport]) -> None:
    total_checks = sum(report.score[1] for report in reports)
    passed_checks = sum(report.score[0] for report in reports)
    passed_cases = sum(report.passed for report in reports)

    print("\n" + "=" * 72)
    print(f"cases passed fully : {passed_cases}/{len(reports)}")
    if total_checks:
        share = 100 * passed_checks / total_checks
        print(f"individual checks  : {passed_checks}/{total_checks} ({share:.0f}%)")
    if reports:
        print(f"average time       : {sum(r.seconds for r in reports) / len(reports):.1f}s")
    print("=" * 72)

    failures = [report for report in reports if not report.passed]
    if not failures:
        print("\nEvery case passed.")
        return

    print("\nWhat did not pass:\n")
    for report in failures:
        print(f"  {report.name} - {report.description}")
        if report.error:
            print(f"      error: {report.error}")
        for name, ok in report.checks.items():
            if not ok:
                print(f"      missed: {name}")
        for note in report.notes:
            print(f"      note:   {note}")
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="*", help="run only these fixture filenames")
    parser.add_argument("--json", type=Path, help="also write the raw results to this file")
    arguments = parser.parse_args()

    print(f"Scoring {Settings().model_name} against the golden dataset\n")
    reports = run(arguments.only)
    print_report(reports)

    if arguments.json:
        arguments.json.write_text(
            json.dumps(
                [
                    {
                        "file": report.name,
                        "tests": report.description,
                        "passed": report.passed,
                        "checks": report.checks,
                        "notes": report.notes,
                        "seconds": round(report.seconds, 2),
                        "error": report.error,
                    }
                    for report in reports
                ],
                indent=2,
            )
        )

    return 0 if all(report.passed for report in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())

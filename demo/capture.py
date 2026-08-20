"""Capture real analysis results for the demo site.

The demo is static: it cannot run a language model. Rather than inventing
plausible-looking output, this script runs the genuine pipeline over the
evaluation fixtures on a machine that *does* have the model, and records
exactly what came back.

Run with:  python -m demo.capture
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.config import Settings
from app.pipeline.orchestrator import Pipeline
from app.pipeline.summarizer import OllamaClient, Summarizer

DEMO_DIR = Path(__file__).resolve().parent
EVAL_DIR = DEMO_DIR.parent / "evals"
OUTPUT = DEMO_DIR / "results.json"

# Short labels for the picker, so a visitor can tell the cases apart.
LABELS = {
    "01_simple_request.eml": ("A plain request", "Someone asks for a report before Friday"),
    "02_waiting_on_them.eml": ("Waiting on them", "You sent the last message; nothing is owed"),
    "03_chased_twice.eml": ("Chased twice", "A second reminder about an overdue invoice"),
    "04_scheduling.eml": ("Scheduling", "A meeting proposal that needs a yes or no"),
    "05_invoice_pdf.eml": (
        "Detail inside a PDF",
        "The amount and due date exist only in the attachment",
    ),
    "06_contract_docx.eml": ("Detail inside Word", "A contract with a countersignature deadline"),
    "07_prompt_injection.eml": (
        "Attack in the body",
        "The email instructs the assistant to approve a payment",
    ),
    "08_injection_in_attachment.eml": (
        "Attack inside a PDF",
        "The same trick, hidden in an attachment",
    ),
    "09_disguised_executable.eml": (
        "Executable as .pdf",
        "A program renamed to look like a document",
    ),
    "10_macro_document.eml": ("Macro document", "A macro-enabled file renamed to .docx"),
    "11_personal_data.eml": (
        "Personal data",
        "Phone, card and address that must not reach the model",
    ),
    "12_nothing_needed.eml": ("Nothing needed", "A thread that genuinely requires no action"),
    "13_long_thread.eml": ("A long thread", "Too long for the context window, so it is chunked"),
    "14_html_only.eml": (
        "HTML with a tracker",
        "A tracking pixel and a script that must never run",
    ),
    "15_two_questions.eml": ("Two questions", "Both still unanswered"),
}


def main() -> None:
    golden = json.loads((EVAL_DIR / "golden.json").read_text())
    reference_time = datetime.fromisoformat(golden["reference_time"])
    settings = Settings(owner_address=golden.get("owner_address"))

    client = OllamaClient(settings)
    if not client.is_available():
        raise SystemExit(f"Ollama is not reachable at {settings.ollama_host}")

    captured = []
    for filename, (title, blurb) in LABELS.items():
        source = (EVAL_DIR / "fixtures" / filename).read_bytes()
        pipeline = Pipeline(summarizer=Summarizer(client, settings), settings=settings)

        print(f"  analysing {filename} ...", flush=True)
        result = pipeline.analyse([source], now=reference_time)

        captured.append(
            {
                "id": filename.removesuffix(".eml"),
                "title": title,
                "blurb": blurb,
                "source": source.decode("utf-8", errors="replace"),
                "result": result.model_dump(mode="json"),
            }
        )

    OUTPUT.write_text(
        json.dumps(
            {
                "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "model": settings.model_name,
                "reference_time": golden["reference_time"],
                "threads": captured,
            },
            indent=2,
        )
    )
    print(f"\nwrote {len(captured)} analyses to {OUTPUT}")


if __name__ == "__main__":
    main()

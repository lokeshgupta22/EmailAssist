"""Loading of prompt templates.

Prompts live in ``prompts/*.txt`` rather than inside the code. They are the
part of the system most likely to be revised, and keeping them as plain files
means a change to the wording shows up as a readable diff instead of being
buried in a Python string.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"


class PromptNotFoundError(FileNotFoundError):
    """Raised when a template is missing, which is always a packaging mistake."""


@cache
def load_prompt(name: str) -> str:
    """Return the named template. Cached: templates never change at runtime."""
    path = PROMPT_DIR / f"{name}.txt"
    if not path.is_file():
        raise PromptNotFoundError(f"prompt template '{name}' is missing from {PROMPT_DIR}")
    return path.read_text(encoding="utf-8").strip()


def render(name: str, **values: object) -> str:
    """Fill a template's ``{placeholders}`` with the given values."""
    template = load_prompt(name)
    try:
        return template.format(**values)
    except KeyError as exc:
        raise PromptNotFoundError(f"prompt template '{name}' expects a value for {exc}") from exc

"""Small text helpers shared by the parsing and enrichment stages.

They live here rather than being duplicated per stage, and they are pure
functions so they can be tested without constructing any email at all.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

# Tags whose *content* must never end up in the extracted text, and tags that
# would pull in remote resources if the document were ever rendered.
_UNSAFE_TAGS = (
    "script",
    "style",
    "head",
    "meta",
    "link",
    "img",
    "iframe",
    "object",
    "embed",
    "svg",
    "video",
    "audio",
    "form",
    "input",
)

_SIGNATURE_SEPARATOR_RE = re.compile(r"^--\s?$", re.MULTILINE)
_BLANK_RUN_RE = re.compile(r"\n{3,}")
_TRAILING_SPACE_RE = re.compile(r"[ \t]+$", re.MULTILINE)


def html_to_text(html: str) -> str:
    """Convert an HTML mail body to plain text.

    The document is parsed, never rendered: scripts and stylesheets are dropped
    with their contents, and elements that would fetch remote resources (chiefly
    tracking pixels) are removed entirely. Only text nodes survive, so no URL,
    attribute or markup from the email can reach the rest of the pipeline.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag_name in _UNSAFE_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    for line_break in soup.find_all(["br", "p", "div", "tr", "li"]):
        line_break.append("\n")

    return normalise_whitespace(soup.get_text())


def normalise_whitespace(text: str) -> str:
    """Collapse trailing spaces and runs of blank lines."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _TRAILING_SPACE_RE.sub("", text)
    return _BLANK_RUN_RE.sub("\n\n", text).strip()


def strip_signature(text: str) -> str:
    """Remove everything after the conventional ``-- `` signature separator.

    Signatures are noise for summarisation and are a dense source of personal
    data, so dropping them early improves both output quality and privacy.
    """
    match = _SIGNATURE_SEPARATOR_RE.search(text)
    if match is None:
        return text
    return text[: match.start()].rstrip()


def dequote(text: str) -> str:
    """Strip one level of ``>`` quoting from a block of quoted text.

    Only applied when the block really is quoted, so unquoted Outlook-style
    history passes through untouched.
    """
    lines = text.split("\n")
    quoted = [line for line in lines if line.strip()]
    if not quoted or not all(line.lstrip().startswith(">") for line in quoted):
        return text

    stripped = []
    for line in lines:
        without_marker = line.lstrip()
        if without_marker.startswith(">"):
            without_marker = without_marker[1:]
            if without_marker.startswith(" "):
                without_marker = without_marker[1:]
            stripped.append(without_marker)
        else:
            stripped.append(line)
    return "\n".join(stripped)

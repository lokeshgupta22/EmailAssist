"""The shared text helpers are pure functions, so they are tested directly."""

import pytest

from app.pipeline.text import dequote, html_to_text, normalise_whitespace, strip_signature


class TestHtmlToText:
    def test_tags_are_removed_but_text_is_kept(self):
        assert html_to_text("<p>Hello <b>world</b></p>") == "Hello world"

    @pytest.mark.parametrize(
        "html, forbidden",
        [
            ("<script>steal()</script><p>ok</p>", "steal()"),
            ("<style>.a{color:red}</style><p>ok</p>", "color:red"),
            ('<img src="http://tracker.test/p.gif"><p>ok</p>', "tracker.test"),
            ('<iframe src="http://evil.test"></iframe><p>ok</p>', "evil.test"),
            ("<object data='http://evil.test/x'></object><p>ok</p>", "evil.test"),
        ],
    )
    def test_dangerous_and_remote_content_never_survives(self, html: str, forbidden: str):
        text = html_to_text(html)

        assert forbidden not in text
        assert "ok" in text

    def test_link_targets_are_dropped_while_link_text_is_kept(self):
        text = html_to_text('<a href="http://tracker.test/click">read more</a>')

        assert text == "read more"
        assert "tracker.test" not in text

    def test_block_elements_become_line_breaks(self):
        assert html_to_text("<div>one</div><div>two</div>") == "one\ntwo"

    def test_malformed_html_does_not_raise(self):
        assert "text" in html_to_text("<p>text<<<>>")

    def test_empty_input_gives_empty_output(self):
        assert html_to_text("") == ""


class TestNormaliseWhitespace:
    def test_carriage_returns_are_removed(self):
        assert normalise_whitespace("a\r\nb") == "a\nb"

    def test_runs_of_blank_lines_collapse_to_one(self):
        assert normalise_whitespace("a\n\n\n\n\nb") == "a\n\nb"

    def test_trailing_spaces_are_trimmed(self):
        assert normalise_whitespace("a   \nb\t\n") == "a\nb"


class TestStripSignature:
    def test_content_after_the_separator_is_removed(self):
        assert strip_signature("Body text\n-- \nAlice\nCEO") == "Body text"

    def test_separator_without_trailing_space_is_also_recognised(self):
        assert strip_signature("Body text\n--\nAlice") == "Body text"

    def test_text_without_a_signature_is_unchanged(self):
        assert strip_signature("Body text\nMore text") == "Body text\nMore text"

    def test_a_dashed_rule_inside_a_sentence_is_not_a_signature(self):
        assert strip_signature("costs 5 -- maybe 6 -- per unit") == "costs 5 -- maybe 6 -- per unit"


class TestDequote:
    def test_one_level_of_quoting_is_stripped(self):
        assert dequote("> line one\n> line two") == "line one\nline two"

    def test_only_the_outermost_level_is_stripped(self):
        assert dequote("> a\n>> b") == "a\n> b"

    def test_quote_markers_without_a_space_are_handled(self):
        assert dequote(">a\n>b") == "a\nb"

    def test_unquoted_text_passes_through_untouched(self):
        assert dequote("From: alice\nSent: today") == "From: alice\nSent: today"

    def test_partially_quoted_text_is_left_alone(self):
        assert dequote("> quoted\nnot quoted") == "> quoted\nnot quoted"

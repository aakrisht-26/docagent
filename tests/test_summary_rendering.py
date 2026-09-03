"""Tests for how a summary is rendered in the UI.

The renderer is hand-rolled rather than a markdown library, and it was written
against an EARLIER model's output. It understood `#`/`##`/`###`, `- ` bullets,
`**bold**` and a bare `[Source: Page 1]` citation. The current model emits three
more things constantly, and every one of them fell through to a branch that
printed the line verbatim:

    | Depot | MTBF |     ->  one run-on paragraph of pipes
    ---                  ->  three literal dashes in body text
    *italic*             ->  literal asterisks, italics unsupported
    *[Source: Page 1]*   ->  correct badge with a stray `*` on each side

Measured on a real 518-word document before the fix: 11 citations rendered with
literal asterisks and 0 italicised, and 7 literal `---` lines.

The table case is the same defect the PDF export had, which is the reason to
distrust "it looked fine last time I checked": one renderer was fixed and the
other was not, because nothing tested either.

WHY THE TABLE NEEDS A SEPARATOR ROW. `_is_table_separator` is what stops an
ordinary sentence containing a pipe from being restructured into a one-cell
table. That guard has its own test, because without it the fix would corrupt
prose to render tables.

Run:
    pytest tests/test_summary_rendering.py -v
"""

from __future__ import annotations

import unittest

import ui.components.results_view as rv
from ui.components.results_view import (
    _inline_md, _is_table_separator, _table_html, classification_method_label,
    summary_method_label,
)


def render(summary: str) -> str:
    """Run the real renderer and return the HTML it emitted."""
    captured: list = []
    original = rv._html
    rv._html = lambda html: captured.append(html)
    try:
        rv._render_summary_structured(summary)
    finally:
        rv._html = original
    return "\n".join(captured)


class TestTables(unittest.TestCase):
    TABLE = ("| Depot | MTBF | Change |\n"
             "|---|---|---|\n"
             "| Bangalore | 412 h | +18% |\n"
             "| Chennai | 388 h | -4% |\n")

    def test_a_table_becomes_a_real_table(self):
        html = render(self.TABLE)
        self.assertIn("<table", html)
        self.assertIn("<th>Depot</th>", html)
        self.assertIn("<td>Bangalore</td>", html)

    def test_no_pipes_survive_into_the_output(self):
        """The reported defect was a paragraph full of pipes and dashes."""
        html = render(self.TABLE)
        self.assertNotIn("|---|", html)
        self.assertNotIn("| Bangalore |", html)

    def test_every_row_and_column_is_kept(self):
        html = render(self.TABLE)
        for value in ("Depot", "MTBF", "Change", "Bangalore", "412 h",
                      "+18%", "Chennai", "388 h", "-4%"):
            with self.subTest(value=value):
                self.assertIn(value, html)

    def test_cells_are_formatted_like_the_rest_of_the_summary(self):
        html = render("| A | B |\n|---|---|\n| **bold** | *it* |\n")
        self.assertIn("<strong>bold</strong>", html)
        self.assertIn("<em>it</em>", html)

    def test_a_ragged_row_is_padded_rather_than_dropped(self):
        html = render("| A | B | C |\n|---|---|---|\n| 1 | 2 |\n")
        self.assertIn("<td>1</td>", html)
        self.assertIn("<td>2</td>", html)

    def test_a_pipe_line_without_a_separator_is_not_a_table(self):
        """The guard, exercised properly.

        Two earlier versions of this test did NOT exercise the guard, and both
        were caught by mutation rather than by reading them:

          1. "Revenue | up 4% | ..." does not START with a pipe, so it never
             reached the separator check at all.
          2. A single pipe-prefixed line fails the `i + 1 < len(lines)` bound
             first, so it never reached the check either.

        A second line is therefore required: the first must look like a table
        row and the second must be something that is not a separator, which is
        the only shape where the guard is what decides.
        """
        html = render("| this looks like a table row\n"
                      "but this is ordinary prose")
        self.assertNotIn("<table", html)
        self.assertIn("ordinary prose", html)

    def test_prose_containing_a_pipe_is_left_alone(self):
        html = render("Revenue | up 4% | is the headline.")
        self.assertNotIn("<table", html)
        self.assertIn("Revenue | up 4% | is the headline.", html)

    def test_a_table_immediately_after_a_heading_still_renders(self):
        html = render("## Metrics\n| A | B |\n|---|---|\n| 1 | 2 |\n")
        self.assertIn("summary-section-heading", html)
        self.assertIn("<table", html)

    def test_text_after_a_table_resumes_normally(self):
        html = render(self.TABLE + "\nAnd then a sentence.\n")
        self.assertIn("<table", html)
        self.assertIn("And then a sentence.", html)


class TestTheSeparatorGuard(unittest.TestCase):
    def test_it_accepts_the_forms_a_model_writes(self):
        for line in ("|---|---|", "| --- | --- |", "|:---|---:|",
                     "|:---:|:---:|", "|-|-|"):
            with self.subTest(line=line):
                self.assertTrue(_is_table_separator(line))

    def test_it_rejects_prose_and_rules(self):
        for line in ("| Bangalore | 412 h |", "---", "some | text",
                     "", "| a-b | c |"):
            with self.subTest(line=line):
                self.assertFalse(_is_table_separator(line))


class TestInlineMarkdown(unittest.TestCase):
    def test_bold_still_works(self):
        self.assertIn("<strong>x</strong>", _inline_md("a **x** b"))

    def test_italics_now_work(self):
        self.assertIn("<em>x</em>", _inline_md("a *x* b"))

    def test_bold_is_not_eaten_by_the_italic_rule(self):
        """`**x**` must be consumed before the single-asterisk rule sees it."""
        out = _inline_md("**x**")
        self.assertIn("<strong>x</strong>", out)
        self.assertNotIn("<em>", out)

    def test_inline_code_is_marked_up(self):
        self.assertIn("<code>94 min</code>", _inline_md("resolution `94 min`"))

    def test_the_asterisk_wrapped_citation_leaves_no_asterisks(self):
        """The reported defect: the badge was right, the asterisks stayed.

        Asserting only "no asterisks" is not enough and that was found by
        mutation: if the citation rule matches just the bare form, the leftover
        `*...*` is then swallowed by the ITALIC rule, which removes the
        asterisks and makes a broken implementation look correct. So this also
        pins that the badge is not sitting inside an <em>.
        """
        out = _inline_md("*[Source: Page 1]*")
        self.assertIn('class="citation-tag"', out)
        self.assertNotIn("*", out)
        self.assertNotIn("<em>", out,
                         "the citation rule must consume the asterisks itself, "
                         "not leave them for the italic rule")

    def test_the_bare_citation_still_works(self):
        """The older form must keep working — stored summaries contain it."""
        out = _inline_md("[Source: Page 3]")
        self.assertIn('class="citation-tag"', out)
        self.assertIn("Page 3", out)

    def test_a_multi_page_citation_is_kept_whole(self):
        out = _inline_md("*[Source: Pages 2-4]*")
        self.assertIn("Pages 2-4", out)
        self.assertNotIn("*", out)

    def test_multiplication_in_prose_is_not_italicised(self):
        """A lone asterisk between digits is not emphasis."""
        self.assertNotIn("<em>", _inline_md("3 * 4 = 12"))

    def test_it_survives_empty_input(self):
        self.assertEqual(_inline_md(""), "")


class TestRulesAndCitationsInContext(unittest.TestCase):
    def test_a_horizontal_rule_is_a_rule(self):
        html = render("Before\n\n---\n\nAfter")
        self.assertIn("<hr", html)
        self.assertNotIn(">---<", html)

    def test_citations_inside_bullets_are_formatted(self):
        """Bullets applied bold but never citations, so a citation in a bullet
        stayed raw — the branches disagreed about what inline markup meant."""
        html = render("- **Finding:** it went up. *[Source: Page 1]*")
        self.assertIn('class="citation-tag"', html)
        self.assertIn("<strong>Finding:</strong>", html)

    def test_a_realistic_summary_leaves_no_literal_markup(self):
        summary = (
            "# Report\n\n## 1. Throughput\n\n"
            "- **MTBF:** rose to **402 h**, a *17.9%* increase.\n"
            "  *[Source: Page 1]*\n\n"
            "| Depot | MTBF |\n|---|---|\n| Bangalore | 412 h |\n\n"
            "---\n\n"
            "Resolution was `94 min`.\n"
        )
        html = render(summary)
        for artefact in ("|---|", "*[Source:", ">---<"):
            with self.subTest(artefact=artefact):
                self.assertNotIn(artefact, html)
        self.assertIn("<table", html)
        self.assertIn("<hr", html)
        self.assertIn("<em>17.9%</em>", html)
        self.assertIn("<code>94 min</code>", html)


class TestKeyFieldsReachTheReader(unittest.TestCase):
    """The extraction output used to be rendered nowhere.

    It was stored, persisted and printed by the e2e harness, and no component
    displayed it -- `grep -rn extracted_entities ui/` returned a store and a
    restore. A user met it only by downloading the JSON, while the stage spent
    54% of a free-tier minute producing it.

    It is now a compact table above the summary prose, and a table in the
    markdown export. Above the prose rather than beside it because these are
    lookup values: for a contract, four addressable rows beat the same facts
    spread through seven thousand characters of narrative.
    """

    def _render_fields(self, entities):
        captured = []
        original = rv._html
        rv._html = lambda html: captured.append(html)
        try:
            rv._render_key_fields(type("R", (), {"extracted_entities": entities}))
        finally:
            rv._html = original
        return "\n".join(captured)

    def test_fields_are_rendered(self):
        html = self._render_fields({"parties": "Northwind Ltd",
                                    "effective_date": "1 March 2026"})
        self.assertIn("key-fields", html)
        self.assertIn("Parties", html)
        self.assertIn("Northwind Ltd", html)
        self.assertIn("Effective date", html)

    def test_snake_case_keys_become_readable_labels(self):
        html = self._render_fields({"termination_date": "x"})
        self.assertIn("Termination date", html)
        self.assertNotIn("termination_date", html)

    def test_a_list_value_is_joined_not_printed_as_a_list(self):
        html = self._render_fields({"parties": ["Northwind Ltd", "Calder GmbH"]})
        self.assertIn("Northwind Ltd; Calder GmbH", html)
        self.assertNotIn("[", html)

    def test_nothing_renders_when_there_is_nothing(self):
        """No empty panel on a document the stage skipped or that produced
        no fields."""
        for entities in ({}, None):
            with self.subTest(entities=entities):
                self.assertEqual(self._render_fields(entities), "")

    def test_a_field_with_an_empty_value_is_dropped(self):
        html = self._render_fields({"parties": "Northwind Ltd", "penalties": "  "})
        self.assertIn("Parties", html)
        self.assertNotIn("Penalties", html)

    def test_values_are_html_escaped(self):
        """Field values are model output landing in raw HTML."""
        html = self._render_fields({"parties": "<script>x</script>"})
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)


class TestKeyFieldsInTheMarkdownExport(unittest.TestCase):
    def _md(self, entities):
        from core.pipeline_result import PipelineResult
        return PipelineResult(
            file_name="c.pdf", file_type="pdf", doc_type="normal_document",
            domain="Legal", classification_confidence=0.03,
            classification_method="hybrid_groq", summary="# T\n\nBody.",
            summary_method="llm", questions=[], question_extraction_method="n",
            raw_text="t", word_count=1, page_count=1, metadata={},
            extracted_entities=entities).to_markdown()

    def test_the_export_carries_the_fields(self):
        md = self._md({"parties": ["Northwind Ltd", "Calder GmbH"],
                       "effective_date": "1 March 2026"})
        self.assertIn("## Key fields", md)
        self.assertIn("| Parties | Northwind Ltd; Calder GmbH |", md)
        self.assertIn("| Effective date | 1 March 2026 |", md)

    def test_a_pipe_in_a_value_does_not_break_the_table(self):
        """An unescaped pipe would split one value into two columns."""
        md = self._md({"penalties": "0.5 percent | capped at 8 percent"})
        self.assertIn("0.5 percent \\| capped at 8 percent", md)

    def test_a_newline_in_a_value_does_not_break_the_row(self):
        md = self._md({"obligations": "first line\nsecond line"})
        self.assertIn("| Obligations | first line second line |", md)

    def test_no_section_when_there_are_no_fields(self):
        self.assertNotIn("## Key fields", self._md({}))


class TestMethodLabels(unittest.TestCase):
    """The badge showed the raw identifier title-cased — "Llm Single Groq" —
    which names an internal code path. The method string carries a provider
    suffix, so these match on the prefix rather than listing providers."""

    def test_the_reported_badge_text_is_gone(self):
        self.assertEqual(summary_method_label("llm_single_groq"), "AI summary")

    def test_map_reduce_is_distinguishable(self):
        self.assertNotEqual(summary_method_label("llm_map_reduce_groq"),
                            summary_method_label("llm_single_groq"))

    def test_a_new_provider_does_not_need_a_code_change(self):
        for method in ("llm_single_anthropic", "llm_single_openai",
                       "llm_map_reduce_somethingelse"):
            with self.subTest(method=method):
                self.assertNotIn("_", summary_method_label(method))

    def test_the_fallback_is_named_honestly(self):
        """A reader should be able to tell the LLM did not write this."""
        for method in ("extractive", "extractive_sentences"):
            with self.subTest(method=method):
                self.assertIn("Extractive", summary_method_label(method))

    def test_absence_is_not_dressed_up(self):
        for method in ("skipped", "none", ""):
            with self.subTest(method=method):
                self.assertEqual(summary_method_label(method), "Not summarised")

    def test_the_classifier_label_replaces_hybrid_groq(self):
        self.assertEqual(classification_method_label("hybrid_groq"),
                         "heuristics + AI")
        self.assertEqual(classification_method_label("heuristic"),
                         "heuristics only")
        self.assertEqual(classification_method_label("none"), "not classified")

    def test_no_label_leaks_an_underscore(self):
        for method in ("llm_single_groq", "llm_map_reduce_groq", "hybrid_groq",
                       "extractive_sentences", "heuristic", "none", ""):
            with self.subTest(method=method):
                self.assertNotIn("_", summary_method_label(method))
                self.assertNotIn("_", classification_method_label(method))


if __name__ == "__main__":
    unittest.main(verbosity=2)

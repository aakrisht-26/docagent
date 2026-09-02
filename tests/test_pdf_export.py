"""Tests for the PDF export: font coverage, markdown tables, and what they fix.

Three defects were reported against a real exported PDF and all three are
covered here, because all three were invisible to the previous tests — which
only ever asserted that bytes came back.

DEFECT 1: characters rendered as filled boxes. The export registered no font,
so ReportLab used base-14 **Helvetica with WinAnsiEncoding** — 224 characters.
It looked arbitrary rather than absent because ReportLab silently routes some
characters to two other base-14 fonts: `≈ ≥ ≤ → ∑ √ ∞` come from **Symbol** and
`✓` from **ZapfDingbats**. So an en dash worked and a non-breaking hyphen did
not, and the pattern was three repertoires overlaid.

  Measured before the fix: `12–18%` correct, `nonIbreaking` boxed,
  `I Extracted` / `II Document` boxed (our own ❓ and ℹ️ headers).

  Fixed by vendoring DejaVu Sans: 5,943 codepoints. ReportLab's bundled
  Bitstream Vera was measured and rejected at 293.

DEFECT 2: markdown tables arrived as one run-on paragraph of pipes, because
the summary renderer split on blank lines and handed everything to a converter
that knew about bold and bullets but not tables. The model emits tables
constantly, so this was the worst legibility problem in the file.

DEFECT 3: words appeared joined ("15 msClassify"). This one was NOT a
generator defect and the tests say so — measured on the rendered PDF, zero word
pairs were drawn without a gap, and both PyMuPDF and pdfplumber returned the
prose correctly spaced. The joins were adjacent TABLE CELLS concatenated when
the text was copied, which defect 2 made far worse by putting a whole table in
one paragraph. Fixing 2 fixes the appearance of 3, and
`test_cells_are_laid_out_in_columns` is what pins that, geometrically.

Run:
    pytest tests/test_pdf_export.py -v
"""

from __future__ import annotations

import unittest

from core.pipeline_result import PipelineResult
from ui.components.pdf_fonts import register_pdf_fonts, sanitise_for_pdf

#: Every character class the summariser was observed to emit and lose, plus the
#: ones that worked and must keep working. Enumerated from the Unicode blocks a
#: model actually reaches for: punctuation, currency, maths, arrows, accented
#: Latin, Greek, Cyrillic, super/subscripts.
CHARACTERS = {
    "non-breaking hyphen U+2011": "non‑breaking",
    "en dash U+2013":             "12–18%",
    "em dash U+2014":             "yes — no",
    "approx U+2248":              "≈ 12 ms",
    "greater-equal U+2265":       "≥ 99.9%",
    "rupee U+20B9":               "₹500",
    "arrow U+2192":               "→ up",
    "macron U+016B":              "Bengalūru",
    "s-comma U+0218":             "Ștefan",
    "greek alpha U+03B1":         "α-test",
    "cyrillic U+0416":            "Жurnal",
    "superscript U+2074":         "10⁴",
    "subscript U+2082":           "H₂O",
    "check U+2713":               "✓ done",
    "curly quotes U+201C/D":      "“quoted”",
}

#: No text font carries these. They must DISAPPEAR, not become boxes.
EMOJI = ("❌", "\U0001F4CA", "⏱", "❓")


def _result(summary: str, **kw) -> PipelineResult:
    base = dict(
        file_name="depot.pdf", file_type="pdf", doc_type="normal_document",
        domain="Technical", classification_confidence=0.03,
        classification_method="hybrid_groq", summary=summary,
        summary_method="llm_map_reduce_groq", questions=["What caused the dip?"],
        question_extraction_method="llm", raw_text="x" * 80, word_count=641,
        page_count=3, metadata={"author": "Ops Team"},
        skill_timings={"parse": 120.0, "classify": 15.0},
        processing_time_ms=8600.0, success=True,
    )
    base.update(kw)
    return PipelineResult(**base)


def _render(summary: str):
    """Export, and return (extracted text, fonts actually drawn with)."""
    import fitz
    from ui.components.results_view import generate_pdf_bytes
    data = generate_pdf_bytes(_result(summary))
    doc = fitz.open(stream=data, filetype="pdf")
    text = "\n".join(pg.get_text() for pg in doc)
    drawn = {sp["font"]
             for pg in doc for blk in pg.get_text("dict")["blocks"]
             for ln in blk.get("lines", []) for sp in ln.get("spans", [])}
    return text, drawn


class TestFontRegistration(unittest.TestCase):
    def test_a_unicode_font_is_registered(self):
        regular, bold, italic, bolditalic = register_pdf_fonts()
        self.assertIn("DejaVu", regular)
        self.assertIn("DejaVu", bold)
        self.assertNotEqual(regular, bold, "bold must be a real second face; "
                                           "ReportLab does not synthesise it")

    def test_it_falls_back_rather_than_raising(self):
        """A missing font file must degrade to the old behaviour, not 500 the
        download button."""
        import ui.components.pdf_fonts as mod
        register_pdf_fonts.cache_clear()
        original = mod._FONT_DIR
        mod._FONT_DIR = "/nonexistent/fonts"
        try:
            self.assertEqual(register_pdf_fonts()[0], "Helvetica")
        finally:
            mod._FONT_DIR = original
            register_pdf_fonts.cache_clear()


class TestTheSanitiserIsALastResort(unittest.TestCase):
    """It exists only for emoji. If it starts removing anything else, the font
    is doing less than it should and that is the bug to fix."""

    def test_ascii_is_never_touched(self):
        text = "Plain ASCII: costs fell 12-18% (about $500)."
        self.assertEqual(sanitise_for_pdf(text), text)

    def test_characters_the_font_covers_survive(self):
        for label, snippet in CHARACTERS.items():
            with self.subTest(char=label):
                self.assertEqual(sanitise_for_pdf(snippet), snippet)

    def test_emoji_are_removed(self):
        for ch in EMOJI:
            with self.subTest(emoji=repr(ch)):
                self.assertNotIn(ch, sanitise_for_pdf(f"start {ch} end"))

    def test_it_does_not_collapse_the_metadata_spacing(self):
        """The metadata line separates fields with three spaces. An earlier
        version collapsed whitespace after dropping an emoji and would have
        eaten them."""
        line = "**File:** a.pdf   |   **Domain:** Technical"
        self.assertEqual(sanitise_for_pdf(line), line)

    def test_newlines_and_tabs_survive(self):
        self.assertEqual(sanitise_for_pdf("a\nb\tc"), "a\nb\tc")

    def test_it_no_ops_when_coverage_is_unknown(self):
        """Guessing at coverage would corrupt documents to fix a cosmetic
        problem, so an unreadable font must mean 'change nothing'."""
        import ui.components.pdf_fonts as mod
        mod._covered_codepoints.cache_clear()
        original = mod._FONT_DIR
        mod._FONT_DIR = "/nonexistent/fonts"
        try:
            text = f"keep {EMOJI[0]} everything"
            self.assertEqual(sanitise_for_pdf(text), text)
        finally:
            mod._FONT_DIR = original
            mod._covered_codepoints.cache_clear()


class TestCharactersThatUsedToBeBoxes(unittest.TestCase):
    """Rendered and read back, not asserted against the font's cmap."""

    @classmethod
    def setUpClass(cls):
        body = " ".join(CHARACTERS.values())
        cls.text, cls.drawn = _render(f"# Report\n\n## Findings\n{body}\n")

    def test_every_character_class_round_trips(self):
        for label, snippet in CHARACTERS.items():
            with self.subTest(char=label):
                self.assertIn(snippet, self.text)

    def test_the_document_is_drawn_in_the_unicode_font(self):
        self.assertTrue(any("DejaVu" in f for f in self.drawn), self.drawn)

    def test_nothing_is_drawn_in_helvetica(self):
        """Helvetica stays in the page RESOURCES because ReportLab always adds
        it as the canvas default; what matters is that it draws no glyph."""
        self.assertFalse([f for f in self.drawn if "Helvetica" in f], self.drawn)


class TestEmojiInsteadOfBoxes(unittest.TestCase):
    def test_model_emitted_emoji_do_not_appear_as_anything(self):
        text, _ = _render("# R\n\n## S\nStatus ❌ failed \U0001F4CA chart.\n")
        for ch in ("❌", "\U0001F4CA"):
            with self.subTest(emoji=repr(ch)):
                self.assertNotIn(ch, text)
        self.assertIn("Status", text)
        self.assertIn("chart", text)

    def test_our_own_section_headers_no_longer_carry_emoji(self):
        """Three headers hardcoded ❓, ℹ️ and ⏱, which rendered as boxes.
        They were decoration on headings that already say what they are."""
        text, _ = _render("# R\n\n## S\nBody.\n")
        for heading in ("Extracted Questions", "Document Metadata",
                        "Skill Timing Breakdown"):
            with self.subTest(heading=heading):
                self.assertIn(heading, text)
        for ch in ("❓", "ℹ", "⏱"):
            with self.subTest(emoji=repr(ch)):
                self.assertNotIn(ch, text)


class TestMarkdownTables(unittest.TestCase):
    TABLE = ("# R\n\n## Metrics\n\n"
             "| Depot | MTBF | Change |\n|---|---|---|\n"
             "| Bangalore | 412 h | +18% |\n| Chennai | 388 h | −4% |\n")

    def test_a_table_is_not_a_run_on_paragraph(self):
        """The reported defect: every table arrived as one line of pipes."""
        text, _ = _render(self.TABLE)
        flat = text.replace("\n", " ")
        self.assertNotIn("|---|", flat)
        self.assertNotIn("| Bangalore |", flat)

    def test_the_content_survives_the_conversion(self):
        text, _ = _render(self.TABLE)
        for cell in ("Depot", "MTBF", "Bangalore", "412 h", "+18%", "Chennai"):
            with self.subTest(cell=cell):
                self.assertIn(cell, text)

    def test_cells_are_laid_out_in_columns(self):
        """This is what fixes the reported "15 msClassify" joining.

        Asserted structurally rather than textually. An earlier version of this
        test flattened the extracted text and looked for "Bangalore 412 h",
        which proves nothing: dropping newlines joins ANY two adjacent lines,
        table or not, so it failed against correct output.

        What actually distinguishes a real table is geometry. A row's cells
        share a baseline and sit at different x positions, which is impossible
        in the run-on paragraph the export used to produce.
        """
        import fitz
        from ui.components.results_view import generate_pdf_bytes
        doc = fitz.open(stream=generate_pdf_bytes(_result(self.TABLE)),
                        filetype="pdf")
        words = [w for pg in doc for w in pg.get_text("words")]
        def box(needle):
            return next(w for w in words if w[4] == needle)
        depot, mtbf = box("Bangalore"), box("412")
        self.assertLess(abs(depot[1] - mtbf[1]), 2.0,
                        "cells of one row should share a baseline")
        self.assertGreater(mtbf[0] - depot[2], 1.0,
                           "the next column should start clear of the previous "
                           "one, not run on from it")

    def test_prose_containing_a_pipe_is_not_turned_into_a_table(self):
        """The guard: a table needs a |---| separator row. Without that check,
        an ordinary sentence with a pipe would be silently restructured."""
        text, _ = _render("# R\n\n## S\nRevenue | up 4% | is the headline.\n")
        self.assertIn("Revenue", text)
        self.assertIn("up 4%", text)

    def test_a_table_directly_under_a_heading_still_renders(self):
        """No blank line between the heading and the table — the split-on-blank
        -lines walker sees them as one block."""
        text, _ = _render("# R\n\n## Metrics\n| A | B |\n|---|---|\n| 1 | 2 |\n")
        self.assertNotIn("|---|", text.replace("\n", " "))
        self.assertIn("Metrics", text)


class TestTheOtherExportsWereAlreadyCorrect(unittest.TestCase):
    """Audited alongside the PDF. Both were fine, and these pin that."""

    SUMMARY = ("# R\n\n## M\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n"
               "Costs fell 12–18% with a non‑breaking hyphen.\n")

    def test_markdown_keeps_the_table_as_markdown(self):
        md = _result(self.SUMMARY).to_markdown()
        self.assertIn("| A | B |", md)
        self.assertIn("|---|", md)

    def test_markdown_keeps_every_character(self):
        md = _result(self.SUMMARY).to_markdown()
        self.assertIn("‑", md)
        self.assertIn("–", md)

    def test_the_dict_export_round_trips_the_summary_exactly(self):
        import json
        payload = json.dumps(_result(self.SUMMARY).to_dict(),
                             ensure_ascii=False, default=str)
        self.assertEqual(json.loads(payload)["summary"], self.SUMMARY)


class TestTheFilledFormExportUsesTheSameFont(unittest.TestCase):
    """The second generator had the same defect, and it renders a FILLED FORM —
    where a dropped character is a wrong answer, not just an ugly one."""

    def test_it_draws_in_the_unicode_font(self):
        import fitz
        from ui.components.results_view import generate_filled_pdf
        data = generate_filled_pdf("# Form\n\nName: Ștefan\n\nSpend: ₹500\n")
        doc = fitz.open(stream=data, filetype="pdf")
        text = "\n".join(pg.get_text() for pg in doc)
        drawn = {sp["font"] for pg in doc for blk in pg.get_text("dict")["blocks"]
                 for ln in blk.get("lines", []) for sp in ln.get("spans", [])}
        self.assertTrue(any("DejaVu" in f for f in drawn), drawn)
        self.assertIn("Ștefan", text)
        self.assertIn("₹500", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)

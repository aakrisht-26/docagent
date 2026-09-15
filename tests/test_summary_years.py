"""Tests for the unstated-year warning on summaries.

WHY IT EXISTS. A summary of a sales workbook said "$2,379,900 in total revenue
for Q3 2024". No sheet states a year. Measured on 85 recorded summaries
(tests/e2e/summary_eval/RESULTS.md), 21 of the 45 summaries of undated
documents gave a year the document never states, and 0 of the 40 summaries of
dated documents did. The audio briefing was titled "Q3 2024" in 5 of 5 runs.
The prompt already says "Never invent information".

WHY A WARNING AND NOT A FIX. Extraction drops a value carrying an invented
figure; a sentence cannot lose its year and still read. A warning names the
year instead.

WHY YEARS ONLY. It is the one kind of false figure a check can find without
being wrong about the rest. On the recording it flags 22 summaries: 39 invented
years, and one "e.g., 80% by 2028" -- a year that really is not in the
document. Flagging every figure absent from the source would mark 821 figures,
of which only 171 are false; the rest are correct arithmetic.

WHAT IT DOES NOT COVER. Wrong arithmetic (27 of 85 summaries) and invented
figures (8 of 85) pass untouched, and the warning says other figures are not
checked.

Run:
    pytest tests/test_summary_years.py -v
"""

from __future__ import annotations

import importlib.util
import json
import re
import unittest
from pathlib import Path

from core.models import SkillInput
from skills.summarization_skill import unstated_years, year_warning

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "tests" / "e2e" / "summary_eval"


def _eval_figures():
    """The eval's classifier, loaded by path under a unique name so nothing is
    added to sys.path."""
    spec = importlib.util.spec_from_file_location("summary_years_eval_figures", EVAL_DIR / "figures.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestItFlagsWhatWasMeasured(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.trials = [json.loads(line) for line in open(EVAL_DIR / "trials.jsonl", encoding="utf-8")]
        cls.sources = json.loads((EVAL_DIR / "trials.sources.json").read_text(encoding="utf-8"))

    def test_it_agrees_with_the_eval_on_every_recorded_summary(self):
        """The shipped check and the measured one must see the same years, or
        the published precision describes a different check."""
        figures = _eval_figures()
        parsed, flagged, kinds = {}, 0, set()
        for t in self.trials:
            text = self.sources[t["fixture"]]["full_text"]
            source = parsed.setdefault(t["fixture"], figures.Source(text))
            measured = {re.sub(r"\s", "", r["token"]) for r in figures.classify(t["summary"], source)
                        if r["category"] == "year"}
            shipped = set(unstated_years(t["summary"], text))
            with self.subTest(fixture=t["fixture"], round=t["round"]):
                self.assertEqual(shipped, measured)
            if shipped:
                flagged += 1
                kinds.add(self.sources[t["fixture"]]["kind"])
        self.assertEqual(flagged, 22)
        self.assertEqual(kinds, {"e2e"}, "no summary of a dated document may be flagged")

    def test_the_case_that_started_this(self):
        summary = "The company generated $2,379,900 in total revenue for Q3 2024."
        self.assertEqual(unstated_years(summary, self.sources["sample_sales.xlsx"]["full_text"]), ["2024"])


class TestYearsTheDocumentStatesAreNotFlagged(unittest.TestCase):
    def test_a_year_anywhere_in_the_document(self):
        self.assertEqual(unstated_years("Prepared in March 2026.", "Circulated on 12 March 2026."), [])

    def test_a_fiscal_year_written_short(self):
        self.assertEqual(unstated_years("FY 2026 sales by region", "[Sheet: Q3 FY26 Sales]"), [])

    def test_the_second_year_of_a_range(self):
        self.assertEqual(unstated_years("the 2026 budget", "Annual report 2025/26"), [])
        self.assertEqual(unstated_years("in 2025", "Review (2024-25)"), [])

    def test_dates(self):
        self.assertEqual(unstated_years("admitted in 2026", "Admitted 2026-05-04"), [])
        self.assertEqual(unstated_years("signed in 2026", "Signed 04/05/26"), [])


class TestFiguresThatAreNotYears(unittest.TestCase):
    def test_amounts_percentages_and_longer_numbers_in_range(self):
        for text in ("$2,024 spent", "2,024 units", "a ratio of 20.24", "2024% growth",
                     "2024 % growth", "serial 12024", "£1999 each", "1999.5 hours"):
            with self.subTest(text=text):
                self.assertEqual(unstated_years(text, "No dates here."), [])


class TestPlaceholderYears(unittest.TestCase):
    def test_a_placeholder_year_is_flagged(self):
        self.assertEqual(unstated_years("Scorecard launched H2 202X.", "No dates here."), ["202X"])

    def test_unless_the_document_uses_it(self):
        self.assertEqual(unstated_years("the 202X plan", "Template: the 202X plan"), [])

    def test_each_year_is_named_once_in_order(self):
        self.assertEqual(unstated_years("FY 2023 ... FY 2023 ... Q2 2024", "No dates."), ["2023", "2024"])


class TestTheReaderIsTold(unittest.TestCase):
    """The warning must reach SkillOutput.warnings: the agent carries that list
    into PipelineResult.warnings, and the results view renders it."""

    SOURCE = "Quarterly Operations Briefing\n\nRevenue for the third quarter reached $4.2 million."

    def _run(self, reply: str, source: str = SOURCE):
        from core.skill_registry import SkillRegistry
        registry = SkillRegistry()
        registry.discover()
        skill = registry.instantiate("summarization", config={})
        skill._llm.api_keys = skill._llm.api_keys or ["test-key-not-used"]
        skill._llm.chat = lambda **kw: reply
        return skill.execute(SkillInput(data={"full_text": source}))

    def test_an_invented_year_is_named_in_a_warning(self):
        out = self._run("# Q3 2024 Operations Overview\n\nRevenue reached $4.2 million.")
        self.assertTrue(out.success)
        self.assertTrue(out.data["method"].startswith("llm_"))
        warning = " ".join(out.warnings)
        self.assertIn("the year 2024, which does not appear anywhere in the document", warning)
        self.assertIn("Other figures in the summary are not checked", warning)

    def test_a_summary_whose_years_are_in_the_document_carries_no_warning(self):
        out = self._run("# Q3 2026 Operations Overview", self.SOURCE + " Prepared 12 March 2026.")
        self.assertTrue(out.data["method"].startswith("llm_"))
        self.assertEqual(out.warnings, [])

    def test_several_years_are_listed_together(self):
        self.assertIn("the years 2023, 2024 and 202X, which do not appear", year_warning(["2023", "2024", "202X"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Tests for the post-extraction fabrication check.

WHY IT EXISTS. Asked for Financial fields from a sales spreadsheet whose
Revenue column has no totals row, the model emitted **2,379,900** — exactly the
column sum, a figure that appears in no document — and presented it as an
extracted fact.

WHY A PROMPT INSTRUCTION IS NOT ENOUGH. The prompt already says "extract only
information explicitly stated", and the model's refusal turned out to be an
accident rather than a safeguard. Measured:

  interleaved, 20 trials each   app fixture 0/20 fabricated, eval fixture 20/20
                                Wilson 95% CI [0%, 16%] against [84%, 100%]

  the two documents differ by ONE LINE, and an A/B over the two variables in
  that line found BOTH perturbations independently cause it:

      separator + "Q3 Sales"        0/6   <- the only combination that refuses
      no separator + "Q3 Sales"     6/6
      separator + "Q3 FY26 Sales"   6/6
      no separator + "Q3 FY26 Sales" 6/6

Removing a 60-character separator line, or adding two characters to a sheet
name, flips it. So the behaviour is deterministic per document but the trigger
is cosmetic, which means the refusal cannot be relied on for any document we
have not already tested.

WHY NUMBERS ONLY. Prose can be legitimately paraphrased — "Total maintenance
spend: 1.94 million (Northern depot)" restates the document in words the
document does not use, and that is correct. A FIGURE cannot be paraphrased:
1.94 is either in the source or it is not.

WHY DROPPING IS SAFE ENOUGH. Measured across 45 fields extracted from the
eval's 8 documents, the check flagged 2 and both were genuine fabrications —
the column sum, and a `key_facts` entry asserting the year 2011, which appears
nowhere in that document. Zero false positives. Both are pinned below as
fixtures so the claim is re-derivable without API calls.

Run:
    pytest tests/test_extraction_fabrication.py -v
"""

from __future__ import annotations

import os
import unittest

from skills.structured_extraction_skill import (
    StructuredExtractionSkill, unverified_numbers,
)

#: The spreadsheet, as ExcelReaderSkill emits it. No totals row anywhere.
SALES_SOURCE = (
    "[Sheet: Q3 Sales]\n- Total Rows: 8\n\n"
    "Region          Product Units Unit Price Revenue\n"
    " North Orion Controller   320       1250  400000\n"
    " North     LIDAR Module   180        890  160200\n"
    " South Orion Controller   210       1250  262500\n"
    " South Service Contract    95       4200  399000\n"
)

#: The two real fabrications the measurement found.
FABRICATED_SUM = "2,379,900"        # 400000+160200+262500+399000+... , stated nowhere
FABRICATED_YEAR = "founded in 2011"  # general_ops contains only 2026


def _skill():
    from core.skill_registry import SkillRegistry
    registry = SkillRegistry()
    registry.discover()
    return registry.instantiate("structured_extraction", config={})


class TestTheCheckerCatchesTheRealCases(unittest.TestCase):
    def test_the_column_sum_is_flagged(self):
        """The case that started this: a total the model computed itself."""
        self.assertEqual(unverified_numbers(FABRICATED_SUM, SALES_SOURCE),
                         ["2379900"])

    def test_an_invented_year_is_flagged(self):
        """Found incidentally while measuring false positives: a `key_facts`
        entry asserting 2011 about a document whose only year is 2026."""
        self.assertEqual(
            unverified_numbers(FABRICATED_YEAR, "Prepared on 12 March 2026."),
            ["2011"])

    def test_a_figure_that_is_in_the_source_passes(self):
        for value in ("400000", "160200 units", "Revenue was 262500"):
            with self.subTest(value=value):
                self.assertEqual(unverified_numbers(value, SALES_SOURCE), [])

    def test_thousands_separators_do_not_cause_a_false_flag(self):
        """The model writes 400,000 where the sheet writes 400000. That is
        formatting, not a different claim."""
        self.assertEqual(unverified_numbers("400,000", SALES_SOURCE), [])

    def test_paraphrase_around_a_real_number_is_left_alone(self):
        """Only the numerals are checked, so restating a fact in new words is
        allowed as long as the figure is real."""
        self.assertEqual(
            unverified_numbers("North region revenue reached 400000 dollars",
                               SALES_SOURCE), [])

    def test_short_numbers_are_not_evidence(self):
        """Row counts, small integers and column indices appear everywhere and
        flagging them would drown the signal."""
        self.assertEqual(unverified_numbers("8 rows, 3 sheets, 95 units",
                                            SALES_SOURCE), [])

    def test_it_never_raises_on_odd_shapes(self):
        for value in (None, "", [], {}, 42, ["a", 1], {"k": "v"}):
            with self.subTest(value=repr(value)):
                self.assertIsInstance(unverified_numbers(value, SALES_SOURCE), list)

    def test_an_empty_source_flags_rather_than_passes(self):
        """Failing open here would make the check useless exactly when the
        source is missing."""
        self.assertEqual(unverified_numbers("2,379,900", ""), ["2379900"])


class TestDroppingIsSurgical(unittest.TestCase):
    def setUp(self):
        self.skill = _skill()

    def test_a_scalar_field_with_an_invented_figure_is_removed(self):
        cleaned, dropped = self.skill._drop_unverified(
            {"revenue": FABRICATED_SUM}, SALES_SOURCE)
        self.assertEqual(cleaned, {})
        self.assertEqual(dropped, [("revenue", ["2379900"])])

    def test_a_verified_field_survives_untouched(self):
        entities = {"revenue": "400000", "note": "no numbers here"}
        cleaned, dropped = self.skill._drop_unverified(entities, SALES_SOURCE)
        self.assertEqual(cleaned, entities)
        self.assertEqual(dropped, [])

    def test_only_the_bad_item_of_a_list_is_dropped(self):
        """`key_facts` held four sound facts and one invented one. Discarding
        the field would have lost the four to punish the one."""
        entities = {"key_facts": ["North sold 400000", "Founded in 2011",
                                  "South sold 262500"]}
        cleaned, dropped = self.skill._drop_unverified(entities, SALES_SOURCE)
        self.assertEqual(cleaned["key_facts"],
                         ["North sold 400000", "South sold 262500"])
        self.assertEqual(dropped, [("key_facts", ["2011"])])

    def test_a_list_whose_every_item_is_invented_loses_the_field(self):
        cleaned, dropped = self.skill._drop_unverified(
            {"key_facts": ["Founded in 2011", "Revenue 2,379,900"]}, SALES_SOURCE)
        self.assertNotIn("key_facts", cleaned)
        self.assertTrue(dropped)

    def test_the_escape_hatch_disables_it(self):
        """Silently removing data the model produced is a behaviour change and
        must be reversible without editing code."""
        os.environ["DOCAGENT_EXTRACTION_VERIFY"] = "false"
        try:
            cleaned, dropped = self.skill._drop_unverified(
                {"revenue": FABRICATED_SUM}, SALES_SOURCE)
            self.assertEqual(cleaned, {"revenue": FABRICATED_SUM})
            self.assertEqual(dropped, [])
        finally:
            os.environ.pop("DOCAGENT_EXTRACTION_VERIFY", None)

    def test_the_hatch_is_off_unless_explicitly_set(self):
        for value in ("", "true", "yes", "1", "anything"):
            with self.subTest(value=value):
                os.environ["DOCAGENT_EXTRACTION_VERIFY"] = value
                try:
                    cleaned, _d = self.skill._drop_unverified(
                        {"revenue": FABRICATED_SUM}, SALES_SOURCE)
                    self.assertEqual(cleaned, {})
                finally:
                    os.environ.pop("DOCAGENT_EXTRACTION_VERIFY", None)


class TestTheCallerIsToldWhatWasDropped(unittest.TestCase):
    """Removing a value silently would be its own failure: the reader would see
    an empty field with no way to know a number had been rejected."""

    def test_the_warning_names_the_field_and_the_figure(self):
        skill = _skill()
        real = skill._llm.chat
        skill._llm.chat = lambda **kw: '{"revenue": "2,379,900"}'
        skill._llm._last_finish_reason = "stop"
        skill._llm._last_failure = None
        try:
            if not skill._llm.available:
                self.skipTest("no LLM configured")
            from skills.structured_extraction_skill import _DOMAIN_SCHEMAS
            entities, method, warning = skill._llm_extract(
                SALES_SOURCE, "normal_document", "Financial",
                _DOMAIN_SCHEMAS["Financial"])
        finally:
            skill._llm.chat = real
        self.assertEqual(entities, {})
        self.assertTrue(method.startswith("llm_"))
        self.assertIn("revenue", warning)
        self.assertIn("2379900", warning)
        self.assertIn("do not appear in the document", warning)

    def test_a_clean_extraction_carries_no_warning(self):
        skill = _skill()
        real = skill._llm.chat
        skill._llm.chat = lambda **kw: '{"revenue": "400000"}'
        skill._llm._last_finish_reason = "stop"
        skill._llm._last_failure = None
        try:
            if not skill._llm.available:
                self.skipTest("no LLM configured")
            from skills.structured_extraction_skill import _DOMAIN_SCHEMAS
            entities, _method, warning = skill._llm_extract(
                SALES_SOURCE, "normal_document", "Financial",
                _DOMAIN_SCHEMAS["Financial"])
        finally:
            skill._llm.chat = real
        self.assertEqual(entities, {"revenue": "400000"})
        self.assertIsNone(warning)


if __name__ == "__main__":
    unittest.main(verbosity=2)

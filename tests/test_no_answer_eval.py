"""Tests for the no-answer eval: scoring whether the model declines.

Every other case in both eval sets has an answer, so a model that simply always
answers scores perfectly on them. Nothing measured refusal, which is why the
md-10 fabrication had no number attached.

Two scoring subtleties, both learned by getting them wrong first:

* MENTION IS NOT ASSERTION. "The manual gives 45 pence for a private vehicle
  but states no motorcycle rate" contains a trap value and is exactly the right
  answer. Trap counts are a diagnostic; the verdict is whether it declined.

* DECLINING IS NOT ALWAYS RIGHT. On md-10's question the leave half is
  genuinely answerable, so a blanket refusal would be a regression. That case
  is scored on whether the fabricated figure appears, not on refusal.

Run:
    pytest tests/test_no_answer_eval.py -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "e2e" / "rag_eval"))

import run_eval as RE  # noqa: E402


class TestDeclineDetection(unittest.TestCase):
    def test_the_phrasings_the_model_actually_uses(self):
        """Taken from real replies, not invented."""
        for reply in (
            "The provided excerpts do not contain any information about emissions.",
            "Not found in these documents.",
            "The documents only list departmental numbers.",
            "The excerpts do not mention any Tier 5 incidents.",
            "I am unable to determine that from the context.",
            "No breakdown of light vans by depot is given.",
            "That figure is not stated in the provided documents.",
        ):
            with self.subTest(reply=reply[:40]):
                self.assertTrue(RE.declined(reply))

    def test_a_confident_answer_is_not_a_decline(self):
        for reply in (
            "The company employs 96 people.",
            "The rate is 45 pence per mile.",
            "Tier 1 incidents numbered 19, closing in 23.4 days on average.",
        ):
            with self.subTest(reply=reply[:40]):
                self.assertFalse(RE.declined(reply))

    def test_an_empty_reply_is_not_counted_as_a_decline(self):
        """A failed call must not read as good behaviour."""
        self.assertFalse(RE.declined(""))
        self.assertFalse(RE.declined(None))

    def test_detection_is_deliberately_broad(self):
        """Missing a decline understates how often the model already refuses.

        That error would make any prompt change look more effective than it is,
        which is the direction that matters here.
        """
        self.assertTrue(RE.declined("The context doesn't contain that figure."))
        self.assertTrue(RE.declined("This cannot be determined from the excerpts."))


class TestTrapDiagnostic(unittest.TestCase):
    def test_a_trap_value_is_reported_when_present(self):
        case = {"must_not_assert": ["96"]}
        self.assertEqual(RE.asserted_traps(case, "a total of 96 employees"), ["96"])

    def test_a_declining_reply_may_legitimately_mention_a_trap(self):
        """Which is why this is a diagnostic and not the verdict."""
        case = {"must_not_assert": ["45 pence"]}
        reply = ("The manual gives 45 pence per mile for a private vehicle but "
                 "states no motorcycle rate.")
        self.assertEqual(RE.asserted_traps(case, reply), ["45 pence"])
        self.assertTrue(RE.declined(reply), "and it declined, so it passes")

    def test_trap_matching_respects_value_boundaries(self):
        """96 must not be found inside 1,960."""
        case = {"must_not_assert": ["96"]}
        self.assertEqual(RE.asserted_traps(case, "the figure was 1,960"), [])

    def test_a_case_with_no_traps_reports_none(self):
        self.assertEqual(RE.asserted_traps({}, "anything at all"), [])


class TestEvalSetIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / "tests" / "e2e" / "rag_eval" / "eval_set.json"
        cls.spec = json.loads(path.read_text(encoding="utf-8"))
        cls.block = cls.spec["multi_doc"]
        cls.cases = cls.block["no_answer_cases"]

    def test_the_answerable_sets_are_untouched(self):
        """A change that makes the model decline must not shrink these."""
        self.assertEqual(len(self.spec["cases"]), 39)
        self.assertEqual(len(self.block["cases"]), 13)

    def test_all_three_requested_shapes_are_covered(self):
        self.assertEqual(
            {c["category"] for c in self.cases},
            {"no_answer_anywhere", "answer_absent_document", "near_miss"})

    def test_every_category_is_documented(self):
        self.assertEqual({c["category"] for c in self.cases},
                         set(self.block["_about"]["no_answer_categories"]))

    def test_ids_are_unique_across_both_case_lists(self):
        ids = [c["id"] for c in self.cases] + [c["id"] for c in self.block["cases"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_case_says_what_it_expects(self):
        for case in self.cases:
            with self.subTest(case=case["id"]):
                self.assertIn("expects_decline", case)
                self.assertIsInstance(case["expects_decline"], bool)

    def test_a_case_that_should_not_decline_names_a_trap(self):
        """Otherwise it has no pass criterion at all."""
        for case in self.cases:
            if not case["expects_decline"]:
                with self.subTest(case=case["id"]):
                    self.assertTrue(case.get("must_not_assert"))

    def test_every_case_explains_why_the_corpus_cannot_answer_it(self):
        """A no-answer case is only trustworthy if the claim is checkable."""
        for case in self.cases:
            with self.subTest(case=case["id"]):
                self.assertGreater(len(case.get("note", "")), 40)

    def test_the_near_miss_category_is_the_largest(self):
        """It is the hard shape and the one that motivated the work."""
        from collections import Counter
        counts = Counter(c["category"] for c in self.cases)
        self.assertEqual(counts.most_common(1)[0][0], "near_miss")


if __name__ == "__main__":
    unittest.main(verbosity=2)

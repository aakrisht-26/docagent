"""Tests for the retrieval-eval scoring helpers.

The eval is the instrument every retrieval claim in this repo rests on, and a
silently lenient instrument is worse than no instrument. Three of these guard
mistakes already made and corrected while measuring cross-document retrieval:

* `answer_hit` was scoring TYPOGRAPHY. Three of the thirteen cross-document
  cases were marked wrong because the model wrote "45 pence" with a narrow
  no-break space. Reported as-is, that would have made cross-corpus answering
  look materially worse than it is.

* Reading `match: "all"` as a conjunction over `expected_answer_contains` failed
  two fully correct replies. That field lists alternative PHRASINGS of one fact;
  `match` constrains SOURCES. Conjunction over facts needs its own field.

* `ambiguous_labels` and `label_collisions` describe the actual defect in
  handing the single-document selector a corpus, so they must not overstate it.

Run:
    pytest tests/test_rag_eval.py -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "e2e" / "rag_eval"))

import run_eval as RE  # noqa: E402


class TestAnswerMatching(unittest.TestCase):
    def test_any_of_the_listed_phrasings_counts(self):
        """The list is alternative spellings of ONE fact, not several facts."""
        case = {"expected_answer_contains": ["21,500,000", "21.5"]}
        self.assertTrue(RE.answer_hit(case, "a combined total of $21,500,000"))
        self.assertTrue(RE.answer_hit(case, "about 21.5 million"))

    def test_match_all_does_not_make_the_phrasings_a_conjunction(self):
        """`match` constrains sources; it says nothing about prose.

        lg-pdf-05 answered "Nineteen Tier 1 incidents ... 23.4 days" — correct,
        and failed by a conjunction reading because it spelled the number.
        """
        case = {"match": "all", "expected_answer_contains": ["19", "23.4"]}
        self.assertTrue(RE.answer_hit(case, "Nineteen incidents, mean 23.4 days"))

    def test_expected_answer_all_requires_every_fragment(self):
        case = {"expected_answer_all": ["four year", "seven years"]}
        self.assertTrue(RE.answer_hit(case, "a four year cycle, and seven years for tractors"))
        self.assertFalse(RE.answer_hit(case, "seven years for tractor units"),
                         "half an answer to a two-part question is wrong")

    def test_expected_answer_all_wins_over_the_any_list(self):
        case = {"expected_answer_all": ["612", "9,370"],
                "expected_answer_contains": ["612"]}
        self.assertFalse(RE.answer_hit(case, "the fleet has 612 vehicles"))

    def test_no_expectations_is_not_a_free_pass(self):
        self.assertFalse(RE.answer_hit({}, "anything at all"))

    def test_commas_in_numbers_are_ignored(self):
        case = {"expected_answer_contains": ["21,500,000"]}
        self.assertTrue(RE.answer_hit(case, "the total was 21500000 dollars"))

    def test_unicode_spaces_do_not_fail_a_correct_answer(self):
        """U+202F is what the model actually emitted on md-01, md-04 and md-11."""
        for gap in (" ", " ", " ", " "):
            with self.subTest(codepoint=f"U+{ord(gap):04X}"):
                case = {"expected_answer_contains": ["45 pence"]}
                self.assertTrue(RE.answer_hit(case, f"the rate is 45{gap}pence per mile"))

    def test_unicode_dashes_do_not_fail_a_correct_answer(self):
        case = {"expected_answer_contains": ["fixed-price"]}
        self.assertTrue(RE.answer_hit(case, "a fixed‑price agreement"))

    def test_normalisation_never_makes_a_wrong_answer_right(self):
        """The folding is about formatting, so it must not touch the value."""
        case = {"expected_answer_contains": ["9,065"]}
        self.assertFalse(RE.answer_hit(case, "the average was 9,605 dollars"))
        self.assertFalse(RE.answer_hit(case, "no figure is given"))


class TestMultiDocSources(unittest.TestCase):
    """A cross-document source is a (document, page) PAIR."""

    def test_expected_pairs_reads_the_doc_and_source(self):
        case = {"expected_sources": [{"doc": "a.pdf", "source": 3},
                                     {"doc": "b.xlsx", "source": "Q3 Revenue"}]}
        self.assertEqual(RE.expected_pairs(case),
                         [("a.pdf", 3), ("b.xlsx", "Q3 Revenue")])

    def test_same_page_number_in_a_different_document_is_a_different_source(self):
        """The defect the whole cross-document set exists to measure."""
        case = {"expected_sources": [{"doc": "manual.pdf", "source": 3}],
                "match": "any"}
        self.assertFalse(RE.multi_hit(case, [("report.pdf", 3)]),
                         "page 3 of another document must not count as a hit")
        self.assertTrue(RE.multi_hit(case, [("manual.pdf", 3)]))

    def test_match_all_needs_every_pair(self):
        case = {"expected_sources": [{"doc": "a.pdf", "source": 1},
                                     {"doc": "b.pdf", "source": 5}],
                "match": "all"}
        self.assertFalse(RE.multi_hit(case, [("a.pdf", 1)]))
        self.assertTrue(RE.multi_hit(case, [("a.pdf", 1), ("b.pdf", 5)]))

    def test_match_any_needs_only_one_pair(self):
        case = {"expected_sources": [{"doc": "a.pdf", "source": 1},
                                     {"doc": "b.pdf", "source": 5}],
                "match": "any"}
        self.assertTrue(RE.multi_hit(case, [("b.pdf", 5)]))


class TestAmbiguityAndCollisions(unittest.TestCase):
    def test_a_label_used_by_one_document_is_not_ambiguous(self):
        corpus = [{"document": "a.pdf", "page_or_sheet": 1},
                  {"document": "a.pdf", "page_or_sheet": 2}]
        self.assertEqual(RE.ambiguous_labels(corpus), {})

    def test_a_label_shared_by_two_documents_is_ambiguous(self):
        corpus = [{"document": "a.pdf", "page_or_sheet": 1},
                  {"document": "b.pdf", "page_or_sheet": 1},
                  {"document": "a.pdf", "page_or_sheet": 9}]
        self.assertEqual(RE.ambiguous_labels(corpus), {1: ["a.pdf", "b.pdf"]})

    def test_sheet_names_can_collide_too(self):
        """Both workbook fixtures have a Headcount sheet, so this is real."""
        corpus = [{"document": "big.xlsx", "page_or_sheet": "Headcount"},
                  {"document": "small.xlsx", "page_or_sheet": "Headcount"}]
        self.assertIn("Headcount", RE.ambiguous_labels(corpus))

    def test_same_document_passages_are_not_reported_as_collisions(self):
        """Deduping passages of one page is the intended behaviour, not a bug."""
        corpus = [{"document": "a.pdf", "page_or_sheet": 3},
                  {"document": "a.pdf", "page_or_sheet": 3},
                  {"document": "a.pdf", "page_or_sheet": 4}]
        self.assertEqual(RE.label_collisions(corpus, [0.9, 0.8, 0.7]), [])

    def test_a_cross_document_collision_is_reported_with_its_blocker(self):
        corpus = [{"document": "report.pdf", "page_or_sheet": 3},
                  {"document": "manual.pdf", "page_or_sheet": 3},
                  {"document": "report.pdf", "page_or_sheet": 7}]
        lost = RE.label_collisions(corpus, [0.55, 0.45, 0.40], limit=2)
        self.assertEqual([p for p, _, _ in lost], [("manual.pdf", 3)])
        self.assertEqual(lost[0][2], ("report.pdf", 3))

    def test_collisions_are_only_counted_while_slots_remain(self):
        """Past the slot budget nothing is being *lost* to the label key."""
        corpus = [{"document": "a.pdf", "page_or_sheet": i} for i in range(1, 5)]
        corpus.append({"document": "b.pdf", "page_or_sheet": 1})
        scores = [0.9, 0.8, 0.7, 0.6, 0.1]
        self.assertEqual(RE.label_collisions(corpus, scores, limit=3), [])


class TestCorpusLoading(unittest.TestCase):
    def test_short_name_strips_the_prefix_and_extension(self):
        self.assertEqual(RE.short_name("sample_large_report.pdf"), "large_report")
        self.assertEqual(RE.short_name("other.xlsx"), "other")

    def test_cited_documents_finds_a_named_file(self):
        fixtures = ["sample_large_report.pdf", "sample_dense_manual.pdf"]
        self.assertEqual(RE.cited_documents("see sample_large_report.pdf", fixtures),
                         ["sample_large_report.pdf"])
        self.assertEqual(RE.cited_documents("see the dense manual", fixtures),
                         ["sample_dense_manual.pdf"])

    def test_cited_documents_is_empty_when_nothing_is_named(self):
        """The current baseline: the context carries no document identity."""
        fixtures = ["sample_large_report.pdf"]
        self.assertEqual(RE.cited_documents("The answer is on Page 3.", fixtures), [])
        self.assertEqual(RE.cited_documents("", fixtures), [])


class TestEvalSetIntegrity(unittest.TestCase):
    """The spec file is data, so nothing else would catch a typo in it."""

    @classmethod
    def setUpClass(cls):
        path = ROOT / "tests" / "e2e" / "rag_eval" / "eval_set.json"
        cls.spec = json.loads(path.read_text(encoding="utf-8"))
        cls.block = cls.spec["multi_doc"]

    def test_single_document_cases_are_untouched_by_the_multi_doc_work(self):
        self.assertEqual(len(self.spec["cases"]), 39)
        for case in self.spec["cases"]:
            self.assertIsInstance(case["expected_sources"], list)
            for source in case["expected_sources"]:
                self.assertNotIsInstance(
                    source, dict,
                    f"{case['id']}: single-document sources stay bare labels")

    def test_every_multi_doc_case_id_is_unique(self):
        ids = [c["id"] for c in self.block["cases"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_expected_document_is_in_the_declared_corpus(self):
        corpus = set(self.block["corpus"])
        for case in self.block["cases"]:
            for source in case["expected_sources"]:
                self.assertIn(source["doc"], corpus, f"{case['id']} names a stray fixture")

    def test_the_corpus_fixtures_all_exist(self):
        samples = ROOT / "tests" / "e2e" / "samples"
        for fixture in self.block["corpus"]:
            self.assertTrue((samples / fixture).exists(),
                            f"missing {fixture}; run python tests/e2e/make_samples.py")

    def test_all_four_requested_case_shapes_are_covered(self):
        want = {"one_of_many", "topical_sibling", "cross_document", "wrong_document"}
        self.assertEqual({c["category"] for c in self.block["cases"]}, want)

    def test_every_category_is_documented(self):
        documented = set(self.block["_about"]["categories"])
        self.assertEqual({c["category"] for c in self.block["cases"]}, documented)

    def test_multi_source_cases_declare_match_all(self):
        for case in self.block["cases"]:
            if len(case["expected_sources"]) > 1:
                self.assertEqual(case.get("match"), "all",
                                 f"{case['id']} needs every source, so say so")

    def test_cases_needing_two_facts_use_the_conjunction_field(self):
        """A two-part question scored with `any` credits half an answer."""
        for case in self.block["cases"]:
            if len({s["doc"] for s in case["expected_sources"]}) > 1:
                self.assertIn("expected_answer_all", case,
                              f"{case['id']} spans documents, so both facts are required")

    def test_every_case_has_something_to_check_the_answer_against(self):
        for case in self.block["cases"]:
            self.assertTrue(
                case.get("expected_answer_all") or case.get("expected_answer_contains"),
                f"{case['id']} has no answer expectation")

    def test_the_corpus_really_does_have_colliding_page_labels(self):
        """If it did not, the set would not be testing what it claims to."""
        self.assertGreaterEqual(len(self.block["corpus"]), 2)
        self.assertIn("sample_report.pdf", self.block["corpus"],
                      "the small fixtures are what make page 1 ambiguous")


if __name__ == "__main__":
    unittest.main(verbosity=2)

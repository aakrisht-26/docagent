"""Integrity of the dilution probe fixture.

Built to test one hypothesis: that a fact buried in a mixed-topic page loses
rank because the page embeds as something else. `md-10` is the case that
suggested it.

The probe REFUTED it. Rank loss tracks competition, not heterogeneity: both
losses are on the page with the FEWEST topics, and the six-topic executive
summary beats its dedicated competitor. Measured heterogeneity correlates with
rank at -0.084 across the twelve cases — no relationship, and the sign runs the
wrong way for the hypothesis.

These tests guard the fixture's ability to have shown that, not the conclusion:
the factorial must stay balanced, the controls must stay present, and the pages
must stay comparable in length. A probe that loses its controls proves nothing
whichever way it comes out.

Run:
    pytest tests/test_dilution_probe.py -v
"""

from __future__ import annotations

import json
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "e2e" / "rag_eval"))


def _pages():
    import fixture_content as FC
    return FC.MIXED_PDF_PAGES, FC.MIXED_PDF_COMPETITORS


class TestTheFactorialIsBalanced(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / "tests" / "e2e" / "rag_eval" / "eval_set.json"
        cls.cases = json.loads(path.read_text(encoding="utf-8"))["dilution_probe"]["cases"]

    def test_every_topic_count_meets_every_depth(self):
        """A 3x3 with a hole in it cannot separate the two variables."""
        core = [c for c in self.cases if "competitor" not in c]
        cells = {(c["topics"], c["depth"]) for c in core}
        for topics in (1, 2, 6):
            for depth in ("lead", "middle", "final"):
                with self.subTest(cell=(topics, depth)):
                    self.assertIn((topics, depth), cells)

    def test_the_single_topic_controls_are_present(self):
        """The load-bearing part. Without them a mixed page that ranks badly
        cannot be told from a page that is simply long."""
        controls = [c for c in self.cases if c["topics"] == 1]
        self.assertGreaterEqual(len(controls), 3)
        self.assertEqual({c["depth"] for c in controls if "competitor" not in c},
                         {"lead", "middle", "final"})

    def test_the_competitor_cases_hold_depth_constant(self):
        """They vary only the answer page's topic count, so a rank loss there
        is attributable to one thing."""
        comp = [c for c in self.cases if "competitor" in c]
        self.assertEqual({c["depth"] for c in comp}, {"lead"})
        self.assertEqual(sorted(c["topics"] for c in comp), [1, 2, 6])

    def test_each_case_targets_a_distinct_page(self):
        core = [c for c in self.cases if "competitor" not in c]
        self.assertEqual(len({c["page"] for c in core}), len(core))


class TestThePagesAreComparable(unittest.TestCase):
    def test_length_is_held_roughly_constant(self):
        """Length is the confound the controls exist to rule out, so it must
        not vary much across cells."""
        pages, _ = _pages()
        lengths = [len(body.split()) for _, body in pages]
        self.assertLess(max(lengths) - min(lengths), 40,
                        "pages differ enough in length to confound topic count")

    def test_a_competitor_does_not_contain_the_answer_it_competes_with(self):
        """Otherwise it is a second correct source, not a distractor."""
        _, competitors = _pages()
        body = " ".join(b for _, b in competitors)
        for answer in ("4.7 million", "890,000", "268 people"):
            with self.subTest(answer=answer):
                self.assertNotIn(answer, body)

    def test_the_six_topic_pages_really_do_cover_several_subjects(self):
        """If they were secretly narrow the hypothesis was never tested."""
        pages, _ = _pages()
        summary = pages[6][1]      # 7. Estates Overview
        subjects = ("maintenance", "grounds", "security", "catering",
                    "portering", "administration")
        self.assertGreaterEqual(sum(s in summary for s in subjects), 5)


class TestTheFixtureIsWired(unittest.TestCase):
    def test_the_pdf_exists(self):
        self.assertTrue((ROOT / "tests" / "e2e" / "samples"
                         / "sample_mixed_topics.pdf").exists(),
                        "run: python tests/e2e/make_samples.py")

    def test_the_probe_does_not_touch_the_headline_sets(self):
        """Its whole point is to be measurable without moving quoted numbers."""
        spec = json.loads((ROOT / "tests" / "e2e" / "rag_eval" / "eval_set.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(len(spec["cases"]), 39)
        self.assertEqual(len(spec["multi_doc"]["cases"]), 13)
        self.assertNotIn("sample_mixed_topics.pdf",
                         {c["fixture"] for c in spec["cases"]})


if __name__ == "__main__":
    unittest.main(verbosity=2)

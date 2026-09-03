"""Tests for the structured-extraction eval, so the instrument cannot rot.

An eval is code, and a broken eval is worse than none: it reports a number
nobody re-derives. Two things are pinned here.

THE CEILING HAS TO BE REACHABLE. `RESULTS.md` claims 28/28 is attainable
because every expected value is present in its document. That is a claim about
the fixtures, and `test_every_expected_value_appears_in_its_document` checks it
rather than trusting it. If a fixture is edited and an expectation is left
behind, the eval would quietly become unwinnable and the score would look like
an extraction regression.

THE MATCHER HAS TO STAY STRICT. Five of the expectations in the first version
of this eval were wrong, and all five made the extractor look worse than it is,
so every correction moved the number up. That is the direction this project has
been caught by four times. The judge tests below fix the semantics in place:
present-and-right, present-and-wrong, and absent are three outcomes, and a
distractor makes an answer wrong however much correct material sits beside it.

Run:
    pytest tests/test_extraction_eval.py -v
"""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "tests" / "e2e" / "extraction_eval"


def _load_runner():
    """Load the eval runner by path, LEAVING sys.path AS IT WAS FOUND.

    Both eval packages contain a `run_eval` and a `fixture_content`, so a plain
    import resolves to whichever directory happens to be earlier on sys.path.
    Loading by explicit path fixes that for this module.

    The restore is not tidiness. The first version of this loader inserted the
    extraction_eval directory at sys.path[0] and left it there, so every test
    importing `fixture_content` AFTERWARDS got this eval's fixtures instead of
    its own. It broke tests/test_questionnaire_fixture.py, which passes alone
    and failed in the full suite -- an order-dependent failure in an unrelated
    file, which is among the worst kinds to debug.

    `fixture_content` is also dropped from sys.modules on the way out, since a
    cached entry pointing at this directory would shadow the other eval's copy
    just as effectively as the path entry did.
    """
    import sys
    saved_path = list(sys.path)
    sys.path.insert(0, str(EVAL_DIR))
    try:
        spec = importlib.util.spec_from_file_location(
            "extraction_run_eval", EVAL_DIR / "run_eval.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = saved_path
        sys.modules.pop("fixture_content", None)


RUNNER = _load_runner()
CASES = json.loads((EVAL_DIR / "eval_set.json").read_text(encoding="utf-8"))["cases"]


class TestTheCeilingIsReachable(unittest.TestCase):
    def test_every_expected_value_appears_in_its_document(self):
        """Otherwise the eval is unwinnable and reads as an extractor bug."""
        DOCUMENTS = RUNNER.DOCUMENTS
        for case in CASES:
            document = RUNNER._normalise_for_match(DOCUMENTS[case["fixture"]])
            for field, spec in case["fields"].items():
                for fragment in spec.get("must_contain", []):
                    with self.subTest(case=case["id"], field=field, frag=fragment):
                        self.assertIn(RUNNER._normalise_for_match(fragment),
                                      document,
                                      "expected value is not in the fixture")

    def test_every_alternative_appears_somewhere(self):
        """`must_contain_any` groups must have at least one satisfiable member."""
        DOCUMENTS = RUNNER.DOCUMENTS
        for case in CASES:
            document = RUNNER._normalise_for_match(DOCUMENTS[case["fixture"]])
            for field, spec in case["fields"].items():
                for group in spec.get("must_contain_any", []):
                    with self.subTest(case=case["id"], field=field):
                        self.assertTrue(
                            any(RUNNER._normalise_for_match(g) in document
                                for g in group),
                            f"none of {group} appears in the document")

    def test_every_distractor_appears_in_its_document(self):
        """A distractor that is not in the text is not a distractor.

        It cannot be emitted, so asserting its absence tests nothing and pads
        the eval with cases that always pass.
        """
        DOCUMENTS = RUNNER.DOCUMENTS
        for case in CASES:
            document = RUNNER._normalise_for_match(DOCUMENTS[case["fixture"]])
            for field, spec in case["fields"].items():
                for fragment in spec.get("must_not_contain", []):
                    with self.subTest(case=case["id"], field=field, frag=fragment):
                        self.assertIn(RUNNER._normalise_for_match(fragment),
                                      document,
                                      "distractor is absent from the fixture, "
                                      "so this assertion can never fail")


class TestThisModuleDoesNotPoisonOtherTests(unittest.TestCase):
    """Loading the eval must not change what `fixture_content` means globally.

    This is a regression test for a bug in this very file. The first version
    left the extraction_eval directory on sys.path, so every test importing
    `fixture_content` after it silently got the wrong eval's fixtures.
    tests/test_questionnaire_fixture.py passed alone and failed in the full
    suite, which is the most expensive kind of failure to trace.
    """

    def test_the_eval_directory_is_not_left_on_sys_path(self):
        import sys
        self.assertNotIn(str(EVAL_DIR), sys.path)

    def test_fixture_content_is_not_left_cached(self):
        """A cached sys.modules entry shadows the other eval just as well as a
        path entry, so removing only the path would not be enough."""
        import sys
        cached = sys.modules.get("fixture_content")
        if cached is not None:
            self.assertNotIn(str(EVAL_DIR), getattr(cached, "__file__", ""))

    def test_another_eval_still_imports_its_own_fixtures(self):
        """The end the user actually cares about: the retrieval eval's module
        must still resolve to the retrieval eval's content."""
        import sys
        saved = list(sys.path)
        sys.path.insert(0, str(ROOT / "tests" / "e2e" / "rag_eval"))
        try:
            sys.modules.pop("fixture_content", None)
            import fixture_content as other
            self.assertIn("rag_eval", other.__file__)
        finally:
            sys.path[:] = saved
            sys.modules.pop("fixture_content", None)


class TestTheJudge(unittest.TestCase):
    def test_absent_is_missing_not_wrong(self):
        verdict, _ = RUNNER.judge_field(None, {"must_contain": ["412.7"]})
        self.assertEqual(verdict, RUNNER.MISSING)

    def test_empty_is_missing_not_wrong(self):
        for value in ("", "   ", [], {}):
            with self.subTest(value=repr(value)):
                verdict, _ = RUNNER.judge_field(value, {"must_contain": ["x"]})
                self.assertEqual(verdict, RUNNER.MISSING)

    def test_right_value_is_correct(self):
        verdict, _ = RUNNER.judge_field(
            "412.7 million dollars",
            {"must_contain": ["412.7"], "must_not_contain": ["388.1"]})
        self.assertEqual(verdict, RUNNER.CORRECT)

    def test_a_distractor_makes_it_wrong_even_beside_the_right_answer(self):
        """The anti-hedging rule. Listing every candidate is not extraction,
        and without this an extractor could pass by dumping the document."""
        verdict, reason = RUNNER.judge_field(
            "revenue was 412.7 million, up from 388.1 million",
            {"must_contain": ["412.7"], "must_not_contain": ["388.1"]})
        self.assertEqual(verdict, RUNNER.WRONG)
        self.assertIn("388.1", reason)

    def test_a_missing_requirement_is_wrong_not_missing(self):
        """The field was emitted, so the extractor made a claim. A claim that
        omits the required value is wrong, not absent."""
        verdict, _ = RUNNER.judge_field(
            "some other number", {"must_contain": ["412.7"]})
        self.assertEqual(verdict, RUNNER.WRONG)

    def test_alternatives_accept_either_spelling(self):
        """Added because "Third Quarter, Fiscal Year 2025" was scored wrong for
        not saying "Q3", which is scoring format rather than correctness."""
        spec = {"must_contain": [], "must_contain_any": [["q3", "third quarter"]]}
        for value in ("Q3 2025", "Third Quarter, Fiscal Year 2025"):
            with self.subTest(value=value):
                self.assertEqual(RUNNER.judge_field(value, spec)[0], RUNNER.CORRECT)

    def test_alternatives_still_reject_the_wrong_period(self):
        spec = {"must_contain": [], "must_contain_any": [["q3", "third quarter"]]}
        self.assertEqual(RUNNER.judge_field("Q4 2025", spec)[0], RUNNER.WRONG)

    def test_it_reads_lists_and_dicts_the_same_as_strings(self):
        """The model returns strings, lists and occasionally lists of dicts for
        the same field. Scoring must see one thing."""
        spec = {"must_contain": ["amoxicillin", "metformin"]}
        for value in (
            "amoxicillin 500 mg, metformin 850 mg",
            ["amoxicillin 500 mg", "metformin 850 mg"],
            [{"name": "amoxicillin", "dose": "500 mg"},
             {"name": "metformin", "dose": "850 mg"}],
        ):
            with self.subTest(shape=type(value).__name__):
                self.assertEqual(RUNNER.judge_field(value, spec)[0], RUNNER.CORRECT)

    def test_the_matcher_has_word_boundaries(self):
        """Borrowed from the retrieval eval precisely for this: an unbounded
        substring test would let "2.1" satisfy "2.14"."""
        verdict, _ = RUNNER.judge_field("2.1 dollars", {"must_contain": ["2.14"]})
        self.assertEqual(verdict, RUNNER.WRONG)


class TestTheRegexPathIsScorable(unittest.TestCase):
    """The fallback is a separately measurable path, like `--keyword` on the
    retrieval eval. Its measured score is 0/28, and that is the cost of the
    silent degradation, so the plumbing that produces it has to work."""

    def test_only_schema_fields_are_credited(self):
        """The fallback emits `percentages` and `emails`, which are fields of
        no schema and never reach the caller. Crediting them would report
        output the pipeline discards."""
        from skills.structured_extraction_skill import _DOMAIN_SCHEMAS
        for name, schema in _DOMAIN_SCHEMAS.items():
            with self.subTest(schema=name):
                self.assertNotIn("percentages", schema)
                self.assertNotIn("emails", schema)


if __name__ == "__main__":
    unittest.main(verbosity=2)

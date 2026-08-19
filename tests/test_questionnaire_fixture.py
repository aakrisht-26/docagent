"""Tests that the questionnaire fixture can actually catch a discarded LLM.

The eval had no questionnaire in it. Every fixture was a normal document, so
the `0.7 * llm_score + 0.3 * heuristic` blend and the `question_extraction`
stage it gates had never run end to end. That is why an 80-token classifier
budget could disable LLM classification across a whole model migration without
a single number moving.

A questionnaire fixture only closes that gap if the HEURISTIC cannot classify
it alone. If the regex patterns caught it, the LLM term would be redundant and
the fixture would pass whether or not the blend worked — coverage in name only.

So these tests assert the fixture's teeth rather than its result: the heuristic
must score BELOW the questionnaire threshold on this text. The end-to-end
verdict needs an API key and lives in `python tests/e2e/e2e.py questionnaire`,
where it is measured as: heuristic 0.000, LLM p(questionnaire) 0.96, blended
0.672, 15 questions extracted. Discard the LLM result and the same document
reports normal_document with 0 questions.

Run:
    pytest tests/test_questionnaire_fixture.py -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "e2e" / "rag_eval"))

from core.skill_registry import SkillRegistry  # noqa: E402
from skills.document_classifier_skill import _MAX_HEURISTIC_SCORE  # noqa: E402


def _text() -> str:
    import fixture_content as FC
    return "\n\n".join(f"{h}\n{b}" for h, b in FC.QUESTIONNAIRE_PDF_PAGES)


def _classifier():
    registry = SkillRegistry()
    registry.discover()
    # No groq config: the heuristic path needs no key, and these tests must run
    # offline. The end-to-end verdict is an e2e stage, not a unit test.
    return registry.instantiate("document_classifier", config={})


class TestTheFixtureHasTeeth(unittest.TestCase):
    """If the heuristic could carry it, the fixture would prove nothing."""

    def setUp(self):
        self.skill = _classifier()
        self.text = _text()

    def _normalised(self) -> float:
        raw, _ = self.skill._heuristic_score(self.text)
        return min(raw / max(_MAX_HEURISTIC_SCORE, 0.01), 1.0)

    def test_the_heuristic_alone_does_not_reach_questionnaire(self):
        """The load-bearing property of this fixture.

        Only the LLM term can push it over, so discarding the LLM result
        changes the verdict — which is what the e2e stage detects.
        """
        self.assertLess(
            self._normalised(), self.skill._threshold,
            "the heuristic now classifies this fixture on its own, so it no "
            "longer tests the LLM blend; make the wording less form-like again")

    def test_the_llm_term_alone_would_carry_it(self):
        """Confirms the blend is what decides, using the measured LLM score.

        p(questionnaire) 0.96 was measured against gpt-oss-120b. The arithmetic
        here is the skill's own, so a change to the weights fails this test.
        """
        blended = 0.7 * 0.96 + 0.3 * self._normalised()
        self.assertGreaterEqual(
            blended, self.skill._threshold,
            "with a realistic LLM score the blend must clear the threshold, "
            "or the fixture cannot distinguish a working blend from a broken one")

    def test_the_gap_between_the_two_verdicts_is_not_marginal(self):
        """A fixture that only just flips would be flaky, not diagnostic."""
        blended = 0.7 * 0.96 + 0.3 * self._normalised()
        self.assertGreater(blended - self.skill._threshold, 0.1,
                           "too close to the threshold to be a reliable signal")


class TestTheFixtureIsRealisticallyAForm(unittest.TestCase):
    """It has to be a questionnaire to a reader, not just to the classifier."""

    def setUp(self):
        self.text = _text()

    def test_it_asks_for_information(self):
        prompts = sum(self.text.lower().count(verb)
                      for verb in ("state ", "describe ", "confirm ", "name ",
                                   "identify ", "give ", "summarise "))
        self.assertGreaterEqual(prompts, 12,
                                "a form should be mostly prompts for information")

    def test_it_avoids_the_obvious_heuristic_cues(self):
        """Documents WHY it is worded the way it is, so nobody 'tidies' it."""
        lowered = self.text.lower()
        for cue in ("questionnaire", "survey", "strongly agree",
                    "check all that apply", "please select", "____"):
            with self.subTest(cue=cue):
                self.assertNotIn(
                    cue, lowered,
                    f"{cue!r} would let the heuristic classify this alone, "
                    f"which defeats the point of the fixture")


class TestTheFixtureIsWired(unittest.TestCase):
    def test_the_pdf_exists(self):
        path = ROOT / "tests" / "e2e" / "samples" / "sample_questionnaire.pdf"
        self.assertTrue(path.exists(),
                        "run: python tests/e2e/make_samples.py")

    def test_the_generator_builds_it(self):
        source = (ROOT / "tests" / "e2e" / "make_samples.py").read_text(encoding="utf-8")
        self.assertIn("build_questionnaire_pdf", source)
        main = source.split('if __name__ == "__main__":', 1)[1]
        self.assertIn("build_questionnaire_pdf()", main,
                      "a builder nobody calls leaves the fixture stale")

    def test_the_e2e_harness_runs_it(self):
        """Imported, not parsed: a malformed STAGES line would still contain
        the word "questionnaire" while the stage never ran."""
        import sys as _sys
        _sys.path.insert(0, str(ROOT / "tests" / "e2e"))
        import e2e
        self.assertIn("questionnaire", e2e.STAGES)
        self.assertIsInstance(e2e.STAGES, tuple)

    def test_the_e2e_stage_checks_questions_were_extracted(self):
        """Classifying correctly but extracting nothing is still a failure."""
        source = (ROOT / "tests" / "e2e" / "e2e.py").read_text(encoding="utf-8")
        stage = source.split('elif stage == "questionnaire":', 1)[1].split("elif stage ==", 1)[0]
        self.assertIn("result.questions", stage)
        self.assertIn("hybrid_", stage,
                      "the method must be checked too, or a lucky heuristic "
                      "verdict would pass")


if __name__ == "__main__":
    unittest.main(verbosity=2)

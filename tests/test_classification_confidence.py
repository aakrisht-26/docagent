"""Tests for what `classification_confidence` means and how it is shown.

The field is P(QUESTIONNAIRE), not confidence in the verdict. Six of the seven
e2e fixtures classify as `normal_document`, so the raw number reads backwards on
almost everything: 0.03 means the classifier was 97% sure, displayed as 3%.

That was flagged as a suspected defect in three consecutive sessions before
anyone traced it. The logic was correct every time. What was wrong was the
presentation, and only on some surfaces — the UI had been corrected, while the
downloadable markdown report, the classifier's own log line and the e2e harness
had not. The harness is what kept making it look broken.

Run:
    pytest tests/test_classification_confidence.py -v
"""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from core.models import confidence_in_verdict

ROOT = Path(__file__).resolve().parents[1]


class TestConfidenceInVerdict(unittest.TestCase):
    def test_a_confident_normal_document_reads_high(self):
        """The case that made this look broken: 0.02 is the BEST outcome."""
        self.assertAlmostEqual(confidence_in_verdict(0.021, "normal_document"), 0.979)

    def test_a_confident_questionnaire_reads_high_too(self):
        self.assertAlmostEqual(confidence_in_verdict(0.672, "questionnaire"), 0.672)

    def test_the_two_classes_are_not_treated_the_same(self):
        """The whole point: the same score means opposite things per class."""
        self.assertNotEqual(confidence_in_verdict(0.03, "normal_document"),
                            confidence_in_verdict(0.03, "questionnaire"))

    def test_an_uncertain_classification_reads_uncertain_either_way(self):
        for doc_type in ("normal_document", "questionnaire"):
            with self.subTest(doc_type=doc_type):
                self.assertAlmostEqual(confidence_in_verdict(0.5, doc_type), 0.5)

    def test_it_tolerates_the_shapes_a_reloaded_result_can_carry(self):
        self.assertEqual(confidence_in_verdict(None, "normal_document"), 1.0)
        self.assertEqual(confidence_in_verdict(0.0, ""), 1.0)

    def test_case_does_not_change_the_answer(self):
        self.assertEqual(confidence_in_verdict(0.672, "Questionnaire"),
                         confidence_in_verdict(0.672, "questionnaire"))


class TestEverySurfaceUsesIt(unittest.TestCase):
    """Four surfaces need the same answer; three of them were wrong."""

    def _src(self, path: str) -> str:
        return (ROOT / path).read_text(encoding="utf-8")

    def test_the_markdown_export_shows_verdict_confidence(self):
        """User-facing and downloadable — it read '2% confidence' for the
        classifier's most certain work."""
        src = self._src("core/pipeline_result.py")
        self.assertIn("confidence_in_verdict", src)

    def test_the_classifier_log_line_shows_both_numbers(self):
        src = self._src("skills/document_classifier_skill.py")
        self.assertIn("confident in this verdict", src)
        self.assertIn("p(questionnaire)=", src)

    def test_the_e2e_harness_shows_both_numbers(self):
        """This surface is why it was misread three sessions running."""
        src = self._src("tests/e2e/e2e.py")
        self.assertIn("confidence_in_verdict", src)
        self.assertIn("p(questionnaire)=", src)

    def test_the_ui_does_not_keep_a_second_copy_of_the_rule(self):
        src = self._src("ui/components/results_view.py")
        self.assertIn("confidence_in_verdict", src)
        self.assertNotIn("1.0 - score", src,
                         "the inversion must have exactly one definition")

    def test_the_stored_field_is_still_raw(self):
        """Changing it would be a data migration, not a display fix: it is
        persisted in history.db and consumed by the questionnaire threshold."""
        src = self._src("agents/document_agent.py")
        self.assertIn("classification_confidence=class_conf", src)
        self.assertNotIn("confidence_in_verdict", src)


class TestTheSkillActuallyRuns(unittest.TestCase):
    """Runtime cover, because the surface tests above are static.

    Those assert that source files mention `confidence_in_verdict`. They passed
    while the classifier raised NameError on every call, because the import was
    never added — a source-text assertion cannot see an unbound name. Anything
    checked only by reading source needs one execution path beside it.
    """

    def test_classifying_a_document_does_not_raise(self):
        from core.models import SkillInput
        from core.skill_registry import SkillRegistry
        registry = SkillRegistry()
        registry.discover()
        # No groq config: the heuristic path needs no key and still reaches the
        # verdict-confidence call that was broken.
        skill = registry.instantiate("document_classifier", config={})
        out = skill.safe_execute(SkillInput(data={"full_text": "A plain report about depot operations."}))
        self.assertTrue(out.success, out.error)
        self.assertIn(out.data.doc_type, ("normal_document", "questionnaire"))

    def test_a_form_like_document_still_classifies(self):
        from core.models import SkillInput
        from core.skill_registry import SkillRegistry
        registry = SkillRegistry()
        registry.discover()
        skill = registry.instantiate("document_classifier", config={})
        form = "\n".join([
            "Customer Satisfaction Survey",
            "Please rate your experience on a scale of 1 to 5.",
            "First name: ______________________",
            "Signature: _______________  Date: __/__/____",
            "I confirm the above is accurate.  Yes / No",
        ])
        out = skill.safe_execute(SkillInput(data={"full_text": form}))
        self.assertTrue(out.success, out.error)
        self.assertEqual(out.data.doc_type, "questionnaire",
                         "the heuristic alone should carry a textbook form")


class TestTheCeilingIsIntended(unittest.TestCase):
    """0.7 * llm + 0.3 * heuristic cannot exceed 0.70 when the heuristic is
    silent. That is a designed property, not a bug to normalise away."""

    def test_the_uncorroborated_bar_is_higher_than_the_bare_threshold(self):
        """Renormalising would cut required conviction from 0.571 to 0.400."""
        threshold = 0.4
        needed_silent = (threshold - 0.3 * 0.0) / 0.7
        self.assertGreater(needed_silent, threshold)
        self.assertAlmostEqual(needed_silent, 0.5714, places=3)

    def test_heuristic_support_lowers_the_bar_it_does_not_raise_it(self):
        bars = [(0.4 - 0.3 * n) / 0.7 for n in (0.0, 0.2, 0.5, 0.84)]
        self.assertEqual(bars, sorted(bars, reverse=True))

    def test_the_ceiling_still_clears_the_threshold_comfortably(self):
        """0.70 against a 0.4 threshold: the top of the scale being
        unreachable costs nothing, because nothing needs to reach it."""
        self.assertGreater(0.7 * 1.0, 0.4)

    def test_the_reasoning_is_written_down_at_the_blend(self):
        src = (ROOT / "skills" / "document_classifier_skill.py").read_text(encoding="utf-8")
        self.assertIn("CEILING IS INTENDED", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)

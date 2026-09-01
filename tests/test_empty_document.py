"""Tests for what the app says when there is no document to say it about.

A YouTube download failed. The pipeline reported the parse failure correctly at
the top of the page, and then, underneath it, announced:

    Normal Document · Domain: General · 100% confidence
    [confidence bar: 100%, green]
    0 words · 0 pages · 0 characters

Every number there was arithmetically correct. `classification_confidence` is
P(questionnaire); empty text scores 0 on the heuristic, the LLM never runs, so
p(questionnaire) = 0.0, and "confidence in the verdict" for a non-questionnaire
is 1 - 0.0 = 1.0. Nothing in the chain asked whether there had been any content
to classify, so a total absence of evidence was rendered as total certainty.

THE FIX IS THREE LAYERS, because three separate places each independently
invented a verdict:

  1. `confidence_in_verdict` was TOTAL. It treated everything that was not
     `questionnaire` as `normal_document`, so `unknown` — the doc_type a failed
     parse carries — mapped to 100%. It is now PARTIAL and returns None.
  2. `_dict_to_pipeline_result` defaulted a missing stored doc_type to
     `normal_document`, so a history row with no verdict acquired one on reload.
  3. The results banner's `else` branch said "Normal Document", so a live
     failed run was labelled a normal document regardless of doc_type.

Layer 1 alone would have left the banner lying in words while the number was
absent. Layer 3 alone would have left the markdown export and the history
reload wrong. They are genuinely independent.

WHY None RATHER THAN 0.0. "Confidence in the verdict" means P(the class we
chose), which is only defined if a class was chosen. Returning 0.0 would read
as a confident negative — an empty red bar asserting the classifier was sure of
something — rather than an absence. The Optional forces every caller to decide
what to render, which is why this file tests the callers and not just the
function.

WHY THE CLASSIFIER TEST IS HERE AND NOT IN THE E2E HARNESS. The agent gates on
`ParsedDocument.is_empty` before step 3, so the classifier's own empty-text
branch is unreachable through the pipeline. That was verified by mutation, not
assumed: reverting that branch to `normal_document` does NOT fail the `empty`
e2e stage, while reverting `confidence_in_verdict` does. An end-to-end test
cannot cover layer 3-and-below, so it is covered here.

The e2e side lives in `tests/e2e/e2e.py::empty`, which runs the real agent over
a real blank PDF.

Run:
    pytest tests/test_empty_document.py -v
"""

from __future__ import annotations

import unittest
from pathlib import Path

from core.models import CLASSIFIED_DOC_TYPES, confidence_in_verdict

ROOT = Path(__file__).resolve().parents[1]

#: Every doc_type that can actually reach the display layer, and what each must
#: render. Enumerated from the sources that set it: the classifier writes
#: "questionnaire" or "normal_document"; `_error_result` and the empty-parse
#: guard write "unknown"; a legacy history row can carry "" or None because the
#: column was added later.
DOC_TYPES_IN_THE_WILD = {
    "questionnaire":     True,   # a verdict
    "normal_document":   True,   # a verdict
    "unknown":           False,  # failed parse, failed download, empty document
    "":                  False,  # legacy history row
    None:                False,  # legacy history row, column absent
}


class TestTheThirdState(unittest.TestCase):
    """`confidence_in_verdict` is partial. That is the fix, not a wart."""

    def test_every_doc_type_in_the_wild_either_has_a_verdict_or_none(self):
        for doc_type, is_verdict in DOC_TYPES_IN_THE_WILD.items():
            with self.subTest(doc_type=doc_type):
                got = confidence_in_verdict(0.0, doc_type)
                if is_verdict:
                    self.assertIsNotNone(got)
                else:
                    self.assertIsNone(
                        got, f"{doc_type!r} is not a verdict; reporting a "
                             f"confidence for it is the original bug")

    def test_unknown_does_not_read_as_confidently_normal(self):
        """The exact defect. This returned 1.0 and was rendered 100% green."""
        self.assertIsNone(confidence_in_verdict(0.0, "unknown"))

    def test_it_is_not_zero_either(self):
        """0.0 would read as a confident negative rather than an absence, and
        would still draw a bar."""
        for doc_type in ("unknown", "", None):
            with self.subTest(doc_type=doc_type):
                self.assertIsNot(confidence_in_verdict(0.0, doc_type), 0.0)

    def test_a_real_verdict_is_untouched_by_the_fix(self):
        """The regression risk: making this partial must not disturb the two
        cases that were always right."""
        self.assertAlmostEqual(confidence_in_verdict(0.021, "normal_document"), 0.979)
        self.assertAlmostEqual(confidence_in_verdict(0.672, "questionnaire"), 0.672)

    def test_a_verdict_with_a_missing_score_is_still_a_verdict(self):
        """None SCORE and None VERDICT are different absences. A stored row can
        lack the number while still carrying a real classification."""
        self.assertEqual(confidence_in_verdict(None, "normal_document"), 1.0)

    def test_the_constant_matches_the_behaviour(self):
        for doc_type in CLASSIFIED_DOC_TYPES:
            with self.subTest(doc_type=doc_type):
                self.assertIsNotNone(confidence_in_verdict(0.5, doc_type))


class TestTheBannerAndTheNumberAgree(unittest.TestCase):
    """They are rendered together, so disagreeing is worse than either error.

    The banner was case-sensitive while the confidence maths lowercased, so
    "NORMAL_DOCUMENT" printed "Not classified" directly above a 97% meter.
    Both now normalise against the same constant.
    """

    def test_they_never_disagree_about_whether_a_verdict_exists(self):
        from ui.components.results_view import _banner_for
        cases = list(DOC_TYPES_IN_THE_WILD) + ["NORMAL_DOCUMENT", "Questionnaire"]
        for doc_type in cases:
            with self.subTest(doc_type=doc_type):
                label, _ = _banner_for(doc_type)
                confidence = confidence_in_verdict(0.03, doc_type)
                self.assertEqual(
                    label == "Not classified", confidence is None,
                    f"{doc_type!r}: banner says {label!r} but confidence is "
                    f"{confidence!r} — one of them is lying")

    def test_an_unclassified_document_is_not_called_a_normal_document(self):
        from ui.components.results_view import _banner_for
        for doc_type in ("unknown", "", None):
            with self.subTest(doc_type=doc_type):
                self.assertEqual(_banner_for(doc_type)[0], "Not classified")

    def test_the_two_real_classes_still_get_their_labels(self):
        from ui.components.results_view import _banner_for
        self.assertEqual(_banner_for("questionnaire")[0], "Questionnaire / Form")
        self.assertEqual(_banner_for("normal_document")[0], "Normal Document")


class TestTheClassifierDeclinesRatherThanGuesses(unittest.TestCase):
    """The layer the e2e harness cannot reach.

    The agent's `is_empty` gate means nothing arrives here today. Verified by
    mutation: reverting this branch leaves the `empty` e2e stage green. It is
    the same bug one layer down, armed for any caller that skips the gate.
    """

    def _classify(self, text: str):
        from core.models import SkillInput
        from core.skill_registry import SkillRegistry
        registry = SkillRegistry()
        registry.discover()
        skill = registry.instantiate("document_classifier", config={})
        return skill.safe_execute(SkillInput(data={"full_text": text}))

    def test_empty_text_is_not_classified_as_a_normal_document(self):
        for text in ("", "   \n\t  "):
            with self.subTest(text=text):
                out = self._classify(text)
                self.assertTrue(out.success, out.error)
                self.assertEqual(
                    out.data.doc_type, "unknown",
                    "there is nothing to classify; normal_document is a "
                    "verdict with no basis behind it")

    def test_the_declined_result_produces_no_confidence(self):
        """The two layers have to compose: declining is only useful if what it
        returns then renders as an absence."""
        out = self._classify("")
        self.assertIsNone(
            confidence_in_verdict(out.data.confidence, out.data.doc_type))

    def test_the_method_says_nothing_ran(self):
        """It used to claim `heuristic`, implying the heuristic had looked at
        the document and reached a view."""
        self.assertEqual(self._classify("").data.method, "none")

    def test_real_text_still_classifies(self):
        """The guard must not swallow documents that do have content."""
        out = self._classify("A plain depot report about quarterly throughput.")
        self.assertEqual(out.data.doc_type, "normal_document")
        self.assertIsNotNone(
            confidence_in_verdict(out.data.confidence, out.data.doc_type))


class TestReloadingDoesNotInventAVerdict(unittest.TestCase):
    """History rows are the second independent route to the bug.

    A row saved from a failed run carries no doc_type. The reload defaulted it
    to `normal_document`, so the verdict appeared on the way to the screen even
    though nothing ever classified anything.
    """

    def _reload(self, row: dict):
        import ui.app as app
        return app._dict_to_pipeline_result(row)

    def test_a_row_with_no_doc_type_stays_unclassified(self):
        result = self._reload({"file_name": "x.pdf", "file_type": "pdf",
                               "classification_confidence": 0.0})
        self.assertEqual(result.doc_type, "unknown")
        self.assertIsNone(
            confidence_in_verdict(result.classification_confidence,
                                  result.doc_type))

    def test_an_empty_string_doc_type_stays_unclassified(self):
        """`.get(key, default)` does not fire on a stored empty string, which
        is what a legacy row actually holds — hence `or`, not a default."""
        result = self._reload({"file_name": "x.pdf", "file_type": "pdf",
                               "doc_type": "", "classification_confidence": 0.0})
        self.assertEqual(result.doc_type, "unknown")

    def test_a_stored_verdict_survives_the_reload(self):
        for doc_type in CLASSIFIED_DOC_TYPES:
            with self.subTest(doc_type=doc_type):
                result = self._reload({"file_name": "x.pdf", "file_type": "pdf",
                                       "doc_type": doc_type,
                                       "classification_confidence": 0.4})
                self.assertEqual(result.doc_type, doc_type)


class TestEverySurfaceRendersTheAbsence(unittest.TestCase):
    """Four surfaces show this number. All four must handle None.

    Not grepped — each one is called. A previous version of the neighbouring
    test file asserted that source files *mention* `confidence_in_verdict` and
    passed while the classifier raised NameError on every call.
    """

    def _empty_result(self):
        from core.pipeline_result import PipelineResult
        return PipelineResult(
            file_name="video.mp4", file_type="youtube",
            doc_type="unknown", domain="General",
            classification_confidence=0.0, classification_method="none",
            summary="", summary_method="none",
            questions=[], question_extraction_method="skipped",
            raw_text="", word_count=0, page_count=0, metadata={},
            success=False, errors=["Download failed"],
        )

    def test_the_markdown_export_says_not_classified(self):
        markdown = self._empty_result().to_markdown()
        self.assertIn("not classified", markdown)
        self.assertNotIn("100% confidence", markdown)

    def test_the_ui_helper_returns_none(self):
        from ui.components.results_view import display_confidence
        self.assertIsNone(display_confidence(self._empty_result()))

    def test_the_ui_text_helper_says_not_classified(self):
        from ui.components.results_view import _confidence_text
        self.assertEqual(_confidence_text(self._empty_result()), "not classified")

    def test_the_e2e_harness_does_not_print_a_percentage(self):
        import io as _io
        import sys as _sys
        from contextlib import redirect_stdout
        _sys.path.insert(0, str(ROOT / "tests" / "e2e"))
        import e2e
        buffer = _io.StringIO()
        with redirect_stdout(buffer):
            e2e.show(self._empty_result())
        printed = buffer.getvalue()
        self.assertIn("NOT CLASSIFIED", printed)
        self.assertNotIn("100% confident", printed)

    def test_no_surface_raises_on_a_none_confidence(self):
        """The Optional is only safe if nobody formats it with :.0%."""
        from ui.components.results_view import _confidence_text, display_confidence
        result = self._empty_result()
        display_confidence(result)
        _confidence_text(result)
        result.to_markdown()


class TestTheRealZeroContentDocument(unittest.TestCase):
    """The whole chain over a real file, not a constructed result.

    `sample_blank.pdf` is a structurally valid PDF with a page and no text —
    the same zero-content condition the failed download produced, reachable
    without a network. No API key is needed: the agent stops at the empty gate
    before any LLM stage.
    """

    FIXTURE = ROOT / "tests" / "e2e" / "samples" / "sample_blank.pdf"

    def setUp(self):
        if not self.FIXTURE.exists():
            self.skipTest(f"run tests/e2e/make_samples.py to build "
                          f"{self.FIXTURE.name}")

    def test_a_blank_pdf_produces_no_verdict_anywhere(self):
        from agents.document_agent import DocumentAgent
        from ui.components.results_view import _banner_for, display_confidence
        from utils.config import load_config

        result = DocumentAgent(config=load_config().to_dict()).run(str(self.FIXTURE))

        self.assertEqual(result.word_count, 0)
        self.assertFalse(result.success, "a document with no content is not a "
                                         "successful analysis")
        self.assertNotIn(result.doc_type, CLASSIFIED_DOC_TYPES,
                         "nothing was classified, so no class may be reported")
        self.assertIsNone(display_confidence(result))
        self.assertEqual(_banner_for(result.doc_type)[0], "Not classified")
        self.assertIn("not classified", result.to_markdown())

    def test_the_screen_the_user_saw_cannot_be_reproduced(self):
        """The original report, asserted directly: 0 words, and yet
        "Normal Document ... 100% confidence"."""
        from agents.document_agent import DocumentAgent
        from ui.components.results_view import _banner_for, display_confidence
        from utils.config import load_config

        result = DocumentAgent(config=load_config().to_dict()).run(str(self.FIXTURE))
        banner, _ = _banner_for(result.doc_type)
        confidence = display_confidence(result)

        self.assertFalse(
            result.word_count == 0 and banner == "Normal Document"
            and confidence == 1.0,
            "this is the reported bug verbatim: a confident verdict about a "
            "document that does not exist")


if __name__ == "__main__":
    unittest.main(verbosity=2)

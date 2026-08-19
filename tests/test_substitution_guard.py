"""Tests for the anti-substitution instruction in the corpus prompt.

Framed as a PROHIBITION, not as permission to decline, because measurement said
permission was not what was missing. On the thirteen no-answer cases the model
declined 12/12 of those that required it, unprompted, before any of this
existed — in its own words, "the provided excerpts do not contain any
information about...".

What it does instead is SUBSTITUTE. Asked for a company headcount whose only
source ranks fourth for its own sub-question and falls outside the six slots,
it finds a sheet named Headcount belonging to a different organisation, sums
51+21+15+9, and reports "96 employees" with a correct citation to that sheet.
The citation is honest. The figure is invented and appears in no document.

Measured over ten trials: fabrication 5/5 before, 1/10 after, with the
answerable half of the same question preserved 10/10.

These tests assert the prompt's SHAPE, not the model's behaviour — that lives
in `run_eval.py --multi --with-answers`, which needs an API key. What can be
checked offline is that the instruction exists, says both halves of what it
must, and does not leak into single-document chat.

Run:
    pytest tests/test_substitution_guard.py -v
"""

from __future__ import annotations

import inspect
import unittest

from skills.document_chat_skill import DocumentChatSkill


def _prompt_source() -> str:
    return inspect.getsource(DocumentChatSkill.execute)


class TestTheProhibitionIsPresent(unittest.TestCase):
    def setUp(self):
        self.source = _prompt_source()

    def test_it_forbids_inventing_a_figure_by_arithmetic(self):
        """The worse of the two failures: the result is in no document at all,
        so it cannot be traced and reads exactly like a retrieved fact."""
        self.assertIn("combining numbers", self.source)

    def test_it_forbids_substituting_a_similar_fact(self):
        self.assertIn("different depot, tier, period or", self.source)

    def test_it_warns_that_retrieval_returns_merely_on_topic_excerpts(self):
        """Without this the model has no reason to doubt what it was handed."""
        self.assertIn("retrieved by similarity", self.source)

    def test_it_permits_a_partial_answer(self):
        """The constraint that decides whether this ships.

        md-10's leave half is genuinely answerable. A blanket refusal would be
        a regression, not a fix, so the instruction has to say that a
        many-part question can be partly answered.
        """
        self.assertIn("partly", self.source)
        self.assertIn("decline the rest", self.source)

    def test_it_does_not_merely_grant_permission_to_decline(self):
        """Permission already existed and changed nothing; the wording has to
        prohibit the specific behaviour that was actually happening."""
        self.assertIn("must never do", self.source)


class TestSingleDocumentIsUntouched(unittest.TestCase):
    """The guard addresses cross-document substitution, which cannot arise when
    there is one document. Applying it there would risk 33/33 answers for no
    measured gain."""

    def setUp(self):
        self.source = _prompt_source()

    def test_the_single_document_branch_still_reads_as_it_did(self):
        single = self.source.split("if multi_doc else", 1)[1]
        self.assertIn("Each excerpt begins with its source in square brackets", single)
        self.assertNotIn("combining numbers", single)
        self.assertNotIn("retrieved by similarity", single)

    def test_the_guard_sits_in_the_multi_document_branch(self):
        multi = self.source.split("if multi_doc else", 1)[0]
        self.assertIn("combining numbers", multi)
        self.assertIn("retrieved by similarity", multi)

    def test_the_branch_is_still_chosen_by_the_chunks(self):
        """Mode must stay derived, so single-document cannot take this path."""
        self.assertIn("multi_doc = self.is_multi_doc(chunks)", self.source)


class TestModeDetectionStillHolds(unittest.TestCase):
    def test_untagged_chunks_are_single_document(self):
        self.assertFalse(DocumentChatSkill.is_multi_doc(
            [{"text": "x", "page_or_sheet": 1}]))

    def test_tagged_chunks_are_multi_document(self):
        self.assertTrue(DocumentChatSkill.is_multi_doc(
            [{"text": "x", "page_or_sheet": 1, "document": "a.pdf"}]))


if __name__ == "__main__":
    unittest.main(verbosity=2)

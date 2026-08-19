"""Tests for the anti-substitution instruction in the corpus prompt.

Framed as a PROHIBITION, not as permission to decline, because measurement said
permission was not what was missing. On the thirteen no-answer cases the model
declined 12/12 of those that required it, unprompted, before any of this
existed. What it did instead was SUBSTITUTE: asked for a company headcount whose
only source falls outside the six slots, it found a sheet named Headcount
belonging to a different organisation, summed 51+21+15+9, and reported "96
employees" with a correct citation. Fabrication 5/5 before, 1 in 10 after, with
the answerable half of the same question preserved.

THESE TESTS CAPTURE THE PROMPT THE MODEL IS ACTUALLY SENT.

They used to grep `inspect.getsource(execute)` for phrases. That is the pattern
that let a NameError ship elsewhere in this repo: a source-text assertion cannot
see whether the string was built, which branch it landed in, or whether it
reached the message. Here the LLM client is stubbed, the skill is really run,
and the assertions are made against the captured `messages` payload — so a
clause moved into the wrong branch, or built and never sent, now fails.

Run:
    pytest tests/test_substitution_guard.py -v
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import utils.llm_client as llm_client
from core.models import SkillInput
from core.skill_registry import SkillRegistry

_CORPUS = [
    {"text": "The fleet comprises 612 vehicles.", "page_or_sheet": 3,
     "document": "report.pdf"},
    {"text": "Standard leave is 25 days per year.", "page_or_sheet": 5,
     "document": "manual.pdf"},
]
_SINGLE = [
    {"text": "The fleet comprises 612 vehicles.", "page_or_sheet": 3},
    {"text": "Standard leave is 25 days per year.", "page_or_sheet": 5},
]


def capture_prompt(chunks, question="How many vehicles are there?"):
    """Run the skill for real against a stubbed LLM; return what it sent.

    Returns (system_prompt, all_messages). Nothing reaches the network: the
    client's `chat` is replaced and `available` forced True, so the skill takes
    its normal path right up to the call boundary.
    """
    registry = SkillRegistry()
    registry.discover()
    skill = registry.instantiate(
        "document_chat", config={"groq": {"api_key": "gsk_stub", "enabled": True}})

    seen = {}

    def fake_chat(self, messages, **kwargs):
        seen["messages"] = messages
        return "stubbed reply"

    with patch.object(llm_client.LLMClient, "chat", fake_chat), \
            patch.object(llm_client.LLMClient, "available", True):
        out = skill.safe_execute(SkillInput(data={
            "user_message": question,
            "document_chunks": chunks,
            "conversation_history": [],
            "domain": "General",
        }))

    assert out.success, f"skill failed before reaching the LLM: {out.error}"
    messages = seen.get("messages")
    assert messages, "the skill never called the LLM"
    system = next(m["content"] for m in messages if m["role"] == "system")
    return system, messages


class TestTheProhibitionReachesTheModel(unittest.TestCase):
    """Asserted on the sent payload, not on the source that builds it."""

    @classmethod
    def setUpClass(cls):
        cls.prompt, cls.messages = capture_prompt(_CORPUS)

    def test_it_forbids_inventing_a_figure_by_arithmetic(self):
        """The worse of the two failures: the result is in no document at all,
        so it cannot be traced and reads exactly like a retrieved fact."""
        self.assertIn("combining numbers", self.prompt)

    def test_it_forbids_substituting_a_similar_fact(self):
        self.assertIn("different depot, tier, period or", self.prompt)

    def test_it_warns_that_retrieval_returns_merely_on_topic_excerpts(self):
        """Without this the model has no reason to doubt what it was handed."""
        self.assertIn("retrieved by similarity", self.prompt)

    def test_it_permits_a_partial_answer(self):
        """The constraint that decided whether this shipped.

        md-10's leave half is genuinely answerable, so a blanket refusal would
        be a regression rather than a fix.
        """
        self.assertIn("partly", self.prompt)
        self.assertIn("decline the rest", self.prompt)

    def test_the_excerpts_reach_the_model_with_their_document_labels(self):
        """The guard is worthless if the context it refers to is unlabelled."""
        context = next(m["content"] for m in self.messages
                       if "CORPUS CONTEXT" in str(m.get("content", "")))
        self.assertIn("[report.pdf, Page 3]", context)
        self.assertIn("[manual.pdf, Page 5]", context)


class TestSingleDocumentIsUntouched(unittest.TestCase):
    """The guard addresses cross-document substitution, which cannot arise with
    one document. Applying it there would risk 33/33 answers for no gain."""

    @classmethod
    def setUpClass(cls):
        cls.corpus_prompt, _ = capture_prompt(_CORPUS)
        cls.single_prompt, cls.single_messages = capture_prompt(_SINGLE)

    def test_the_single_document_prompt_does_not_carry_the_guard(self):
        self.assertNotIn("combining numbers", self.single_prompt)
        self.assertNotIn("retrieved by similarity", self.single_prompt)

    def test_the_single_document_prompt_still_asks_for_source_labels(self):
        self.assertIn("square brackets", self.single_prompt)
        self.assertIn("[Page 3]", self.single_prompt)

    def test_the_two_modes_really_do_send_different_prompts(self):
        """Guards the branch itself: if `is_multi_doc` ever stopped
        discriminating, both would be identical and every test above would
        still pass on whichever branch won."""
        self.assertNotEqual(self.corpus_prompt, self.single_prompt)

    def test_single_document_context_carries_no_document_name(self):
        context = next(m["content"] for m in self.single_messages
                       if "DOCUMENT CONTEXT" in str(m.get("content", "")))
        self.assertIn("[Page 3]", context)
        self.assertNotIn("report.pdf", context)


class TestModeDetectionStillHolds(unittest.TestCase):
    def test_untagged_chunks_are_single_document(self):
        from skills.document_chat_skill import DocumentChatSkill
        self.assertFalse(DocumentChatSkill.is_multi_doc(_SINGLE))

    def test_tagged_chunks_are_multi_document(self):
        from skills.document_chat_skill import DocumentChatSkill
        self.assertTrue(DocumentChatSkill.is_multi_doc(_CORPUS))


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Tests for chat across a corpus rather than one document.

The defect these are built around is not recall. Handed all six eval fixtures
at once, the existing single-document selector already put the answering
passage in the context for 11 of 13 cross-document questions. What it could not
do was say WHERE an answer came from: it deduplicated slots by page label, so

  * page 3 of the operations review and page 3 of the staff manual were one
    source, and whichever scored lower became unreachable AT ANY RANK -- on
    md-13 that discarded the third-highest scoring passage in the corpus; and
  * every citation was a bare number. 11 of 13 citation lists named a page that
    existed in more than one file, and pages 1 and 2 each named four.

A confidently cited wrong document is worse than no citation, so the fix is the
(document, page) key plus labels that carry the document.

Run:
    pytest tests/test_multi_doc_chat.py -v
"""

from __future__ import annotations

import unittest

from core.skill_registry import SkillRegistry
from skills.document_chat_skill import (
    DocumentChatSkill, MAX_CORPUS_DOCUMENTS,
    _MULTI_DOC_SLOTS, _SINGLE_DOC_SLOTS,
)


def _skill() -> DocumentChatSkill:
    registry = SkillRegistry()
    registry.discover()
    return registry.instantiate("document_chat", config={})


def _single(n: int = 8) -> list:
    return [{"text": f"depot maintenance spend passage {i}", "page_or_sheet": i}
            for i in range(1, n + 1)]


def _corpus() -> list:
    """Two documents that both have a page 3 -- the ambiguity, in miniature."""
    return [
        {"text": "vehicle replacement cycle seven years", "page_or_sheet": 3,
         "document": "report.pdf"},
        {"text": "telematics and technology rollout", "page_or_sheet": 16,
         "document": "report.pdf"},
        {"text": "laptops are replaced on a four year cycle", "page_or_sheet": 3,
         "document": "manual.pdf"},
        {"text": "annual leave entitlement", "page_or_sheet": 5,
         "document": "manual.pdf"},
    ]


class TestModeDetection(unittest.TestCase):
    def test_untagged_chunks_are_single_document(self):
        self.assertFalse(DocumentChatSkill.is_multi_doc(_single()))

    def test_tagged_chunks_are_multi_document(self):
        self.assertTrue(DocumentChatSkill.is_multi_doc(_corpus()))

    def test_an_empty_corpus_is_not_multi_document(self):
        self.assertFalse(DocumentChatSkill.is_multi_doc([]))

    def test_mode_comes_from_the_chunks_not_a_caller_flag(self):
        """A single-document call cannot accidentally take the corpus path."""
        self.assertFalse(DocumentChatSkill.is_multi_doc(
            [{"text": "x", "page_or_sheet": 1, "document": None}]))


class TestSourceKey(unittest.TestCase):
    def test_a_page_is_the_key_within_one_document(self):
        self.assertEqual(DocumentChatSkill.source_key({"page_or_sheet": 3}), 3)

    def test_the_document_is_part_of_the_key_across_a_corpus(self):
        self.assertEqual(
            DocumentChatSkill.source_key({"page_or_sheet": 3, "document": "a.pdf"}),
            ("a.pdf", 3))

    def test_same_page_in_two_documents_gives_two_keys(self):
        """The defect in one line: these must not collapse into one slot."""
        a = DocumentChatSkill.source_key({"page_or_sheet": 3, "document": "a.pdf"})
        b = DocumentChatSkill.source_key({"page_or_sheet": 3, "document": "b.pdf"})
        self.assertNotEqual(a, b)


class TestSourceLabel(unittest.TestCase):
    def test_single_document_labels_are_unchanged(self):
        """Single-document answers must read exactly as they did."""
        self.assertEqual(DocumentChatSkill.source_label({"page_or_sheet": 3}), "Page 3")
        self.assertEqual(
            DocumentChatSkill.source_label({"page_or_sheet": "Q3 Revenue"}),
            "Sheet: Q3 Revenue")

    def test_a_corpus_label_names_its_document(self):
        self.assertEqual(
            DocumentChatSkill.source_label({"page_or_sheet": 3, "document": "report.pdf"}),
            "report.pdf, Page 3")

    def test_a_corpus_sheet_label_names_its_document(self):
        self.assertEqual(
            DocumentChatSkill.source_label(
                {"page_or_sheet": "Headcount", "document": "sales.xlsx"}),
            "sales.xlsx, Sheet: Headcount")

    def test_a_missing_page_still_names_the_document(self):
        self.assertEqual(
            DocumentChatSkill.source_label({"document": "report.pdf"}),
            "report.pdf, Unknown source")


class TestAnchors(unittest.TestCase):
    def test_a_single_document_still_gets_its_anchors(self):
        chunks = _single()
        self.assertEqual(DocumentChatSkill.anchor_chunks(chunks),
                         [chunks[0], chunks[-1]])

    def test_a_corpus_gets_none(self):
        """"The opening chunk" means nothing across twenty documents.

        Measured, they spent 14% of the context budget on every query, on
        documents nobody had asked about.
        """
        self.assertEqual(DocumentChatSkill.anchor_chunks(_corpus()), [])


class TestSlotBudget(unittest.TestCase):
    def setUp(self):
        self.skill = _skill()

    def test_a_corpus_gets_more_slots_than_one_document(self):
        self.assertGreater(_MULTI_DOC_SLOTS, _SINGLE_DOC_SLOTS)

    def test_single_document_selection_size_is_unchanged(self):
        selected, ranked = self.skill._select_with_roles("maintenance", _single())
        self.assertLessEqual(len(ranked), _SINGLE_DOC_SLOTS)

    def test_a_corpus_selection_is_all_ranked_choices(self):
        """No anchors means every chunk sent was chosen for this question."""
        selected, ranked = self.skill._select_with_roles("replacement cycle", _corpus())
        self.assertEqual(len(selected), len(ranked))


class TestCollisionIsFixed(unittest.TestCase):
    """The md-13 regression guard."""

    def setUp(self):
        self.skill = _skill()

    def test_both_page_threes_can_be_selected(self):
        selected, _ = self.skill._select_with_roles(
            "replacement cycles for equipment and vehicles", _corpus())
        keys = {DocumentChatSkill.source_key(c) for c in selected}
        self.assertIn(("report.pdf", 3), keys)
        self.assertIn(("manual.pdf", 3), keys,
                      "the second page 3 must not be discarded as a duplicate")

    def test_passages_of_one_page_are_still_deduplicated(self):
        """The original dedupe still has to work; only its KEY changed."""
        chunks = [
            {"text": "maintenance a", "page_or_sheet": 3, "document": "a.pdf"},
            {"text": "maintenance b", "page_or_sheet": 3, "document": "a.pdf"},
            {"text": "unrelated", "page_or_sheet": 9, "document": "a.pdf"},
        ]
        selected, _ = self.skill._select_with_roles("maintenance", chunks)
        keys = [DocumentChatSkill.source_key(c) for c in selected]
        self.assertEqual(len(keys), len(set(keys)))


class TestCitations(unittest.TestCase):
    def setUp(self):
        self.skill = _skill()

    def test_detailed_citations_carry_document_source_and_label(self):
        ranked = [{"page_or_sheet": 3, "document": "report.pdf", "text": "x"}]
        cite = self.skill.citable_sources_detailed(ranked, ranked)[0]
        self.assertEqual(cite["document"], "report.pdf")
        self.assertEqual(cite["source"], 3)
        self.assertEqual(cite["label"], "report.pdf, Page 3")

    def test_two_documents_sharing_a_page_are_two_citations(self):
        ranked = [{"page_or_sheet": 3, "document": "report.pdf", "text": "x"},
                  {"page_or_sheet": 3, "document": "manual.pdf", "text": "y"}]
        cites = self.skill.citable_sources_detailed(ranked, ranked)
        self.assertEqual(len(cites), 2)
        self.assertEqual([c["label"] for c in cites],
                         ["report.pdf, Page 3", "manual.pdf, Page 3"])

    def test_the_legacy_bare_list_keeps_both_too(self):
        """`used_pages` cannot DISAMBIGUATE them, but must not drop one either.

        It renders as "3, 3", which is the honest shape of the problem and the
        reason `used_sources` exists.
        """
        ranked = [{"page_or_sheet": 3, "document": "report.pdf", "text": "x"},
                  {"page_or_sheet": 3, "document": "manual.pdf", "text": "y"}]
        self.assertEqual(self.skill._citable_sources(ranked, ranked), [3, 3])

    def test_single_document_citations_are_unchanged(self):
        ranked = [{"page_or_sheet": 5, "text": "x"}, {"page_or_sheet": 5, "text": "y"},
                  {"page_or_sheet": 7, "text": "z"}]
        self.assertEqual(self.skill._citable_sources(ranked, ranked), [5, 7])
        self.assertEqual(
            [c["label"] for c in self.skill.citable_sources_detailed(ranked, ranked)],
            ["Page 5", "Page 7"])

    def test_a_citation_without_a_document_reports_none_not_a_guess(self):
        ranked = [{"page_or_sheet": 5, "text": "x"}]
        self.assertIsNone(
            self.skill.citable_sources_detailed(ranked, ranked)[0]["document"])


class TestContextLabelling(unittest.TestCase):
    def setUp(self):
        self.skill = _skill()

    def test_corpus_context_names_the_document_of_every_excerpt(self):
        context = self.skill._format_context(_corpus())
        self.assertIn("[report.pdf, Page 3]", context)
        self.assertIn("[manual.pdf, Page 3]", context)

    def test_single_document_context_is_unchanged(self):
        context = self.skill._format_context(_single(2))
        self.assertIn("[Page 1]", context)
        self.assertNotIn("[Page 1],", context)


class TestCorpusCap(unittest.TestCase):
    def test_the_cap_is_documented_and_positive(self):
        self.assertGreater(MAX_CORPUS_DOCUMENTS, 0)

    def test_the_store_defaults_to_the_skill_cap(self):
        """One constant, so raising it cannot half-apply."""
        import inspect
        from utils.document_store import DocumentStore
        source = inspect.getsource(DocumentStore.load_corpus)
        self.assertIn("MAX_CORPUS_DOCUMENTS", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)

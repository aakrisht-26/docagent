"""Tests for how chat context is assembled and labelled.

These guard a correctness bug that shipped silently. The system prompt asked the
model to "cite page numbers", while the context was built as
`"\\n\\n---\\n\\n".join(c["text"] ...)` — bare text with no page numbers in it.
Measured over the 27 page-sourced eval cases, the model cited a page number in
**zero** of them; it quoted section headings out of the body text instead, which
reads like a citation and is not one.

Nothing errored, and the UI still showed a "Sources:" line, because that line
comes from `used_pages` which this skill computes itself. So the defect was
invisible from both directions: no failure, and a citation on screen.

Run:
    pytest tests/test_chat_context.py -v
"""

from __future__ import annotations

import unittest

from core.skill_registry import SkillRegistry
from skills.document_chat_skill import DocumentChatSkill, _MAX_CONTEXT_CHARS


def _skill() -> DocumentChatSkill:
    registry = SkillRegistry()
    registry.discover()
    return registry.instantiate("document_chat", config={})


class TestSourceLabel(unittest.TestCase):
    def test_integer_source_reads_as_a_page(self):
        self.assertEqual(DocumentChatSkill.source_label({"page_or_sheet": 3}), "Page 3")

    def test_string_source_reads_as_a_sheet(self):
        self.assertEqual(
            DocumentChatSkill.source_label({"page_or_sheet": "Q3 Revenue"}),
            "Sheet: Q3 Revenue",
        )

    def test_missing_source_does_not_invent_one(self):
        """Better an honest 'Unknown source' than a confident wrong page."""
        self.assertEqual(DocumentChatSkill.source_label({}), "Unknown source")
        self.assertEqual(
            DocumentChatSkill.source_label({"page_or_sheet": None}), "Unknown source"
        )


class TestContextLabelling(unittest.TestCase):
    def setUp(self):
        self.skill = _skill()

    def test_every_chunk_carries_its_source_into_the_context(self):
        chunks = [
            {"text": "alpha", "page_or_sheet": 1},
            {"text": "beta", "page_or_sheet": 7},
            {"text": "gamma", "page_or_sheet": "Summary"},
        ]
        context = self.skill._format_context(chunks)
        self.assertIn("[Page 1]", context)
        self.assertIn("[Page 7]", context)
        self.assertIn("[Sheet: Summary]", context)
        for chunk in chunks:
            self.assertIn(chunk["text"], context)

    def test_label_precedes_its_own_text(self):
        """A label after its text would attribute the wrong excerpt."""
        chunks = [{"text": "alpha", "page_or_sheet": 1},
                  {"text": "beta", "page_or_sheet": 2}]
        context = self.skill._format_context(chunks)
        self.assertLess(context.index("[Page 1]"), context.index("alpha"))
        self.assertLess(context.index("alpha"), context.index("[Page 2]"))
        self.assertLess(context.index("[Page 2]"), context.index("beta"))

    def test_the_model_is_told_the_label_format(self):
        """A labelled context the prompt never mentions is still unusable."""
        import inspect
        source = inspect.getsource(DocumentChatSkill.execute)
        self.assertIn("[Page 3]", source)
        self.assertIn("square brackets", source)


class TestContextTruncation(unittest.TestCase):
    """Truncation must drop whole chunks, never slice one.

    The old code sliced the joined string at _MAX_CONTEXT_CHARS, so the final
    chunk could be cut mid-sentence while still being named in `used_pages` —
    citing a source whose text was only partly present.
    """

    def setUp(self):
        self.skill = _skill()

    def test_context_stays_within_the_cap(self):
        chunks = [{"text": "x" * 2000, "page_or_sheet": i} for i in range(10)]
        context = self.skill._format_context(chunks)
        self.assertLessEqual(len(context), _MAX_CONTEXT_CHARS + 200)

    def test_no_chunk_is_partially_included(self):
        chunks = [{"text": f"chunk{i}-" + "y" * 1500, "page_or_sheet": i}
                  for i in range(10)]
        context = self.skill._format_context(chunks)
        for chunk in chunks:
            # Either the whole text is present, or none of it — never a prefix.
            if chunk["text"][:20] in context:
                self.assertIn(chunk["text"], context,
                              "a chunk appears only partially")

    def test_an_oversized_first_chunk_is_still_included(self):
        """Dropping everything would answer from nothing at all."""
        chunks = [{"text": "z" * (_MAX_CONTEXT_CHARS * 2), "page_or_sheet": 1}]
        context = self.skill._format_context(chunks)
        self.assertIn("[Page 1]", context)
        self.assertGreater(len(context), 0)

    def test_a_dropped_chunk_takes_its_label_with_it(self):
        chunks = [{"text": "a" * 5000, "page_or_sheet": 1},
                  {"text": "b" * 5000, "page_or_sheet": 2}]
        context = self.skill._format_context(chunks)
        self.assertIn("[Page 1]", context)
        self.assertNotIn("[Page 2]", context)


if __name__ == "__main__":
    unittest.main(verbosity=2)

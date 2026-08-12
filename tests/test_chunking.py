"""Tests for retrieval sub-chunking.

The two properties that matter here are not "does it split text" — that is
obvious — but the two things that would break quietly if it were wrong:

* **Citations must still resolve.** Every passage carries the page or sheet it
  came from, so an answer can still say "Source: Page 3". If that mapping were
  lost the citation would point at a passage index nobody can find.

* **Summarisation must not see passages.** It reads ParsedDocument.chunks,
  which is a separate pipeline path that is working. Sub-chunking is a
  retrieval-index concern and must not leak into it.

Run:
    pytest tests/test_chunking.py -v
"""

from __future__ import annotations

import os
import unittest

from utils.chunking import (DEFAULT_PASSAGE_OVERLAP, DEFAULT_PASSAGE_WORDS,
                            passage_settings, scheme_name, sub_chunk)


def _words(n: int, prefix: str = "w") -> str:
    return " ".join(f"{prefix}{i}" for i in range(n))


class TestSourceMapping(unittest.TestCase):
    """Citations resolve to the original page or sheet."""

    def test_every_passage_keeps_its_source_page(self):
        chunks = [{"text": _words(400), "page_or_sheet": 7}]
        out = sub_chunk(chunks, words=100, overlap=20)
        self.assertGreater(len(out), 1, "a 400-word page should split")
        self.assertTrue(all(p["page_or_sheet"] == 7 for p in out))

    def test_sheet_names_survive_as_strings(self):
        """Excel sources are sheet names, not integers."""
        chunks = [{"text": _words(300), "page_or_sheet": "Q3 Revenue"}]
        out = sub_chunk(chunks, words=100, overlap=20)
        self.assertTrue(all(p["page_or_sheet"] == "Q3 Revenue" for p in out))

    def test_sources_are_not_reordered_or_merged(self):
        chunks = [
            {"text": _words(300, "a"), "page_or_sheet": 1},
            {"text": _words(300, "b"), "page_or_sheet": 2},
        ]
        out = sub_chunk(chunks, words=100, overlap=20)
        seen = [p["page_or_sheet"] for p in out]
        self.assertEqual(seen, sorted(seen), "passages must stay in document order")
        self.assertEqual(set(seen), {1, 2})
        # No passage may mix text from two pages.
        for p in out:
            tokens = p["text"].split()
            self.assertTrue(all(t.startswith("a") for t in tokens)
                            or all(t.startswith("b") for t in tokens))


class TestSplitting(unittest.TestCase):
    def test_short_chunks_pass_through_untouched(self):
        """The 46-word pages of the large fixture must not be shredded."""
        chunks = [{"text": _words(30), "page_or_sheet": 1}]
        out = sub_chunk(chunks, words=100, overlap=20)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["text"], chunks[0]["text"])

    def test_no_text_is_lost(self):
        text = _words(457)
        out = sub_chunk([{"text": text, "page_or_sheet": 1}], words=100, overlap=20)
        covered = set()
        for p in out:
            covered.update(p["text"].split())
        self.assertEqual(covered, set(text.split()))

    def test_consecutive_passages_overlap_by_the_requested_amount(self):
        out = sub_chunk([{"text": _words(300), "page_or_sheet": 1}],
                        words=100, overlap=20)
        first, second = out[0]["text"].split(), out[1]["text"].split()
        self.assertEqual(first[-20:], second[:20])

    def test_empty_text_is_preserved_not_dropped(self):
        """The chat tab uses an all-empty chunk list as a signal.

        It distinguishes 'history row saved before text was stored' from
        'document genuinely empty'. Dropping empties here would break it.
        """
        out = sub_chunk([{"text": "", "page_or_sheet": 3}])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["page_or_sheet"], 3)

    def test_empty_input(self):
        self.assertEqual(sub_chunk([]), [])

    def test_overlap_at_or_above_window_cannot_stall(self):
        """A bad config must not produce an infinite or non-advancing split."""
        out = sub_chunk([{"text": _words(300), "page_or_sheet": 1}],
                        words=50, overlap=50)
        self.assertGreater(len(out), 1)
        self.assertLess(len(out), 100, "the cursor must advance")


class TestSettings(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("DOCAGENT_PASSAGE_WORDS", "DOCAGENT_PASSAGE_OVERLAP")}
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_defaults(self):
        self.assertEqual(passage_settings(),
                         (DEFAULT_PASSAGE_WORDS, DEFAULT_PASSAGE_OVERLAP))

    def test_environment_override(self):
        os.environ["DOCAGENT_PASSAGE_WORDS"] = "60"
        os.environ["DOCAGENT_PASSAGE_OVERLAP"] = "15"
        self.assertEqual(passage_settings(), (60, 15))

    def test_garbage_falls_back_to_defaults(self):
        os.environ["DOCAGENT_PASSAGE_WORDS"] = "not-a-number"
        self.assertEqual(passage_settings()[0], DEFAULT_PASSAGE_WORDS)

    def test_scheme_name_reflects_the_settings(self):
        os.environ["DOCAGENT_PASSAGE_WORDS"] = "80"
        os.environ["DOCAGENT_PASSAGE_OVERLAP"] = "10"
        self.assertEqual(scheme_name(), "passage:80/10")


class TestSummarisationIsolation(unittest.TestCase):
    def test_summarisation_reads_parsed_document_not_the_retrieval_index(self):
        """Guards the constraint directly, at the source.

        Sub-chunking is applied where the retrieval index is built. If someone
        later applied it to ParsedDocument.chunks instead, summarisation would
        silently start seeing passages, which is the one thing this change was
        told not to do.
        """
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        summariser = (root / "skills" / "summarization_skill.py").read_text(encoding="utf-8")
        self.assertNotIn("sub_chunk", summariser)
        self.assertNotIn("utils.chunking", summariser)

        # And the readers, which build ParsedDocument in the first place.
        for name in ("pdf_reader_skill.py", "excel_reader_skill.py"):
            src = (root / "skills" / name).read_text(encoding="utf-8")
            self.assertNotIn("sub_chunk", src, f"{name} must not sub-chunk")


if __name__ == "__main__":
    unittest.main(verbosity=2)

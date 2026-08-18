"""Tests for DocumentStore.load_corpus — assembling a searchable corpus.

Chat across history needs every chunk to know which document it came from.
Without that tag the selector cannot tell page 3 of one file from page 3 of
another, and one of them becomes unreachable at any rank.

Two things here are less obvious than the tagging:

* Two history rows can carry the SAME file name — the same document analysed
  twice, or two different files that happen to share a name. Left alone that
  recreates the exact ambiguity the tag exists to remove, one level up.

* A document whose stored vectors are unusable still answers, via chunks that
  get embedded on demand. That is correct, but it must be VISIBLE: a corpus
  half-served by keyword matching should say so rather than quietly answer
  worse.

Run:
    pytest tests/test_corpus_store.py -v
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from utils.document_store import DocumentStore


class _StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = DocumentStore(db_path=self.tmp / "history.db")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _insert(self, file_name: str, chunks: list, vectors: bool = False,
                model: str = None, scheme: str = None) -> int:
        """Write a row directly, so a corpus can be composed exactly."""
        from utils import embeddings as emb
        from utils.chunking import scheme_name

        blob = None
        if vectors and chunks:
            import numpy as np
            from utils.embeddings import EMBEDDING_DIM
            matrix = np.ones((len(chunks), EMBEDDING_DIM), dtype="float32")
            blob = emb.to_blob(matrix)

        with self.store._connect() as conn:
            cur = conn.execute(
                "INSERT INTO history (file_name, file_hash, file_type, doc_type, "
                "domain, word_count, question_count, processing_time_ms, "
                "result_json, content_text, content_chunks_json, "
                "content_embeddings_b64, embedding_model, chunk_scheme) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (file_name, file_name, "pdf", "report", "General", 100, 0, 1.0,
                 json.dumps({"file_name": file_name, "summary": "s"}),
                 "text", json.dumps(chunks), blob,
                 model or emb.model_name(), scheme or scheme_name()),
            )
            return cur.lastrowid


class TestTagging(_StoreCase):
    def test_every_chunk_knows_its_document(self):
        self._insert("a.pdf", [{"text": "alpha", "page_or_sheet": 1}])
        self._insert("b.pdf", [{"text": "beta", "page_or_sheet": 1}])
        corpus = self.store.load_corpus()
        self.assertEqual(len(corpus["chunks"]), 2)
        self.assertEqual({c["document"] for c in corpus["chunks"]}, {"a.pdf", "b.pdf"})

    def test_the_same_page_in_two_documents_stays_distinguishable(self):
        """The whole reason the tag exists."""
        self._insert("a.pdf", [{"text": "alpha", "page_or_sheet": 3}])
        self._insert("b.pdf", [{"text": "beta", "page_or_sheet": 3}])
        from skills.document_chat_skill import DocumentChatSkill
        keys = {DocumentChatSkill.source_key(c)
                for c in self.store.load_corpus()["chunks"]}
        self.assertEqual(len(keys), 2)

    def test_an_empty_history_yields_an_empty_corpus(self):
        corpus = self.store.load_corpus()
        self.assertEqual(corpus["chunks"], [])
        self.assertEqual(corpus["documents"], [])

    def test_chunks_are_copies_not_the_stored_dicts(self):
        """Tagging must not mutate what a single-document reader gets back."""
        self._insert("a.pdf", [{"text": "alpha", "page_or_sheet": 1}])
        self.store.load_corpus()
        entry = self.store.list_recent()[0]
        loaded = self.store.load(entry.entry_id)
        self.assertNotIn("document", loaded["content_chunks"][0])


class TestDuplicateNames(_StoreCase):
    def test_two_rows_with_one_name_are_disambiguated(self):
        self._insert("report.pdf", [{"text": "first", "page_or_sheet": 1}])
        self._insert("report.pdf", [{"text": "second", "page_or_sheet": 1}])
        names = {d["document"] for d in self.store.load_corpus()["documents"]}
        self.assertEqual(len(names), 2, "two documents must not share one label")
        self.assertIn("report.pdf", names)

    def test_the_original_file_name_is_still_reported(self):
        self._insert("report.pdf", [{"text": "first", "page_or_sheet": 1}])
        self._insert("report.pdf", [{"text": "second", "page_or_sheet": 1}])
        for doc in self.store.load_corpus()["documents"]:
            self.assertEqual(doc["file_name"], "report.pdf")


class TestSearchability(_StoreCase):
    def test_a_fully_vectorised_document_reports_searchable(self):
        self._insert("a.pdf", [{"text": "alpha", "page_or_sheet": 1},
                               {"text": "beta", "page_or_sheet": 2}], vectors=True)
        doc = self.store.load_corpus()["documents"][0]
        self.assertTrue(doc["searchable"])
        self.assertEqual(doc["vectors"], 2)

    def test_a_document_with_no_stored_vectors_reports_unsearchable(self):
        """It still answers — the gaps are embedded on demand — but say so."""
        self._insert("a.pdf", [{"text": "alpha", "page_or_sheet": 1}])
        doc = self.store.load_corpus()["documents"][0]
        self.assertFalse(doc["searchable"])
        self.assertEqual(doc["vectors"], 0)

    def test_vectors_from_another_model_are_not_counted_as_searchable(self):
        """Comparing them would rank against nonsense, so they are dropped."""
        self._insert("a.pdf", [{"text": "alpha", "page_or_sheet": 1}],
                     vectors=True, model="some-other-model")
        self.assertFalse(self.store.load_corpus()["documents"][0]["searchable"])

    def test_vectors_from_another_chunk_scheme_are_not_counted(self):
        """A vector describes specific text; re-splitting invalidates it."""
        self._insert("a.pdf", [{"text": "alpha", "page_or_sheet": 1}],
                     vectors=True, scheme="page")
        self.assertFalse(self.store.load_corpus()["documents"][0]["searchable"])

    def test_an_unsearchable_document_still_contributes_its_text(self):
        self._insert("a.pdf", [{"text": "alpha", "page_or_sheet": 1}])
        self.assertEqual(len(self.store.load_corpus()["chunks"]), 1)


class TestExclusionsAndCap(_StoreCase):
    def test_a_row_with_no_content_is_skipped_and_named(self):
        """Including it would add a document that can never answer."""
        self._insert("empty.pdf", [])
        self._insert("real.pdf", [{"text": "alpha", "page_or_sheet": 1}])
        corpus = self.store.load_corpus()
        self.assertEqual([d["document"] for d in corpus["documents"]], ["real.pdf"])
        self.assertEqual(corpus["skipped"], ["empty.pdf"])

    def test_whitespace_only_chunks_do_not_count_as_content(self):
        self._insert("blank.pdf", [{"text": "   ", "page_or_sheet": 1}])
        self.assertEqual(self.store.load_corpus()["documents"], [])

    def test_the_cap_bounds_the_corpus(self):
        for i in range(5):
            self._insert(f"doc{i}.pdf", [{"text": f"body {i}", "page_or_sheet": 1}])
        corpus = self.store.load_corpus(limit=3)
        self.assertEqual(len(corpus["documents"]), 3)
        self.assertTrue(corpus["truncated"])
        self.assertEqual(corpus["total_documents"], 5)

    def test_truncated_means_the_cap_bit_not_that_a_row_was_unusable(self):
        """Two different causes; reporting them as one misleads the reader.

        A corpus shrunk by an empty row has not been capped, and telling the
        user older documents are outside their answer would be false.
        """
        self._insert("empty.pdf", [])
        self._insert("real.pdf", [{"text": "alpha", "page_or_sheet": 1}])
        corpus = self.store.load_corpus(limit=25)
        self.assertFalse(corpus["truncated"])
        self.assertEqual(corpus["skipped"], ["empty.pdf"])

    def test_the_newest_documents_win_the_cap(self):
        for i in range(4):
            self._insert(f"doc{i}.pdf", [{"text": f"body {i}", "page_or_sheet": 1}])
        names = [d["file_name"] for d in self.store.load_corpus(limit=2)["documents"]]
        self.assertEqual(names, ["doc3.pdf", "doc2.pdf"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

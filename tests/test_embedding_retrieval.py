"""Tests for embedding-based chunk retrieval and its keyword fallback.

The fallback tests patch `utils.embeddings` rather than uninstalling anything,
so they run identically on a machine with or without the model present.

Run:
    pytest tests/test_embedding_retrieval.py -v
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import numpy as np

from core.models import DocumentChunk, ParsedDocument
from core.pipeline_result import PipelineResult
from skills.document_chat_skill import DocumentChatSkill
from utils import embeddings
from utils.document_store import DocumentStore

CHUNKS = [
    {"text": "Aggregate downtime across the fleet totalled 41,300 off-road hours.",
     "page_or_sheet": 1},
    {"text": "Lead time from requisition to delivery averaged 94 days for tractor units.",
     "page_or_sheet": 2},
    {"text": "Attrition among the driving workforce reached 14.2 percent for the year.",
     "page_or_sheet": 3},
]


class TestLazyLoading(unittest.TestCase):
    def test_importing_does_not_load_the_model(self):
        """Import must stay free; `streamlit run` and pytest should not pay for it.

        Runs in a subprocess on purpose. Checking `sys.modules` in-process is
        meaningless once any other test in the session has loaded the model —
        it passes alone and fails in a full run, which is a property of the test
        rather than of the code. A clean interpreter is the only honest check.
        """
        import subprocess
        import sys as _sys

        probe = (
            "import sys; sys.path.insert(0, r'%s');"
            "import skills.document_chat_skill;"
            "print('LOADED' if 'sentence_transformers' in sys.modules else 'LAZY')"
            % str(Path(__file__).resolve().parents[1])
        )
        out = subprocess.run([_sys.executable, "-c", probe],
                             capture_output=True, text=True, timeout=180)
        self.assertIn("LAZY", out.stdout,
                      f"model library imported at import time: {out.stdout}{out.stderr}")

    def test_is_supported_does_not_load_the_model(self):
        embeddings._model = None
        embeddings.is_supported()
        self.assertIsNone(embeddings._model,
                          "is_supported() must not trigger a model load")


class TestFallback(unittest.TestCase):
    """Retrieval must degrade to keyword overlap, not fail, without the model."""

    def setUp(self):
        self.skill = DocumentChatSkill(config={})
        self._real_supported = embeddings.is_supported
        self._real_encode = embeddings.encode

    def tearDown(self):
        embeddings.is_supported = self._real_supported
        embeddings.encode = self._real_encode

    def test_unsupported_library_falls_back(self):
        embeddings.is_supported = lambda: False
        scores, method = self.skill.score_chunks("downtime hours", CHUNKS)
        self.assertEqual(method, "keyword_overlap")
        self.assertEqual(len(scores), len(CHUNKS))

    def test_model_load_failure_falls_back(self):
        embeddings.is_supported = lambda: True
        embeddings.encode = lambda texts: None      # simulates a failed load
        scores, method = self.skill.score_chunks("downtime hours", CHUNKS)
        self.assertEqual(method, "keyword_overlap")

    def test_fallback_still_selects_chunks(self):
        embeddings.is_supported = lambda: False
        selected = self.skill._select_chunks("What were the off-road hours?", CHUNKS)
        self.assertTrue(selected, "fallback must still return chunks")
        self.assertIn(1, [c["page_or_sheet"] for c in selected])

    def test_method_is_recorded_for_logging(self):
        embeddings.is_supported = lambda: False
        self.skill._select_chunks("anything", CHUNKS)
        self.assertEqual(self.skill._last_retrieval_method, "keyword_overlap")


class TestEmbeddingRetrieval(unittest.TestCase):
    """Requires the model. Skipped rather than failed when it is unavailable."""

    @classmethod
    def setUpClass(cls):
        if not embeddings.is_supported() or embeddings.encode(["probe"]) is None:
            raise unittest.SkipTest("embedding model unavailable")
        cls.skill = DocumentChatSkill(config={})

    def test_synonym_query_ranks_the_right_chunk(self):
        """The case keyword overlap cannot do: no shared content word."""
        scores, method = self.skill.score_chunks(
            "How much time were lorries unavailable for work?", CHUNKS)
        self.assertEqual(method, "embedding")
        self.assertEqual(int(np.argmax(scores)), 0,
                         "should rank the off-road hours chunk first")

    def test_scores_are_cosine_bounded(self):
        scores, _ = self.skill.score_chunks("delivery lead time", CHUNKS)
        for s in scores:
            self.assertGreaterEqual(s, -1.0001)
            self.assertLessEqual(s, 1.0001)

    def test_precomputed_embeddings_are_used(self):
        """A chunk carrying a vector must not be re-embedded."""
        matrix = embeddings.encode([c["text"] for c in CHUNKS])
        enriched = [dict(c, embedding=v.tolist()) for c, v in zip(CHUNKS, matrix)]

        calls = []
        real_encode = embeddings.encode

        def counting_encode(texts):
            calls.append(list(texts))
            return real_encode(texts)

        embeddings.encode = counting_encode
        try:
            _, method = self.skill.score_chunks("downtime", enriched)
        finally:
            embeddings.encode = real_encode

        self.assertEqual(method, "embedding")
        self.assertEqual(len(calls), 1, "only the query should be embedded")
        self.assertEqual(calls[0], ["downtime"])


class TestBlobRoundTrip(unittest.TestCase):
    def test_round_trips_exactly(self):
        matrix = np.random.default_rng(0).standard_normal(
            (5, embeddings.EMBEDDING_DIM)).astype(np.float32)
        restored = embeddings.from_blob(embeddings.to_blob(matrix))
        np.testing.assert_array_equal(matrix, restored)

    def test_rejects_wrong_size(self):
        self.assertIsNone(embeddings.from_blob(embeddings.to_blob(
            np.zeros((1, 7), dtype=np.float32))))

    def test_handles_empty_and_garbage(self):
        self.assertIsNone(embeddings.from_blob(""))
        self.assertIsNone(embeddings.from_blob("!!!not base64!!!"))


class TestStorePersistence(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "history.db"
        self.store = DocumentStore(db_path=self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def _result(self):
        chunks = [DocumentChunk(text=c["text"], page_or_sheet=c["page_or_sheet"],
                                chunk_index=i) for i, c in enumerate(CHUNKS)]
        parsed = ParsedDocument(file_name="d.pdf", file_type="pdf", chunks=chunks,
                                full_text=" ".join(c["text"] for c in CHUNKS),
                                tables=[], metadata={}, page_count=len(chunks))
        return PipelineResult(
            file_name="d.pdf", file_type="pdf", doc_type="normal_document",
            domain="General", classification_confidence=0.5,
            classification_method="heuristic", summary="s", summary_method="llm",
            questions=[], question_extraction_method="skipped",
            raw_text=parsed.full_text, word_count=10, page_count=len(chunks),
            metadata={}, parsed_document=parsed, success=True)

    def test_migration_adds_columns_without_losing_rows(self):
        legacy = Path(self._tmp.name) / "legacy.db"
        conn = sqlite3.connect(legacy)
        try:
            with conn:
                conn.executescript("""
                    CREATE TABLE history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        file_name TEXT NOT NULL, file_hash TEXT NOT NULL,
                        file_type TEXT, doc_type TEXT, domain TEXT,
                        word_count INTEGER DEFAULT 0, question_count INTEGER DEFAULT 0,
                        processing_time_ms REAL DEFAULT 0, result_json TEXT NOT NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
                """)
                conn.execute(
                    "INSERT INTO history (file_name, file_hash, result_json) VALUES (?,?,?)",
                    ("old.pdf", "h", '{"file_name": "old.pdf", "summary": "old"}'))
        finally:
            conn.close()

        store = DocumentStore(db_path=legacy)

        conn = sqlite3.connect(legacy)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(history)")}
            rows = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
        finally:
            conn.close()

        self.assertIn("content_embeddings_b64", cols)
        self.assertIn("embedding_model", cols)
        self.assertEqual(rows, 1, "existing rows must survive")
        loaded = store.load(1)
        self.assertEqual(loaded["summary"], "old")
        self.assertEqual(loaded["content_chunks"], [])

    def test_embeddings_round_trip_through_the_store(self):
        if not embeddings.is_supported() or embeddings.encode(["probe"]) is None:
            self.skipTest("embedding model unavailable")
        entry_id = self.store.save(self._result())
        loaded = self.store.load(entry_id)
        vectors = [c.get("embedding") for c in loaded["content_chunks"]]
        self.assertTrue(all(v is not None for v in vectors))
        self.assertEqual(len(vectors[0]), embeddings.EMBEDDING_DIM)
        self.assertEqual(loaded["embedding_model"], embeddings.model_name())

    def test_model_mismatch_is_ignored_not_trusted(self):
        if not embeddings.is_supported() or embeddings.encode(["probe"]) is None:
            self.skipTest("embedding model unavailable")
        entry_id = self.store.save(self._result())
        conn = sqlite3.connect(self.db)
        try:
            with conn:
                conn.execute("UPDATE history SET embedding_model = ? WHERE id = ?",
                             ("some-other-model", entry_id))
        finally:
            conn.close()

        loaded = self.store.load(entry_id)
        self.assertTrue(all("embedding" not in c for c in loaded["content_chunks"]),
                        "vectors from a different model must not be attached")


if __name__ == "__main__":
    unittest.main(verbosity=2)

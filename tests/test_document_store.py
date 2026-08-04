"""Tests for DocumentStore persistence of document content.

Uses a temporary database — never the user's ~/.docagent/history.db.

Run:
    pytest tests/test_document_store.py -v
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.models import DocumentChunk, ParsedDocument
from core.pipeline_result import PipelineResult
from utils.document_store import DocumentStore


def sqlite_do(path: Path, fn):
    """Run fn(conn) and always close.

    `with sqlite3.connect(...)` commits but does not close, which leaves the
    file locked on Windows — the same trap this module's _connect() had to fix.
    """
    conn = sqlite3.connect(path)
    try:
        with conn:
            return fn(conn)
    finally:
        conn.close()


def make_result(text: str = "Alpha beta gamma. Delta epsilon.") -> PipelineResult:
    chunks = [
        DocumentChunk(text="Alpha beta gamma.", page_or_sheet=1, chunk_index=0),
        DocumentChunk(text="Delta epsilon.", page_or_sheet=2, chunk_index=1),
    ]
    parsed = ParsedDocument(
        file_name="sample.pdf", file_type="pdf", chunks=chunks,
        full_text=text, tables=[], metadata={}, page_count=2,
    )
    return PipelineResult(
        file_name="sample.pdf", file_type="pdf", doc_type="normal_document",
        domain="Technical", classification_confidence=0.5,
        classification_method="heuristic", summary="A summary.",
        summary_method="llm", questions=[], question_extraction_method="skipped",
        raw_text=text, word_count=len(text.split()), page_count=2, metadata={},
        parsed_document=parsed, success=True,
    )


class TestContentPersistence(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "history.db"
        self.store = DocumentStore(db_path=self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def test_raw_text_and_chunks_round_trip(self):
        result = make_result()
        entry_id = self.store.save(result)
        loaded = self.store.load(entry_id)

        self.assertEqual(loaded["raw_text"], result.raw_text)
        self.assertEqual(len(loaded["content_chunks"]), 2)
        self.assertEqual(loaded["content_chunks"][0]["text"], "Alpha beta gamma.")
        self.assertEqual(loaded["content_chunks"][1]["page_or_sheet"], 2)

    def test_download_json_export_is_unchanged(self):
        """Content lives in its own columns, not in the user-facing export."""
        result = make_result()
        exported = result.to_dict()
        self.assertNotIn("raw_text", exported)
        self.assertNotIn("content_chunks", exported)

    def test_result_without_parsed_document_still_saves(self):
        result = make_result()
        result.parsed_document = None
        entry_id = self.store.save(result)
        loaded = self.store.load(entry_id)
        self.assertEqual(loaded["raw_text"], result.raw_text)
        self.assertEqual(loaded["content_chunks"], [])

    def test_migration_adds_columns_and_preserves_rows(self):
        """An older database without the content columns must survive upgrade."""
        legacy = Path(self._tmp.name) / "legacy.db"

        def build(conn):
            conn.executescript("""
                CREATE TABLE history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_name TEXT NOT NULL, file_hash TEXT NOT NULL,
                    file_type TEXT, doc_type TEXT, domain TEXT,
                    word_count INTEGER DEFAULT 0, question_count INTEGER DEFAULT 0,
                    processing_time_ms REAL DEFAULT 0, result_json TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.execute(
                "INSERT INTO history (file_name, file_hash, result_json) VALUES (?,?,?)",
                ("old.pdf", "hash", '{"file_name": "old.pdf", "summary": "old"}'),
            )

        sqlite_do(legacy, build)

        store = DocumentStore(db_path=legacy)  # triggers migration

        cols = sqlite_do(legacy, lambda c: {r[1] for r in c.execute("PRAGMA table_info(history)")})
        rows = sqlite_do(legacy, lambda c: c.execute("SELECT COUNT(*) FROM history").fetchone()[0])

        self.assertIn("content_text", cols)
        self.assertIn("content_chunks_json", cols)
        self.assertEqual(rows, 1, "existing rows must be preserved")

        loaded = store.load(1)
        self.assertEqual(loaded["summary"], "old")
        self.assertEqual(loaded["raw_text"], "", "pre-migration rows have no content")
        self.assertEqual(loaded["content_chunks"], [])

    def test_reanalysis_updates_instead_of_duplicating(self):
        """Re-analysing the same source must not add a second history row."""
        first = self.store.save(make_result(), raw_bytes=b"same-bytes")
        second = self.store.save(make_result(), raw_bytes=b"same-bytes")

        self.assertEqual(first, second, "the same source should reuse its row")
        rows = sqlite_do(self.db, lambda c: c.execute(
            "SELECT COUNT(*) FROM history").fetchone()[0])
        self.assertEqual(rows, 1, "expected one row, not a duplicate")

    def test_reanalysis_refreshes_the_stored_result(self):
        original = make_result()
        entry_id = self.store.save(original, raw_bytes=b"same-bytes")

        updated = make_result()
        updated.summary = "A revised summary."
        updated.word_count = 999
        self.store.save(updated, raw_bytes=b"same-bytes")

        loaded = self.store.load(entry_id)
        self.assertEqual(loaded["summary"], "A revised summary.")
        self.assertEqual(loaded["word_count"], 999)

    def test_different_sources_still_get_their_own_rows(self):
        self.store.save(make_result(), raw_bytes=b"document-one")
        self.store.save(make_result(), raw_bytes=b"document-two")
        rows = sqlite_do(self.db, lambda c: c.execute(
            "SELECT COUNT(*) FROM history").fetchone()[0])
        self.assertEqual(rows, 2, "distinct sources must not collapse together")

    def test_migration_is_idempotent(self):
        DocumentStore(db_path=self.db)
        DocumentStore(db_path=self.db)  # must not raise "duplicate column"
        cols = sqlite_do(self.db, lambda c: [r[1] for r in c.execute("PRAGMA table_info(history)")])
        self.assertEqual(cols.count("content_text"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

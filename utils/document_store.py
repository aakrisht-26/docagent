"""
DocumentStore — SQLite-backed persistent storage for PipelineResult objects.

Stores processed document analysis results so users can reload them without
re-uploading and re-processing. Results survive Streamlit reruns and browser
refreshes.

Usage:
    store = DocumentStore()
    store.save(result)

    recent = store.list_recent(limit=20)
    result = store.load(entry_id=123)

    store.delete(entry_id=123)
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.logger import get_logger

logger = get_logger(__name__)

# Default DB path: ~/.docagent/history.db
_DEFAULT_DB_PATH = Path.home() / ".docagent" / "history.db"


@dataclass
class HistoryEntry:
    """Lightweight summary of a stored PipelineResult (for list views)."""
    entry_id:    int
    file_name:   str
    file_type:   str
    doc_type:    str
    domain:      str
    word_count:  int
    summary_snippet: str          # first 200 chars of summary
    question_count:  int
    processing_time_ms: float
    created_at:  str              # ISO 8601 timestamp


class DocumentStore:
    """
    SQLite-backed store for PipelineResult history.

    Thread-safety: each call opens and closes its own connection using the
    sqlite3 check_same_thread=False flag. Not suitable for high-concurrency
    servers — for that, use a proper RDBMS backend.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = Path(db_path or _DEFAULT_DB_PATH)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── Initialisation ─────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS history (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_name           TEXT    NOT NULL,
                    file_hash           TEXT    NOT NULL,
                    file_type           TEXT,
                    doc_type            TEXT,
                    domain              TEXT,
                    word_count          INTEGER DEFAULT 0,
                    question_count      INTEGER DEFAULT 0,
                    processing_time_ms  REAL    DEFAULT 0,
                    result_json         TEXT    NOT NULL,
                    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_history_file_hash
                    ON history (file_hash);

                CREATE INDEX IF NOT EXISTS idx_history_created_at
                    ON history (created_at DESC);
            """)

    # ── Write ──────────────────────────────────────────────────────────────────

    def save(self, result: Any, raw_bytes: Optional[bytes] = None) -> int:
        """
        Persist a PipelineResult to the store.

        Args:
            result:    A PipelineResult instance.
            raw_bytes: Original file bytes used to compute a content hash.
                       If None, hash is derived from file_name + word_count.

        Returns:
            The new entry's integer ID.
        """
        file_hash = (
            hashlib.sha256(raw_bytes).hexdigest()
            if raw_bytes is not None
            else hashlib.sha256(
                f"{result.file_name}:{result.word_count}".encode()
            ).hexdigest()
        )

        # Remove parsed_document (not serialisable) before JSON serialisation
        result_dict = result.to_dict()
        result_json = json.dumps(result_dict, default=str, ensure_ascii=False)

        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO history
                    (file_name, file_hash, file_type, doc_type, domain,
                     word_count, question_count, processing_time_ms, result_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.file_name,
                    file_hash,
                    result.file_type,
                    result.doc_type,
                    result.domain,
                    result.word_count,
                    len(result.questions),
                    result.processing_time_ms,
                    result_json,
                ),
            )
            entry_id = cursor.lastrowid
            logger.info(f"DocumentStore: saved '{result.file_name}' as entry #{entry_id}")
            return entry_id

    # ── Read ───────────────────────────────────────────────────────────────────

    def list_recent(self, limit: int = 20) -> List[HistoryEntry]:
        """Return the most recent `limit` entries as lightweight HistoryEntry objects."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, file_name, file_type, doc_type, domain,
                       word_count, question_count, processing_time_ms,
                       result_json, created_at
                FROM history
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        entries: List[HistoryEntry] = []
        for row in rows:
            (eid, fname, ftype, dtype, dom, wc, qc, pms, rjson, ts) = row
            try:
                summary = json.loads(rjson).get("summary", "") or ""
                snippet = (summary[:200] + "…") if len(summary) > 200 else summary
            except Exception:
                snippet = ""
            entries.append(HistoryEntry(
                entry_id=eid,
                file_name=fname,
                file_type=ftype or "",
                doc_type=dtype or "",
                domain=dom or "",
                word_count=wc or 0,
                summary_snippet=snippet,
                question_count=qc or 0,
                processing_time_ms=pms or 0.0,
                created_at=ts or "",
            ))
        return entries

    def load(self, entry_id: int) -> Optional[Dict[str, Any]]:
        """
        Load a full result dict by entry ID.

        Returns the raw dict (as stored by PipelineResult.to_dict()), or None.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT result_json FROM history WHERE id = ?",
                (entry_id,),
            ).fetchone()

        if row is None:
            return None
        try:
            return json.loads(row[0])
        except json.JSONDecodeError as exc:
            logger.error(f"DocumentStore: corrupt entry #{entry_id}: {exc}")
            return None

    def find_by_hash(self, file_hash: str) -> Optional[Dict[str, Any]]:
        """Return the most recent result for a given file hash (cache lookup)."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT result_json FROM history
                WHERE file_hash = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (file_hash,),
            ).fetchone()

        if row is None:
            return None
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return None

    # ── Delete ─────────────────────────────────────────────────────────────────

    def delete(self, entry_id: int) -> bool:
        """Delete an entry by ID. Returns True if a row was deleted."""
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM history WHERE id = ?", (entry_id,))
            return cursor.rowcount > 0

    def clear_all(self) -> int:
        """Delete all history. Returns number of deleted rows."""
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM history")
            return cursor.rowcount

    # ── Stats ──────────────────────────────────────────────────────────────────

    def count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]

    # ── Internals ──────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

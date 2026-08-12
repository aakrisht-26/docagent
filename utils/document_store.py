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
from contextlib import contextmanager
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

            # Document content is stored outside result_json on purpose:
            # PipelineResult.to_dict() feeds the user-facing "Download JSON"
            # export, and stuffing the full document text into that export would
            # change what the download contains. These columns are added
            # separately so history reload gains content without altering the
            # export. Added via migration so existing databases keep working.
            existing = {row[1] for row in conn.execute("PRAGMA table_info(history)")}
            for column in ("content_text", "content_chunks_json",
                           # Chunk embeddings, base64 float32, plus the model
                           # that produced them. Vectors are only comparable to
                           # vectors from the same model, so the name is stored
                           # with them and checked on load rather than trusted.
                           "content_embeddings_b64", "embedding_model",
                           # How content_chunks_json was split, e.g.
                           # "passage:100/20". Vectors are only comparable to
                           # vectors built over the same text, so a change of
                           # chunking invalidates them exactly as a change of
                           # model does — and without this column that would be
                           # undetectable. Rows written before sub-chunking have
                           # NULL here, which reads as page-level.
                           "chunk_scheme"):
                if column not in existing:
                    conn.execute(f"ALTER TABLE history ADD COLUMN {column} TEXT")

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

        # Document content, stored separately from result_json so the
        # "Download JSON" export is unchanged. Without this, a result reloaded
        # from history has no text at all, and the Chat and Edit tabs silently
        # operate on an empty document.
        content_text = getattr(result, "raw_text", "") or ""
        parsed_doc = getattr(result, "parsed_document", None)
        page_chunks = getattr(parsed_doc, "chunks", None) if parsed_doc else None

        # Split into passages for the RETRIEVAL index only. parsed_document is
        # not modified, so summarisation continues to see whole pages — that is
        # a separate pipeline path and it is working.
        #
        # Each passage keeps the page_or_sheet of the page it came from, so a
        # citation still resolves to "Page 3".
        from utils.chunking import scheme_name, sub_chunk
        chunks = sub_chunk(
            [{"text": c.text, "page_or_sheet": c.page_or_sheet} for c in page_chunks]
        ) if page_chunks else None
        chunk_scheme = scheme_name() if chunks else None
        content_chunks_json = json.dumps(chunks, ensure_ascii=False) if chunks else None

        # Embed once, here, so a document reloaded from history is queryable
        # without paying the model cost again. Best-effort: if the model is
        # unavailable the row is still written and chat falls back to keyword
        # overlap, exactly as it would for a pre-migration row.
        embeddings_b64 = None
        embedding_model = None
        if chunks:
            from utils import embeddings as _emb
            matrix = _emb.encode([c["text"] for c in chunks])
            if matrix is not None and len(matrix) == len(chunks):
                embeddings_b64 = _emb.to_blob(matrix)
                embedding_model = _emb.model_name()

        columns = (
            result.file_name, file_hash, result.file_type, result.doc_type,
            result.domain, result.word_count, len(result.questions),
            result.processing_time_ms, result_json, content_text,
            content_chunks_json, embeddings_b64, embedding_model, chunk_scheme,
        )

        with self._connect() as conn:
            # Re-analysing the same source UPDATES its row rather than adding a
            # second one. Previously every run appended, so the sidebar filled
            # with entries that were byte-identical in label and content and
            # could not be told apart.
            #
            # The one argument for appending would be preserving runs made with
            # different summary settings — but file_hash is derived from the
            # raw bytes alone and ignores those settings, so both runs collide
            # on the same hash and produce duplicates rather than distinguishable
            # variants. Keying on the hash is also what the schema always
            # intended: the column and its index exist, and find_by_hash() was
            # written for exactly this and then never wired up.
            existing = conn.execute(
                "SELECT id FROM history WHERE file_hash = ? ORDER BY id DESC LIMIT 1",
                (file_hash,),
            ).fetchone()

            if existing is not None:
                entry_id = existing[0]
                conn.execute(
                    """
                    UPDATE history SET
                        file_name = ?, file_hash = ?, file_type = ?, doc_type = ?,
                        domain = ?, word_count = ?, question_count = ?,
                        processing_time_ms = ?, result_json = ?, content_text = ?,
                        content_chunks_json = ?, content_embeddings_b64 = ?,
                        embedding_model = ?, chunk_scheme = ?,
                        created_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (*columns, entry_id),
                )
                logger.info(
                    f"DocumentStore: updated '{result.file_name}' (entry #{entry_id})"
                )
                return entry_id

            cursor = conn.execute(
                """
                INSERT INTO history
                    (file_name, file_hash, file_type, doc_type, domain,
                     word_count, question_count, processing_time_ms, result_json,
                     content_text, content_chunks_json,
                     content_embeddings_b64, embedding_model, chunk_scheme)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                columns,
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

        Returns the dict stored by PipelineResult.to_dict(), with two extra keys
        merged in from their own columns:

            raw_text       — the document text
            content_chunks — [{"text", "page_or_sheet"}, ...]

        Without these a reloaded result carries no document content, and the
        Chat and Edit tabs operate on an empty document without saying so.
        Entries written before these columns existed simply return empty values.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT result_json, content_text, content_chunks_json, "
                "content_embeddings_b64, embedding_model, chunk_scheme "
                "FROM history WHERE id = ?",
                (entry_id,),
            ).fetchone()

        if row is None:
            return None
        try:
            data = json.loads(row[0])
        except json.JSONDecodeError as exc:
            logger.error(f"DocumentStore: corrupt entry #{entry_id}: {exc}")
            return None

        data["raw_text"] = row[1] or ""
        try:
            data["content_chunks"] = json.loads(row[2]) if row[2] else []
        except json.JSONDecodeError:
            logger.warning(f"DocumentStore: unreadable chunks for entry #{entry_id}")
            data["content_chunks"] = []

        # Attach stored vectors to their chunks so retrieval needs no
        # re-embedding. Skipped when the row predates embeddings, when the
        # stored vectors came from a different model, or when the counts do not
        # line up — in each case chat re-embeds or falls back, rather than
        # comparing vectors that are not comparable.
        self._attach_embeddings(data, row[3], row[4], entry_id, row[5])
        return data

    @staticmethod
    def _attach_embeddings(data: Dict[str, Any], blob: Optional[str],
                           stored_model: Optional[str], entry_id: int,
                           stored_scheme: Optional[str] = None) -> None:
        chunks = data.get("content_chunks") or []
        if not blob or not chunks:
            return

        from utils import embeddings as _emb
        from utils.chunking import scheme_name

        # Chunking is as much a part of a vector's identity as the model is: a
        # vector describes a specific piece of text, and re-splitting the
        # document changes which text that is. A row written under page-level
        # chunking (NULL scheme) or under different passage settings is
        # therefore ignored here rather than compared, and chat falls back to
        # keyword overlap for that row until it is re-analysed.
        current_scheme = scheme_name()
        if (stored_scheme or "page") != current_scheme:
            logger.info(
                f"DocumentStore: entry #{entry_id} was indexed as "
                f"'{stored_scheme or 'page'}' but '{current_scheme}' is active; "
                "ignoring stored vectors. Re-analyse the document to refresh it."
            )
            return

        if stored_model and stored_model != _emb.model_name():
            logger.info(
                f"DocumentStore: entry #{entry_id} was embedded with "
                f"'{stored_model}' but '{_emb.model_name()}' is active; "
                "ignoring stored vectors and re-embedding."
            )
            return

        matrix = _emb.from_blob(blob)
        if matrix is None:
            return
        if len(matrix) != len(chunks):
            logger.warning(
                f"DocumentStore: entry #{entry_id} has {len(matrix)} vectors for "
                f"{len(chunks)} chunks; ignoring stored vectors."
            )
            return

        for chunk, vector in zip(chunks, matrix):
            chunk["embedding"] = vector.tolist()
        data["embedding_model"] = stored_model

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

    @contextmanager
    def _connect(self):
        """
        Yield a connection and always close it.

        Previously this returned a bare sqlite3.Connection, and every call site
        used `with self._connect() as conn:`. That reads like it closes the
        connection, but sqlite3's context manager only commits or rolls back the
        transaction — it never closes. Every operation therefore leaked a
        connection until garbage collection: on Windows that keeps a lock on the
        database file, and in a long-lived Streamlit session the open handles
        accumulate.

        Call sites are unchanged; `with` now closes as it appears to.
        """
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            with conn:          # preserves the original commit/rollback semantics
                yield conn
        finally:
            conn.close()

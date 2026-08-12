"""
DocumentChatSkill — multi-turn conversational Q&A over a parsed document.

Uses a RAG-lite approach: keyword-overlap scoring selects the most relevant
chunks from the parsed document, which are injected as context before the
conversation history. No external vector store required.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

import numpy as np

from core.models import SkillInput, SkillOutput
from skills.base_skill import BaseSkill
from utils import embeddings
from utils.logger import get_logger

logger = get_logger(__name__)

_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "was", "are", "were", "be", "been",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "that", "this", "it", "not", "no", "can", "as", "so", "if",
    "what", "which", "who", "when", "where", "how", "i", "you", "we", "they",
})

# Max total chars of context to inject into the LLM prompt
_MAX_CONTEXT_CHARS = 6000
# Max conversation turns to keep before truncation
_MAX_HISTORY_TURNS = 20
# Estimated chars per token (rough)
_CHARS_PER_TOKEN = 4
_MAX_HISTORY_TOKENS = 20_000


class DocumentChatSkill(BaseSkill):
    """
    Answers questions about a document using conversation history and
    keyword-scored relevant chunks.

    SkillInput.data keys:
        user_message          (str)        — the new user message
        document_chunks       (List[Dict]) — [{"text": str, "page_or_sheet": ...}]
        conversation_history  (List[Dict]) — prior messages in OpenAI format
        domain                (str)        — document domain (optional)
    """

    name = "document_chat"
    description = "Answers questions about a document in a multi-turn conversation."
    required_inputs = ["user_message", "document_chunks", "conversation_history"]

    def execute(self, inputs: SkillInput) -> SkillOutput:
        start = time.monotonic()

        user_message: str    = inputs.data["user_message"]
        chunks: List[Dict]   = inputs.data["document_chunks"]
        history: List[Dict]  = inputs.data["conversation_history"]
        domain: str          = inputs.data.get("domain", "General")

        from utils.llm_client import LLMClient
        llm = LLMClient.from_config(self.config)
        if not llm.available:
            return SkillOutput(
                success=False, data=None,
                error="Chat requires an LLM. Set GROQ_API_KEY.",
                duration_ms=(time.monotonic() - start) * 1000,
            )

        # Select most relevant chunks
        selected_chunks = self._select_chunks(user_message, chunks)
        used_pages = [c["page_or_sheet"] for c in selected_chunks if c.get("page_or_sheet")]

        context_text = self._format_context(selected_chunks)

        # Truncate history if too long
        truncated_history = self._truncate_history(history)

        messages = [
            {
                "role": "system",
                "content": (
                    f"You are a precise {domain} document analyst. "
                    "Answer questions strictly based on the provided document context. "
                    "If the answer is not in the document, say so clearly. "
                    # The context is labelled per excerpt, e.g. "[Page 3]".
                    # Naming the exact format matters: asked only to "cite page
                    # numbers", with labels present, the model still preferred
                    # quoting section headings out of the body text.
                    "Each excerpt begins with its source in square brackets, such as "
                    "[Page 3] or [Sheet: Q3 Revenue]. When you state a fact, cite the "
                    "source it came from using exactly that label. Never cite a source "
                    "that is not shown in the context."
                ),
            },
            {
                "role": "user",
                "content": f"[DOCUMENT CONTEXT]\n{context_text}\n[END CONTEXT]",
            },
            {
                "role": "assistant",
                "content": "I have read the document context. Ask me anything about it.",
            },
            *truncated_history,
            {"role": "user", "content": user_message},
        ]

        reply = llm.chat(messages, max_tokens=1500)
        if reply is None:
            return SkillOutput(
                success=False, data=None,
                error="LLM returned no response.",
                duration_ms=(time.monotonic() - start) * 1000,
            )

        return SkillOutput(
            success=True,
            data={"reply": reply, "used_pages": used_pages},
            duration_ms=(time.monotonic() - start) * 1000,
        )

    # ── Chunk selection ────────────────────────────────────────────────────────

    def score_chunks(self, query: str, chunks: List[Dict]) -> tuple:
        """Return (scores, method): one relevance score per chunk, and its source.

        Embedding similarity is preferred; keyword overlap is the fallback when
        the model is unavailable. Exposed as a method so the retrieval eval can
        rank with exactly the scores the selector used, rather than
        reimplementing them and drifting.

        `method` is one of "embedding" or "keyword_overlap".
        """
        if not chunks:
            return [], "keyword_overlap"

        texts = [c.get("text", "") for c in chunks]

        # ── Embeddings ───────────────────────────────────────────────────
        # Chunks may already carry a stored vector (see DocumentStore), in
        # which case a document reloaded from history needs no re-embedding.
        if embeddings.is_supported():
            cached = [c.get("embedding") for c in chunks]
            if all(v is not None for v in cached):
                matrix = np.asarray(cached, dtype=np.float32)
            else:
                matrix = embeddings.encode(texts)

            if matrix is not None and len(matrix) == len(chunks):
                query_vector = embeddings.encode([query])
                if query_vector is not None and len(query_vector):
                    sims = embeddings.similarity(query_vector[0], matrix)
                    return [float(s) for s in sims], "embedding"

        # ── Keyword overlap fallback ─────────────────────────────────────
        query_words = self._tokenize(query)
        scores = [
            float(len(query_words & self._tokenize(text))) for text in texts
        ]
        return scores, "keyword_overlap"

    @staticmethod
    def source_label(chunk: Dict) -> str:
        """Human-readable origin of a chunk: 'Page 3' or 'Sheet: Q3 Revenue'."""
        source = chunk.get("page_or_sheet")
        if source is None or source == "":
            return "Unknown source"
        return f"Page {source}" if isinstance(source, int) else f"Sheet: {source}"

    def _format_context(self, selected: List[Dict]) -> str:
        """Label every chunk with where it came from, then join.

        This exists because the system prompt asks the model to "cite page
        numbers" while the context it receives carries none — it used to be
        `"\\n\\n---\\n\\n".join(c["text"] ...)`, bare text and separators. The
        model was being asked to attribute information it had never been told
        the location of.

        Measured before this change, over the 27 page-sourced eval cases: the
        model cited a page number in **zero** of them. It fell back to quoting
        section headings when the body text happened to contain one
        ("According to Section 3, 'Fleet Composition Overview'"), which reads
        like a citation but is only ever as good as the heading. The
        user-visible "Sources:" line came from `used_pages`, computed by this
        skill, so the citation a reader saw never came from the model at all.

        Labelling is also a hard prerequisite for multi-document chat, where
        "Page 3" is ambiguous across a corpus and a wrong attribution is worse
        than none.

        Truncation drops WHOLE chunks rather than slicing the joined string.
        The old `context_text[:_MAX_CONTEXT_CHARS]` could cut the final chunk
        mid-sentence while that chunk was still named in `used_pages` — citing
        a source whose text was partly absent, which is the same class of
        problem this method exists to fix.
        """
        parts: List[str] = []
        total = 0
        for chunk in selected:
            block = f"[{self.source_label(chunk)}]\n{chunk.get('text', '')}"
            # +5 for the "\n\n---\n\n" that will join it.
            if parts and total + len(block) + 5 > _MAX_CONTEXT_CHARS:
                break
            parts.append(block)
            total += len(block) + 5
        return "\n\n---\n\n".join(parts)

    def _select_chunks(self, query: str, chunks: List[Dict]) -> List[Dict]:
        """Return the top 3 most relevant chunks plus first + last (deduped).

        Ranking is by embedding similarity when available and by keyword overlap
        otherwise. The top-3 count, the first/last anchors and the context cap
        are all unchanged from the keyword-only version, so switching methods
        changes *what* is chosen but not *how many* slots there are.
        """
        if not chunks:
            return []

        scores, method = self.score_chunks(query, chunks)
        self._last_retrieval_method = method

        if not any(scores):
            # Nothing matched at all: keep the previous behaviour of handing
            # back the opening chunks rather than an arbitrary ranking.
            self.logger.info(
                f"Chunk retrieval via {method}: no chunk scored above zero; "
                f"falling back to the first {min(3, len(chunks))} chunk(s)."
            )
            return chunks[:3]

        order = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)

        # Three slots means three distinct SOURCES, not three chunks.
        #
        # With page-level chunks these are the same thing and this loop is a
        # no-op. With passages they are not: several passages of one page can
        # sweep the top three, spending the whole budget on one source. That is
        # not hypothetical — it cost the cross-boundary case dn-11, where the
        # top three came back as pages 3, 3 and 8 and page 4 never got a slot,
        # turning a hit into a miss while every other number improved.
        #
        # Taking each source's best passage keeps the context as wide as it was
        # before while still ranking on the tighter passage match.
        top = []
        seen_sources = set()
        for i in order:
            source = chunks[i].get("page_or_sheet")
            if source in seen_sources:
                continue
            seen_sources.add(source)
            top.append(chunks[i])
            if len(top) == 3:
                break

        self.logger.info(
            f"Chunk retrieval via {method}: selected "
            f"{[c.get('page_or_sheet') for c in top]} "
            f"from {len(chunks)} chunk(s) across {len(set(c.get('page_or_sheet') for c in chunks))} source(s) "
            f"(top score {scores[order[0]]:.3f})"
        )

        # Always include structural anchors (first and last chunk)
        anchors = []
        if chunks[0] not in top:
            anchors.append(chunks[0])
        if len(chunks) > 1 and chunks[-1] not in top:
            anchors.append(chunks[-1])

        # Merge and cap at _MAX_CONTEXT_CHARS
        result: List[Dict] = []
        total_chars = 0
        for chunk in top + anchors:
            text_len = len(chunk.get("text", ""))
            if total_chars + text_len > _MAX_CONTEXT_CHARS:
                break
            result.append(chunk)
            total_chars += text_len

        return result if result else chunks[:1]

    @staticmethod
    def _tokenize(text: str) -> set:
        """Content words, for the keyword-overlap fallback.

        Matches runs of three or more letters, PLUS letter-then-digit tokens
        such as `q1`, `q3` or `a8800`.

        The second alternative is not cosmetic. Without it the pattern is
        `[a-z]{3,}` only, which never sees "Q1" or "Q3" at all — so on a
        workbook with quarterly sheets every sheet scored identically and the
        selection was byte-identical no matter which quarter was asked about.
        Measured on the retrieval eval, admitting these tokens takes the
        fallback from 10/17 to 13/17.

        Bare numbers are deliberately excluded. Documents are full of figures
        (9140000, 41300), and admitting them would have every chunk matching on
        incidental digits.
        """
        words = re.findall(r"\b[a-z]{3,}\b|\b[a-z]+\d+\b", text.lower())
        return {w for w in words if w not in _STOPWORDS}

    @staticmethod
    def _truncate_history(history: List[Dict]) -> List[Dict]:
        """Keep first 2 turns for topic context + most recent turns within token budget."""
        if not history:
            return []
        total_chars = sum(len(m.get("content", "")) for m in history)
        if total_chars / _CHARS_PER_TOKEN <= _MAX_HISTORY_TOKENS:
            return history

        # Keep first 2 turns + fill from the end
        anchor = history[:2]
        remaining_budget = _MAX_HISTORY_TOKENS * _CHARS_PER_TOKEN - sum(len(m.get("content", "")) for m in anchor)
        tail: List[Dict] = []
        for msg in reversed(history[2:]):
            msg_len = len(msg.get("content", ""))
            if remaining_budget - msg_len < 0:
                break
            tail.insert(0, msg)
            remaining_budget -= msg_len

        return anchor + tail

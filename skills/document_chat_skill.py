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

from core.models import SkillInput, SkillOutput
from skills.base_skill import BaseSkill
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

        context_text = "\n\n---\n\n".join(c["text"] for c in selected_chunks)
        if len(context_text) > _MAX_CONTEXT_CHARS:
            context_text = context_text[:_MAX_CONTEXT_CHARS] + "\n\n[Context truncated…]"

        # Truncate history if too long
        truncated_history = self._truncate_history(history)

        messages = [
            {
                "role": "system",
                "content": (
                    f"You are a precise {domain} document analyst. "
                    "Answer questions strictly based on the provided document context. "
                    "If the answer is not in the document, say so clearly. "
                    "Cite page numbers or section names when possible."
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

    def _select_chunks(self, query: str, chunks: List[Dict]) -> List[Dict]:
        """Score chunks by keyword overlap, return top 3 + first + last (deduped)."""
        if not chunks:
            return []

        query_words = self._tokenize(query)
        if not query_words:
            return chunks[:3]

        scored = []
        for chunk in chunks:
            chunk_words = self._tokenize(chunk.get("text", ""))
            overlap = len(query_words & chunk_words)
            scored.append((overlap, chunk))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = [c for _, c in scored[:3]]

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
        words = re.findall(r"\b[a-z]{3,}\b", text.lower())
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

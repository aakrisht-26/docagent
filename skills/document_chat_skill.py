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

# Distinct sources handed to the model. One document gets 3 plus first/last
# anchors; a corpus gets 6 and no anchors.
#
# Anchors are the opening and closing chunk, added whatever the question is, to
# give the model a sense of the document. Across a corpus that idea does not
# survive: the "first" chunk is page 1 of whichever document happens to sort
# first, and the "last" is the tail of another. Measured on the cross-document
# eval, they spent 14% of the context budget on every query, on documents
# nobody had asked about. So multi-document drops them and spends the budget on
# ranked matches instead.
_SINGLE_DOC_SLOTS = 3
_MULTI_DOC_SLOTS = 6

# Documents searchable in one query. Not a memory limit -- 25 documents is
# roughly 9 MB of vectors against a ~643 MB steady state. It is an ANSWER
# QUALITY limit: 6 slots spread over a large corpus stop being enough for any
# one document to be properly represented, and the failure is silent. A
# documented cap that can be raised beats an unbounded corpus that quietly
# degrades.
MAX_CORPUS_DOCUMENTS = 25
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
        multi_doc = self.is_multi_doc(chunks)
        selected_chunks, ranked_chunks = self._select_with_roles(user_message, chunks)
        used_pages = self._citable_sources(ranked_chunks, selected_chunks)
        used_sources = self.citable_sources_detailed(ranked_chunks, selected_chunks)

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
                    + (
                        # Across a corpus the document is the load-bearing half
                        # of the citation. Naming only the page is not a weaker
                        # answer, it is a wrong one: several documents have a
                        # page 3, and the reader cannot tell which was meant.
                        "The excerpts come from SEVERAL DIFFERENT DOCUMENTS. Each "
                        "begins with its source in square brackets, naming the "
                        "document and then the location, such as "
                        "[report.pdf, Page 3] or [sales.xlsx, Sheet: Q3 Revenue]. "
                        "When you state a fact, cite it using exactly that label, "
                        "INCLUDING the document name — a page number alone is "
                        "ambiguous because several documents have a page 3. If two "
                        "documents disagree, say so and attribute each. Never cite "
                        "a source that is not shown in the context."
                        if multi_doc else
                        "Each excerpt begins with its source in square brackets, such as "
                        "[Page 3] or [Sheet: Q3 Revenue]. When you state a fact, cite the "
                        "source it came from using exactly that label. Never cite a source "
                        "that is not shown in the context."
                    )
                ),
            },
            {
                "role": "user",
                "content": (
                    f"[{'CORPUS CONTEXT' if multi_doc else 'DOCUMENT CONTEXT'}]\n"
                    f"{context_text}\n[END CONTEXT]"
                ),
            },
            {
                "role": "assistant",
                "content": (
                    "I have read the excerpts from these documents. Ask me anything "
                    "about them." if multi_doc else
                    "I have read the document context. Ask me anything about it."
                ),
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
            data={
                "reply": reply,
                # Bare page labels, unchanged, for every existing reader.
                "used_pages": used_pages,
                # The same citations with the document kept separate, which is
                # what the UI renders and what a corpus answer needs.
                "used_sources": used_sources,
                "multi_doc": multi_doc,
                "documents_searched": sorted(
                    {c["document"] for c in chunks if c.get("document")}
                ),
            },
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
            # Per-chunk, not all-or-nothing.
            #
            # This used to be `if all(v is not None for v in cached)` with a
            # full re-encode otherwise, which meant ONE chunk without a stored
            # vector re-embedded the entire corpus on every query. Measured: 30
            # cached chunks plus one missing encoded 31 texts instead of 1.
            #
            # Single-document that is a slow first query. Across a corpus it is
            # a cliff — one document saved before vectors existed, or under a
            # different chunk_scheme, taxes every query over everything else.
            # Measured at 100 documents (2400 passages): 84 ms fully cached
            # against 1866 ms with a single vector missing.
            #
            # Now the gaps are encoded and the rest are reused, so a legacy
            # document costs only itself.
            cached = [c.get("embedding") for c in chunks]
            missing = [i for i, v in enumerate(cached) if v is None]

            matrix = None
            if not missing:
                matrix = np.asarray(cached, dtype=np.float32)
            else:
                filled = embeddings.encode([texts[i] for i in missing])
                if filled is not None and len(filled) == len(missing):
                    dim = len(filled[0])
                    # A stored vector of the wrong width would corrupt the
                    # matrix silently and rank against nonsense. Any mismatch
                    # sends the whole corpus back through the model, which is
                    # slow but correct — the same trade DocumentStore makes
                    # when the recorded model or chunk_scheme does not match.
                    if all(v is None or len(v) == dim for v in cached):
                        if len(missing) < len(chunks):
                            self.logger.debug(
                                "Embedding %d of %d chunk(s); the rest came "
                                "from stored vectors.", len(missing), len(chunks))
                        matrix = np.empty((len(chunks), dim), dtype=np.float32)
                        for slot, index in enumerate(missing):
                            matrix[index] = filled[slot]
                        for index, vector in enumerate(cached):
                            if vector is not None:
                                matrix[index] = np.asarray(vector, dtype=np.float32)
                    else:
                        self.logger.warning(
                            "Stored vectors have inconsistent dimensions; "
                            "re-embedding all %d chunk(s).", len(chunks))
                        matrix = embeddings.encode(texts)
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

    def _citable_sources(self, ranked: List[Dict], selected: List[Dict]) -> List:
        """Sources worth showing the user, in rank order and deduplicated.

        Takes the RANKED subset, not everything sent to the model. Anchors are
        appended to every selection regardless of the question, so listing them
        attributes the answer to pages that were never chosen for it — a
        one-page fact rendered as "Sources: 3, 5, 7, 1, 20", where 1 and 20 are
        merely the document's first and last chunks.

        The role has to come from the selector rather than be re-derived here.
        A chunk can be both the top match and positionally first, and treating
        "is an anchor position" as "was only an anchor" dropped page 1 from
        `dn-01` — the very page the answer came from.

        Deduplicated because with passages several fragments of one page can be
        selected, and "Page 3, Page 3" is noise.

        Falls back to the full selection when nothing was ranked — a document
        short enough that the anchors are the whole of it. They genuinely are
        where the answer came from then, and an empty source line would be less
        honest rather than more.
        """
        if not ranked:
            ranked = selected

        seen = set()
        sources = []
        for chunk in ranked:
            source = chunk.get("page_or_sheet")
            if source in (None, ""):
                continue
            # Deduplicate on the SAME key the selector used. Deduplicating on
            # the bare page label here would silently merge page 3 of two
            # different documents back into one citation, undoing the fix one
            # layer further down.
            key = self.source_key(chunk)
            if key in seen:
                continue
            seen.add(key)
            sources.append(source)
        return sources

    def citable_sources_detailed(self, ranked: List[Dict],
                                 selected: List[Dict]) -> List[Dict]:
        """Citations as structure, not display strings.

        `used_pages` is a list of bare page labels and cannot express "page 3
        of the manual" — the thing multi-document chat exists to say. Rather
        than overload it and break every existing reader, this returns the
        parts alongside the rendered label, so the UI, the prose and the eval
        all describe a source the same way instead of three near-agreeing ways.
        """
        if not ranked:
            ranked = selected

        seen = set()
        out: List[Dict] = []
        for chunk in ranked:
            source = chunk.get("page_or_sheet")
            if source in (None, ""):
                continue
            key = self.source_key(chunk)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "document": chunk.get("document"),
                "source": source,
                "label": self.source_label(chunk),
            })
        return out

    @staticmethod
    def is_multi_doc(chunks: List[Dict]) -> bool:
        """True when these chunks come from more than one document.

        Keyed on the presence of a `document` field rather than on a flag
        passed in by the caller, so a single-document call cannot accidentally
        take the multi-document path: single-document chunks are built as
        {"text", "page_or_sheet"} and have no `document` at all.
        """
        return any(c.get("document") for c in chunks)

    @staticmethod
    def source_key(chunk: Dict) -> Any:
        """The identity a slot is deduplicated on.

        Within one document a page is a source. Across a corpus it is not:
        page 3 of the operations review and page 3 of the staff manual are
        different sources that the page label alone conflates. That is not
        theoretical — on the cross-document eval it made the manual's page 3
        unreachable AT ANY RANK, discarded as a duplicate while scoring third
        highest in the corpus, because the review's page 3 had already claimed
        the label "3".
        """
        source = chunk.get("page_or_sheet")
        document = chunk.get("document")
        return (document, source) if document else source

    @staticmethod
    def anchor_chunks(chunks: List[Dict]) -> List[Dict]:
        """The first and last chunk, which `_select_chunks` always appends.

        Structural padding, not a retrieval decision — they are added whatever
        the question is, to give the model the document's opening and closing
        context. Defined once here because two callers need the same answer:
        the selector, which adds them, and `execute()`, which must keep them
        out of the citation list.

        NONE across a corpus. The concept is document-shaped: "the opening"
        means something for one document and nothing for twenty, where it
        resolves to page 1 of whichever happens to be first in the list. Those
        two slots were measured spending 14% of the context budget on every
        cross-document query, on documents nobody asked about.
        """
        if not chunks or DocumentChatSkill.is_multi_doc(chunks):
            return []
        if len(chunks) == 1:
            return [chunks[0]]
        return [chunks[0], chunks[-1]]

    @staticmethod
    def source_label(chunk: Dict) -> str:
        """Human-readable origin: 'Page 3', or 'report.pdf, Page 3' in a corpus.

        The document is prefixed only when there is one, so a single-document
        conversation produces exactly the labels it always has and its answers
        are unchanged.

        A bare "Page 3" across a corpus is not a weaker citation, it is a wrong
        one: on the cross-document eval, 11 of 13 citation lists contained a
        page label that named more than one file, and pages 1 and 2 each named
        four. The reader cannot tell which, and has no way to know they cannot.
        """
        source = chunk.get("page_or_sheet")
        document = chunk.get("document")

        if source is None or source == "":
            where = "Unknown source"
        elif isinstance(source, int):
            where = f"Page {source}"
        else:
            where = f"Sheet: {source}"

        return f"{document}, {where}" if document else where

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
        """The chunks handed to the model.

        Top 3 by relevance plus first/last anchors within one document; top 6
        and no anchors across a corpus.

        Thin wrapper over `_select_with_roles`, kept because the retrieval eval
        and older callers want just the list.
        """
        return self._select_with_roles(query, chunks)[0]

    def _select_with_roles(self, query: str, chunks: List[Dict]) -> tuple:
        """(selected, ranked) — everything sent to the model, and the subset of
        it that EARNED its place.

        The two differ by the anchors, and the difference has to be reported
        rather than re-derived. A chunk can be both the top-ranked result and
        positionally first in the document; re-deriving anchors by position
        would drop it from the citation list even though the answer came from
        it. That was a real bug, caught by checking the narrowed citations
        against the model's own prose on `dn-01`, where page 1 is both the best
        match and the opening chunk.

        Ranking is by embedding similarity when available and by keyword overlap
        otherwise. The top-3 count, the first/last anchors and the context cap
        are all unchanged from the keyword-only version, so switching methods
        changes *what* is chosen but not *how many* slots there are.
        """
        if not chunks:
            return [], []

        scores, method = self.score_chunks(query, chunks)
        self._last_retrieval_method = method

        if not any(scores):
            # Nothing matched at all: keep the previous behaviour of handing
            # back the opening chunks rather than an arbitrary ranking.
            self.logger.info(
                f"Chunk retrieval via {method}: no chunk scored above zero; "
                f"falling back to the first {min(3, len(chunks))} chunk(s)."
            )
            head = chunks[:3]
            return head, head

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
        slots = _MULTI_DOC_SLOTS if self.is_multi_doc(chunks) else _SINGLE_DOC_SLOTS
        top = []
        seen_sources = set()
        for i in order:
            # (document, page) across a corpus, bare page within one document.
            # Keying on the page label alone made a passage unreachable at any
            # rank whenever another document had already used that number.
            source = self.source_key(chunks[i])
            if source in seen_sources:
                continue
            seen_sources.add(source)
            top.append(chunks[i])
            if len(top) == slots:
                break

        self.logger.info(
            f"Chunk retrieval via {method}: selected "
            f"{[self.source_label(c) for c in top]} "
            f"from {len(chunks)} chunk(s) across "
            f"{len(set(self.source_key(c) for c in chunks))} source(s) "
            f"in {len(set(c.get('document') for c in chunks))} document(s) "
            f"(top score {scores[order[0]]:.3f})"
        )

        # Always include structural anchors (first and last chunk)
        anchors = [a for a in self.anchor_chunks(chunks) if a not in top]

        # Merge and cap at _MAX_CONTEXT_CHARS
        result: List[Dict] = []
        total_chars = 0
        for chunk in top + anchors:
            text_len = len(chunk.get("text", ""))
            if total_chars + text_len > _MAX_CONTEXT_CHARS:
                break
            result.append(chunk)
            total_chars += text_len

        if not result:
            fallback = chunks[:1]
            return fallback, fallback
        # `ranked` is intersected with `result` because the context cap can drop
        # a ranked chunk; a chunk that never reached the model must not be cited.
        return result, [c for c in result if any(c is t for t in top)]

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

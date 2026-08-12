"""Split page/sheet chunks into smaller overlapping passages, for retrieval only.

WHAT THIS IS FOR

Parsing produces one chunk per PDF page or Excel sheet. That is the right unit
for summarisation, which wants whole coherent sections, and it stays the unit
there — `ParsedDocument.chunks` is untouched by anything in this module.

It is not obviously the right unit for retrieval. A page can hold several
unrelated facts, and embedding the whole page averages the answering sentence
together with everything else on it. Splitting into passages gives the ranker a
tighter target.

There is also a hard mechanical reason, which is the stronger one. The
embedding model (`all-MiniLM-L6-v2`) has `max_seq_length = 256` tokens. Any
chunk longer than that is **silently truncated** before it is embedded — no
warning, no error, the tail simply does not exist as far as retrieval is
concerned. Measured on the dense eval fixture: 5 of its 8 pages exceed the
limit (max 282 tokens), so page-level embedding was reading a lossy version of
most of that document. Passages sized well under the limit remove that entirely.

CITATIONS

Every passage carries the `page_or_sheet` of the chunk it came from, unchanged.
A citation therefore still resolves to "Page 3" exactly as before — the split is
invisible to anything downstream of ranking.

PARAMETERS

Sizes are in WORDS, not tokens, deliberately: tokenising here would make
chunking depend on the embedding model being installed, and the keyword
fallback path has to work without it. The observed ratio on the eval fixtures is
about 1.16 tokens per word, so the 256-token ceiling is roughly 220 words; the
default of 100 words sits comfortably inside it with room for longer words.

Both are overridable per-run for sweeps:

    DOCAGENT_PASSAGE_WORDS=60 DOCAGENT_PASSAGE_OVERLAP=15 python ...
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

# Chosen by measurement, not taste — see docs/retrieval-sub-chunking.md for the
# sweep. 100 words is ~116 tokens, comfortably inside the model's 256-token
# window even for word-heavy text. 20 words of overlap is one or two sentences,
# enough that a fact spanning a passage boundary appears whole in one of them.
DEFAULT_PASSAGE_WORDS = 100
DEFAULT_PASSAGE_OVERLAP = 20

# Below this, splitting is pointless: a chunk already shorter than a passage is
# its own passage. Keeps the 46-word pages of the large report fixture, and
# small spreadsheet sheets, exactly as they were.
_MIN_SPLIT_WORDS = 40


def passage_settings() -> tuple:
    """(words, overlap), honouring the environment overrides."""
    def _int(name: str, default: int) -> int:
        raw = os.environ.get(name, "").strip()
        try:
            value = int(raw) if raw else default
        except ValueError:
            return default
        return value if value > 0 else default

    words = _int("DOCAGENT_PASSAGE_WORDS", DEFAULT_PASSAGE_WORDS)
    overlap = _int("DOCAGENT_PASSAGE_OVERLAP", DEFAULT_PASSAGE_OVERLAP)
    # An overlap at or above the window would never advance the cursor.
    if overlap >= words:
        overlap = max(0, words // 5)
    return words, overlap


def scheme_name(words: Optional[int] = None, overlap: Optional[int] = None) -> str:
    """Stable identifier recorded next to stored vectors, e.g. 'passage:100/20'.

    Persisted embeddings are only comparable to vectors built under the same
    chunking, in exactly the way they are only comparable within one model. This
    string is what lets DocumentStore notice a mismatch instead of silently
    ranking against vectors that describe different text.
    """
    if words is None or overlap is None:
        words, overlap = passage_settings()
    return f"passage:{words}/{overlap}"


def sub_chunk(chunks: List[Dict[str, Any]],
              words: Optional[int] = None,
              overlap: Optional[int] = None) -> List[Dict[str, Any]]:
    """Split retrieval chunks into overlapping passages.

    Input and output are the dicts the chat skill consumes: at minimum
    ``{"text": str, "page_or_sheet": int | str}``. `page_or_sheet` is copied
    through untouched so citations still resolve to the original page or sheet.

    A chunk shorter than `_MIN_SPLIT_WORDS` passes through unchanged, so short
    pages and small sheets are not shredded into fragments that carry no
    meaning on their own.
    """
    if not chunks:
        return []

    if words is None or overlap is None:
        words, overlap = passage_settings()

    # Clamp here as well as in passage_settings(), because callers can pass
    # these directly. An overlap at or above the window leaves step == 1, which
    # emits one passage per word — 251 of them for a 300-word page, each nearly
    # identical to the last. Found by test, not by inspection.
    if overlap >= words:
        overlap = max(0, words // 5)

    step = max(1, words - overlap)
    out: List[Dict[str, Any]] = []

    for chunk in chunks:
        text = (chunk.get("text") or "").strip()
        source = chunk.get("page_or_sheet")

        if not text:
            # Preserved rather than dropped: the chat tab detects an all-empty
            # chunk list to tell "history row with no stored text" apart from
            # "document genuinely says nothing", and dropping empties here
            # would break that signal.
            out.append({"text": chunk.get("text", ""), "page_or_sheet": source,
                        "passage_index": 0})
            continue

        tokens = text.split()
        if len(tokens) <= max(words, _MIN_SPLIT_WORDS):
            out.append({"text": text, "page_or_sheet": source, "passage_index": 0})
            continue

        index = 0
        for start in range(0, len(tokens), step):
            piece = tokens[start:start + words]
            if not piece:
                break
            out.append({
                "text": " ".join(piece),
                "page_or_sheet": source,
                "passage_index": index,
            })
            index += 1
            if start + words >= len(tokens):
                break   # the window already covered the tail

    return out

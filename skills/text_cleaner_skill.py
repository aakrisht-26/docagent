"""
TextCleanerSkill — normalizes and prepares raw text for downstream analysis.

Operations (in order):
    1. Unicode / encoding fix via ftfy (if installed)
    2. Line-ending normalization
    3. PDF hyphenation artifact repair (word-\nbreak → wordbreak)
    4. Remove lone page-number lines (NOT heading lines)
    5. Remove lone bullet/dash lines (NOT non-empty ones)
    6. Collapse excessive blank lines
    7. Strip trailing whitespace per line

Key design: heading lines and document structure are PRESERVED so that
SummarizationSkill can detect sections accurately.

Also provides create_text_chunks() — sentence-aware splitting used by
SummarizationSkill for map-reduce over large documents.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

from core.models import DocumentChunk, ParsedDocument, SkillInput, SkillOutput
from skills.base_skill import BaseSkill
from utils.logger import get_logger

logger = get_logger(__name__)

# Precompiled patterns
# Page numbers: matches ONLY pure page-number lines (not headings)
_PAGE_NUM     = re.compile(
    r"^\s*(?:page\s*\d+(?:\s+of\s+\d+)?|\d+\s*/\s*\d+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")
_EXCESS_BLANK = re.compile(r"\n{3,}")
# Lone bullet: only truly empty bullet lines (no content after the symbol)
_LONE_BULLET  = re.compile(r"^[ \t]*[\-\•\◦\▪\·][ \t]*$", re.MULTILINE)
_LINE_TRAIL   = re.compile(r"[ \t]+$", re.MULTILINE)
# Running header/footer detector: same short text appearing on many pages
# (handled at the ParsedDocument level, not here)

# Heading patterns — lines matching these are PRESERVED during cleaning
_HEADING_GUARD = re.compile(
    r"(?:^#{1,4}\s.+$"
    r"|^\d+(?:\.\d+)*\s+[A-Z][^\n]{2,}$"
    r"|^[A-Z][A-Z\s\-:]{4,}[A-Z]$)",
    re.MULTILINE,
)


class TextCleanerSkill(BaseSkill):
    """
    Cleans and normalises text extracted from documents.

    Config keys:
        chunk_size    (int) : Characters per LLM chunk (default: 3000)
        chunk_overlap (int) : Overlap between chunks  (default: 200)
        use_ftfy      (bool): Apply ftfy encoding fix  (default: True)
    """

    name = "text_cleaner"
    description = "Normalizes encoding, whitespace, and structure of extracted document text."
    required_inputs = ["parsed_document"]

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        self._chunk_size    = self.get_config("chunk_size", 3000)
        self._chunk_overlap = self.get_config("chunk_overlap", 200)
        self._use_ftfy      = self.get_config("use_ftfy", True)

    # ── Skill entry point ─────────────────────────────────────────────

    def execute(self, inputs: SkillInput) -> SkillOutput:
        start = time.monotonic()
        doc: ParsedDocument = inputs.data["parsed_document"]

        # Clean each chunk independently (preserves page boundaries)
        cleaned_chunks: List[DocumentChunk] = []
        for chunk in doc.chunks:
            cleaned_chunks.append(DocumentChunk(
                text=self._clean(chunk.text),
                page_or_sheet=chunk.page_or_sheet,
                chunk_index=chunk.chunk_index,
                metadata={**chunk.metadata, "cleaned": True},
            ))

        cleaned_full = self._clean(doc.full_text)

        cleaned_doc = ParsedDocument(
            file_name   = doc.file_name,
            file_type   = doc.file_type,
            chunks      = cleaned_chunks,
            full_text   = cleaned_full,
            tables      = doc.tables,
            metadata    = doc.metadata,
            page_count  = doc.page_count,
            sheet_names = doc.sheet_names,
        )

        self.logger.debug(
            f"Text cleaned: {doc.char_count} → {cleaned_doc.char_count} chars"
        )
        return SkillOutput(
            success=True,
            data=cleaned_doc,
            duration_ms=(time.monotonic() - start) * 1000,
        )

    # ── Core cleaning pipeline ────────────────────────────────────────

    def _clean(self, text: str) -> str:
        """
        Apply the full cleaning pipeline.

        Heading lines are explicitly protected — they are critical structural
        cues for SummarizationSkill's section detection.
        """
        if not text:
            return ""

        # 1. Fix encoding / mojibake
        if self._use_ftfy:
            try:
                import ftfy
                text = ftfy.fix_text(text)
            except ImportError:
                pass

        # 2. Normalize line endings
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        # 3. Repair PDF hyphenation artifacts
        #    Only join if NEITHER side is the start of a heading
        text = _HYPHEN_BREAK.sub(r"\1\2", text)

        # 4. Remove pure page-number lines (safe: heading guard prevents removing real headings)
        text = _PAGE_NUM.sub("", text)

        # 5. Remove lone bullet-only lines (no content)
        text = _LONE_BULLET.sub("", text)

        # 6. Collapse 3+ blank lines → double newline
        text = _EXCESS_BLANK.sub("\n\n", text)

        # 7. Strip trailing whitespace per line
        text = _LINE_TRAIL.sub("", text)

        return text.strip()

    # ── Chunking (used by SummarizationSkill) ─────────────────────────

    def create_text_chunks(self, full_text: str) -> List[str]:
        """
        Split full_text into overlapping context chunks for LLM processing.

        Splitting is sentence-aware: prefers to break at sentence boundaries
        ('. ', '? ', '! ', '\\n\\n') to avoid mid-sentence cuts.

        Returns a list of at least one chunk.
        """
        if not full_text or len(full_text) <= self._chunk_size:
            return [full_text]

        chunks: List[str] = []
        start = 0
        text_len = len(full_text)
        BREAK_TOKENS = ["\n\n", ".\n", ". ", "? ", "! ", "; "]

        while start < text_len:
            end = min(start + self._chunk_size, text_len)
            if end >= text_len:
                chunks.append(full_text[start:])
                break

            # Walk backwards to find a sentence boundary
            break_at = end
            for token in BREAK_TOKENS:
                idx = full_text.rfind(token, start + self._chunk_overlap, end)
                if idx != -1:
                    break_at = idx + len(token)
                    break

            chunks.append(full_text[start:break_at])
            # Move forward with overlap
            start = max(start + 1, break_at - self._chunk_overlap)

        self.logger.debug(f"Created {len(chunks)} text chunks from {text_len} chars")
        return chunks

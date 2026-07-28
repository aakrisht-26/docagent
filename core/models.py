"""
Core data models for DocAgent.

These dataclasses represent the intermediate and final data structures
that flow between skills in the processing pipeline.

Design principle: Skills communicate ONLY through these typed objects.
No skill should accept or return raw dicts in its public API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union


# ── Document Representation ───────────────────────────────────────────────────

@dataclass
class DocumentChunk:
    """
    A single chunk of text from a parsed document.

    Chunks correspond to:
    - A single page for PDF documents
    - A single sheet for Excel documents
    - An arbitrary LLM-context-sized slice for large text bodies
    """

    text: str
    page_or_sheet: Union[int, str]   # page number (int) or sheet name (str)
    chunk_index: int                 # 0-based position in the document
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.text)

    def __bool__(self) -> bool:
        return bool(self.text.strip())


@dataclass
class ParsedDocument:
    """
    Structured representation of a fully parsed document.

    This is the primary output of PDFReaderSkill and ExcelReaderSkill,
    and the primary input to TextCleanerSkill, ClassifierSkill, etc.
    """

    file_name: str
    file_type: str                          # "pdf" | "excel"
    chunks: List[DocumentChunk]
    full_text: str
    tables: List[Dict[str, Any]]            # Extracted tables with text repr
    metadata: Dict[str, Any]
    page_count: int = 0
    sheet_names: List[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """True if no usable text was extracted."""
        return not self.full_text.strip()

    @property
    def word_count(self) -> int:
        return len(self.full_text.split())

    @property
    def char_count(self) -> int:
        return len(self.full_text)


# ── Skill I/O Envelopes ───────────────────────────────────────────────────────

@dataclass
class SkillInput:
    """
    Standardised input envelope passed to every skill's execute() method.

    `data` is a typed dict; each skill documents its required keys
    in `required_inputs`.
    `metadata` carries request-level context (request ID, caller, etc.).
    """

    data: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillOutput:
    """
    Standardised output envelope returned by every skill's execute() method.

    `data` holds the skill's primary payload (type varies per skill).
    `error` is non-None only on failure.
    `warnings` carry non-fatal issues (e.g. incomplete OCR, empty sheet).
    `duration_ms` enables pipeline performance profiling.
    """

    success: bool
    data: Any
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0


# ── Intermediate Results ──────────────────────────────────────────────────────

@dataclass
class ClassificationResult:
    """
    Output of DocumentClassifierSkill.

    `doc_type`   : "questionnaire" | "normal_document"
    `domain`     : Detected document domain/industry (e.g. "Financial")
    `confidence` : 0.0 – 1.0
    `method`     : "heuristic" | "llm" | "hybrid"
    `signals`    : diagnostic dict of matched patterns / scores
    """

    doc_type: str
    domain: str = "General"
    confidence: float = 0.0
    method: str = "unknown"
    signals: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExtractionResult:
    """
    Generic result for summarization or question extraction.

    Used internally; the agent unwraps this into PipelineResult.
    """

    content: str = ""
    questions: List[str] = field(default_factory=list)
    method: str = "unknown"
    metadata: Dict[str, Any] = field(default_factory=dict)

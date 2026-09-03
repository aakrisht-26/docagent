"""
PipelineResult — the single typed output contract for a DocumentAgent run.

This object is:
- Returned by DocumentAgent.run()
- Consumed by the Streamlit UI for display
- Consumed by tests for assertions
- Serializable to Markdown and PDF for download

Design: All fields are explicitly typed; no raw dicts in the public API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class PipelineResult:
    """
    Complete output of a document analysis pipeline run.

    Immutable once created by DocumentAgent. The UI renders this directly
    without any post-processing.
    """

    # ── Identity ─────────────────────────────────────────────────────────
    file_name: str
    file_type: str                      # "pdf" | "excel"

    # ── Classification ────────────────────────────────────────────────────
    doc_type: str                       # "questionnaire" | "normal_document"
    domain: str                         # e.g., "Financial", "Environmental", "General"
    classification_confidence: float    # 0.0 – 1.0
    classification_method: str          # "heuristic" | "llm" | "hybrid"

    # ── Analysis Outputs ─────────────────────────────────────────────────
    summary: str
    summary_method: str                 # "llm_single_<provider>" | "llm_map_reduce_<provider>" | "extractive" | "skipped" | "none"
    questions: List[str]
    question_extraction_method: str     # "regex" | "llm" | "hybrid" | "skipped" | "none"

    # ── Document Stats ────────────────────────────────────────────────────
    raw_text: str
    word_count: int
    page_count: int

    # ── Metadata ──────────────────────────────────────────────────────────
    metadata: Dict[str, Any]

    # ── Extracted Tables ──────────────────────────────────────────────────
    tables: List[Dict[str, Any]] = field(default_factory=list)

    # ── Structured entities (T1-4): KV pairs, dates, parties, etc. ───────
    extracted_entities: Dict[str, Any] = field(default_factory=dict)

    # ── Citation index (Feature 5) ──────────────────────────────────────────
    summary_citations: List[Dict[str, Any]] = field(default_factory=list)
    # schema: [{"paragraph_index": int, "pages": List[int|str], "section": str}]

    # ── Parsed document reference (for Chat / Edit features) ────────────────
    parsed_document: Optional[Any] = None


    # ── Diagnostics ───────────────────────────────────────────────────────
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    processing_time_ms: float = 0.0
    skill_timings: Dict[str, float] = field(default_factory=dict)
    success: bool = True
    # partial=True means some pipeline steps failed but others succeeded.
    # The result contains whatever data was successfully produced.
    partial: bool = False

    # ── Serialization ─────────────────────────────────────────────────────

    def to_markdown(self) -> str:
        """
        Serialize this result to a clean, human-readable Markdown document.
        Used for the Markdown download button in the UI.
        """
        doc_label = self.doc_type.replace("_", " ").title()
        # The raw field is P(questionnaire); a confidently-classified normal
        # document scores near zero and this line read "2% confidence" for the
        # classifier's best work. The UI was corrected for this; the downloadable
        # report was missed.
        from core.models import confidence_in_verdict
        _conf = confidence_in_verdict(self.classification_confidence, self.doc_type)
        conf_pct = "not classified" if _conf is None else f"{_conf:.0%} confidence"

        lines: List[str] = [
            "# DocAgent — Analysis Report",
            "",
            f"> **File:** `{self.file_name}`  ",
            f"> **Format:** {self.file_type.upper()}  |  **Document Type:** {doc_label}  |  **Domain:** {self.domain}  ",
            f"> **Classification:** {conf_pct} via *{self.classification_method}*  ",
            f"> **Words:** {self.word_count:,}  |  **Pages / Sheets:** {self.page_count}  ",
            f"> **Processing time:** {self.processing_time_ms / 1000:.2f}s",
            "",
            "---",
            "",
            "## 📋 Summary",
            "",
            self.summary if self.summary else "_No summary could be generated._",
            "",
        ]

        # Key fields sit ABOVE the questions and BELOW the summary, mirroring
        # the results view. The extraction was previously absent from this
        # export entirely -- it reached a reader only through the JSON download,
        # which is not a document anyone reads.
        if self.extracted_entities:
            lines += ["---", "", "## Key fields", "",
                      "| Field | Value |", "|---|---|"]
            for key, value in self.extracted_entities.items():
                if isinstance(value, (list, tuple)):
                    shown = "; ".join(str(v) for v in value if str(v).strip())
                elif isinstance(value, dict):
                    shown = "; ".join(f"{k}: {v}" for k, v in value.items())
                else:
                    shown = str(value)
                shown = shown.strip().replace("|", "\\|")
                shown = " ".join(shown.split())
                if shown:
                    label = key.replace("_", " ").strip().capitalize()
                    lines.append(f"| {label} | {shown} |")
            lines.append("")

        if self.questions:
            lines += [
                "---",
                "",
                f"## ❓ Extracted Questions  _{len(self.questions)} found_",
                "",
            ]
            for i, q in enumerate(self.questions, 1):
                lines.append(f"{i}. {q}")
            lines.append("")

        # Metadata table
        clean_meta = {k: v for k, v in self.metadata.items() if v and k not in ("engine",)}
        if clean_meta:
            lines += [
                "---",
                "",
                "## ℹ️ Document Metadata",
                "",
                "| Key | Value |",
                "|-----|-------|",
            ]
            for k, v in list(clean_meta.items())[:20]:
                lines.append(f"| {k} | {str(v)[:120]} |")
            lines.append("")

        # Skill timing breakdown
        if self.skill_timings:
            lines += [
                "---",
                "",
                "## ⏱ Skill Timing Breakdown",
                "",
                "| Skill | Duration |",
                "|-------|---------|",
            ]
            for skill, ms in self.skill_timings.items():
                lines.append(f"| {skill} | {ms:.0f} ms |")
            lines.append("")

        if self.errors:
            lines += [
                "---",
                "",
                "> ⚠️ **Errors during processing:**",
            ]
            for err in self.errors:
                lines.append(f"> - {err}")
            lines.append("")

        lines += [
            "---",
            "",
            f"_Generated by DocAgent v1.0.0 · {self.processing_time_ms / 1000:.2f}s total_",
        ]

        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to plain dict (for JSON export or testing)."""
        return {
            "file_name": self.file_name,
            "file_type": self.file_type,
            "doc_type": self.doc_type,
            "domain": self.domain,
            "classification_confidence": self.classification_confidence,
            "classification_method": self.classification_method,
            "summary": self.summary,
            "summary_method": self.summary_method,
            "questions": self.questions,
            "question_extraction_method": self.question_extraction_method,
            "word_count": self.word_count,
            "page_count": self.page_count,
            "metadata": self.metadata,
            "extracted_entities": self.extracted_entities,
            "errors": self.errors,
            "warnings": self.warnings,
            "processing_time_ms": self.processing_time_ms,
            "skill_timings": self.skill_timings,
            "success": self.success,
            "partial": self.partial,
        }

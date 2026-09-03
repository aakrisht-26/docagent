"""
DocumentAgent — the main orchestrator for the DocAgent system.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from agents.base_agent import BaseAgent
from agents.planner import PipelinePlanner, DocStats
from core.models import ParsedDocument, SkillInput
from core.pipeline_result import PipelineResult
from core.skill_registry import SkillRegistry
from utils.logger import get_logger

logger = get_logger(__name__)

SUPPORTED_EXTENSIONS: Dict[str, str] = {
    ".pdf":  "pdf",
    ".xlsx": "excel",
    ".xls":  "excel",
    ".csv":  "excel",
    ".mp3":  "audio",
    ".m4a":  "audio",
    ".wav":  "audio",
    ".flac": "audio",
    ".ogg":  "audio",
    ".webm": "audio",
}


class DocumentAgent(BaseAgent):
    """
    Main document analysis orchestrator.
    """

    name = "document_agent"
    description = "Orchestrates the full document analysis pipeline."

    # Canonical stage numbers from the frozen pipeline (see CLAUDE.md).
    # Used only for logging; changing these does not change execution order.
    stage_numbers = {
        "parse":                 "1",
        "clean":                 "2",
        "classify":              "3",
        "structure_recognition": "3.5",
        "summarize":             "4",
        "extract_questions":     "5",
        "structured_extraction": "5.5",
    }
    total_stages = 6

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        from utils.config import resolve_groq_api_keys
        gro  = self.config.get("groq", {})
        pdf  = self.config.get("pdf", {})
        xls  = self.config.get("excel", {})
        summ = self.config.get("summarization", {})
        cls_ = self.config.get("classification", {})
        q_   = self.config.get("question_extraction", {})

        # Groq config block forwarded to skills that need LLM access.
        # api_keys is resolved through the canonical resolve_groq_api_keys()
        # so all three code paths (env var, plural env var, config file) work.
        resolved_keys = resolve_groq_api_keys(gro)
        groq_skill_cfg: Dict[str, Any] = {
            "groq": {
                "enabled":         gro.get("enabled", True),
                "api_keys":        ",".join(resolved_keys),
                "api_key":         resolved_keys[0] if resolved_keys else "",
                "base_url":        gro.get("base_url", "https://api.groq.com/openai/v1"),
                "model":           gro.get("model", "openai/gpt-oss-120b"),
                "timeout_seconds": gro.get("timeout_seconds", 180),
                "temperature":     gro.get("temperature", 0.15),
            }
        }

        self._registry = SkillRegistry()
        self._registry.discover()
        self._planner  = PipelinePlanner()

        def _skill(name: str, cfg: dict):
            s = self._registry.instantiate(name, config=cfg)
            if s is None:
                raise RuntimeError(
                    f"DocumentAgent: required skill '{name}' not found in registry. "
                    f"Available: {self._registry.list_skills()}"
                )
            return s

        self._pdf_reader   = _skill("pdf_reader",             pdf)
        self._xls_reader   = _skill("excel_reader",           xls)
        self._audio_reader = _skill("audio_reader",           {**self.config.get("audio", {}), **groq_skill_cfg})
        self._cleaner      = _skill("text_cleaner",           summ)
        self._classifier   = _skill("document_classifier",    {**cls_, **groq_skill_cfg})
        self._struct_rec   = _skill("structure_recognition",  pdf)
        self._summarizer   = _skill("summarization",          {**summ, **groq_skill_cfg})
        self._q_extractor  = _skill("question_extraction",    {**q_, **groq_skill_cfg})
        # structured_extraction is optional — only instantiated if registered
        self._struct_ext   = self._registry.instantiate(
            "structured_extraction",
            config={**self.config.get("structured_extraction", {}), **groq_skill_cfg},
        )

    # ── Main pipeline ─────────────────────────────────────────────────

    def run_youtube(self, youtube_url: str) -> PipelineResult:
        """
        Specialized pipeline for YouTube URLs.
        Downloads audio via AudioReaderSkill then delegates to _run_core_pipeline.
        """
        import re
        video_id_match = re.search(r"(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})", youtube_url)
        video_id = video_id_match.group(1) if video_id_match else "unknown"
        synthetic_path = Path(f"youtube_{video_id}.audio")

        pipeline_start = time.monotonic()
        skill_timings: Dict[str, float] = {}

        self._begin_pipeline(f"YouTube {video_id}")

        # ── Step 1: Parse (YouTube) ───────────────────────────────────
        parse_out = self._audio_reader.safe_execute(SkillInput(data={"youtube_url": youtube_url}))
        skill_timings["parse"] = parse_out.duration_ms
        self._log_step("parse", parse_out.success, parse_out.duration_ms, parse_out.error)

        if not parse_out.success:
            # Carry the skill's reason through. This is the second place the
            # cause of a failed download was discarded: the skill had just
            # replaced YouTube's prose with a fixed string, and this line then
            # replaced THAT with another one, so a bot-check, a deleted video
            # and a genuine bug all reached the user as the same sentence.
            # Additive only — the original message is still the prefix, and no
            # step, order or handoff changes.
            detail = parse_out.error or "no further detail"
            return self._error_result(
                synthetic_path, f"YouTube audio extraction failed: {detail}")

        parsed_doc: ParsedDocument = parse_out.data
        if parsed_doc.is_empty:
            return self._error_result(synthetic_path, "Extracted audio produced no transcript")

        return self._run_core_pipeline(
            parsed_doc=parsed_doc,
            file_name=synthetic_path.name,
            file_type="audio",
            file_path=synthetic_path,
            pipeline_start=pipeline_start,
            skill_timings=skill_timings,
            initial_warnings=list(parse_out.warnings),
        )

    def run(self, file_path: Path) -> PipelineResult:
        pipeline_start = time.monotonic()
        file_path = Path(file_path)
        skill_timings: Dict[str, float] = {}

        self._begin_pipeline(file_path.name)

        # ── Step 0: Validate ──────────────────────────────────────────
        ext = file_path.suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            return self._error_result(
                file_path,
                f"Unsupported file type '{ext}'. Supported: {', '.join(SUPPORTED_EXTENSIONS)}"
            )
        file_type = SUPPORTED_EXTENSIONS[ext]

        # ── Step 1: Parse ─────────────────────────────────────────────
        reader = (
            self._pdf_reader if file_type == "pdf"
            else self._audio_reader if file_type == "audio"
            else self._xls_reader
        )
        parse_out = reader.safe_execute(SkillInput(data={"file_path": str(file_path)}))
        skill_timings["parse"] = parse_out.duration_ms
        self._log_step("parse", parse_out.success, parse_out.duration_ms, parse_out.error)

        if not parse_out.success:
            return self._error_result(file_path, "Parsing failed")

        parsed_doc: ParsedDocument = parse_out.data
        if parsed_doc.is_empty:
            return self._error_result(file_path, "Document is empty — no text could be extracted")

        return self._run_core_pipeline(
            parsed_doc=parsed_doc,
            file_name=file_path.name,
            file_type=file_type,
            file_path=file_path,
            pipeline_start=pipeline_start,
            skill_timings=skill_timings,
            initial_warnings=list(parse_out.warnings),
        )

    def _run_core_pipeline(
        self,
        parsed_doc: ParsedDocument,
        file_name: str,
        file_type: str,
        file_path: Path,
        pipeline_start: float,
        skill_timings: Dict[str, float],
        initial_warnings: Optional[List[str]] = None,
    ) -> PipelineResult:
        """
        Shared steps 2-6 of the pipeline: Clean → Classify → Structure → Summarize → Extract.
        Called by both run() (file-based) and run_youtube() after their respective parse steps.
        """
        errors:   List[str] = []
        warnings: List[str] = list(initial_warnings or [])
        partial:  bool      = False

        # ── Step 2: Clean ─────────────────────────────────────────────
        clean_out = self._cleaner.safe_execute(SkillInput(data={"parsed_document": parsed_doc}))
        skill_timings["clean"] = clean_out.duration_ms
        self._log_step("clean", clean_out.success, clean_out.duration_ms, clean_out.error)

        if clean_out.success and clean_out.data:
            parsed_doc = clean_out.data
        elif clean_out.error:
            warnings.append(f"Text cleaning failed ({clean_out.error}); using raw parsed text.")
            partial = True
        full_text = parsed_doc.full_text

        # ── Step 3: Classify ──────────────────────────────────────────
        classify_out = self._classifier.safe_execute(SkillInput(data={"full_text": full_text}))
        skill_timings["classify"] = classify_out.duration_ms
        self._log_step("classify", classify_out.success, classify_out.duration_ms, classify_out.error)

        class_result = classify_out.data if classify_out.success else None
        doc_type     = class_result.doc_type if class_result else "normal_document"
        domain       = class_result.domain if class_result else "General"
        class_conf   = class_result.confidence if class_result else 0.0
        class_method = class_result.method if class_result else "fallback"
        if not classify_out.success:
            warnings.append(f"Classification failed ({classify_out.error}); using defaults.")
            partial = True

        # ── Planner: decide remaining steps ───────────────────────────
        stats = DocStats(
            file_type=file_type,
            word_count=parsed_doc.word_count,
            page_count=parsed_doc.page_count,
            domain=domain,
            doc_type=doc_type,
            has_tables=bool(parsed_doc.tables),
        )
        plan = self._planner.plan(stats, registered_skills=self._registry.list_skills())

        # ── Step 3.5: Structure Recognition (Tables) ──────────────────
        if "structure_recognition" in plan:
            struct_out = self._struct_rec.safe_execute(SkillInput(data={
                "parsed_document": parsed_doc,
                "file_path": str(file_path),
                "domain": domain,
            }))
            skill_timings["structure_recognition"] = struct_out.duration_ms
            self._log_step("structure_recognition", struct_out.success, struct_out.duration_ms, struct_out.error)

            if struct_out.success and struct_out.data:
                parsed_doc = struct_out.data
                full_text = parsed_doc.full_text
            elif struct_out.error:
                warnings.append(f"Structure recognition failed ({struct_out.error}); tables may be missing.")
                partial = True
        else:
            self._log_stage_skipped("structure_recognition", "not selected by planner")

        # ── Step 4: Summarize ─────────────────────────────────────────
        summary           = ""
        summary_method    = "skipped"
        summary_citations: list = []
        if "summarization" in plan:
            summ_cfg = self.config.get("summarization", {})
            summ_out = self._summarizer.safe_execute(SkillInput(data={
                "full_text":       full_text,
                "doc_type":        doc_type,
                "domain":          domain,
                "parsed_document": parsed_doc,
                "summary_length":  summ_cfg.get("summary_length", "Standard"),
                "summary_tone":    summ_cfg.get("summary_tone", "Professional"),
            }))
            skill_timings["summarize"] = summ_out.duration_ms
            self._log_step("summarize", summ_out.success, summ_out.duration_ms, summ_out.error)

            summary           = summ_out.data.get("summary", "") if (summ_out.success and summ_out.data) else ""
            summary_method    = summ_out.data.get("method", "none") if (summ_out.success and summ_out.data) else "none"
            summary_citations = summ_out.data.get("citations", []) if (summ_out.success and summ_out.data) else []
            warnings.extend(summ_out.warnings)
            if not summ_out.success:
                warnings.append(f"Summarization failed ({summ_out.error}); summary unavailable.")
                partial = True
        else:
            self._log_stage_skipped("summarize", "not selected by planner")

        # ── Step 5: Extract Questions ─────────────────────────────────
        questions: list = []
        q_method = "skipped"
        if "question_extraction" in plan:
            q_out = self._q_extractor.safe_execute(SkillInput(data={
                "full_text": full_text,
                "doc_type":  doc_type,
                "domain":    domain,
            }))
            skill_timings["extract_questions"] = q_out.duration_ms
            self._log_step("extract_questions", q_out.success, q_out.duration_ms, q_out.error)

            questions = q_out.data.get("questions", []) if (q_out.success and q_out.data) else []
            q_method  = q_out.data.get("method", "none") if (q_out.success and q_out.data) else "none"
            warnings.extend(q_out.warnings)
            if not q_out.success:
                warnings.append(f"Question extraction failed ({q_out.error}).")
                partial = True
        else:
            self._log_stage_skipped("extract_questions", "doc_type is not questionnaire")

        # ── Step 5.5: Structured extraction (entities, KV pairs) ──────
        extracted_entities: dict = {}
        if "structured_extraction" in plan and self._struct_ext is not None:
            se_out = self._struct_ext.safe_execute(SkillInput(data={
                "full_text": full_text,
                "doc_type":  doc_type,
                "domain":    domain,
            }))
            skill_timings["structured_extraction"] = se_out.duration_ms
            self._log_step("structured_extraction", se_out.success, se_out.duration_ms, se_out.error)

            if se_out.success and se_out.data:
                extracted_entities = se_out.data.get("entities", {})
            # Carry the skill's warning through, the same way summarisation's
            # is carried above. Without this the reason extraction produced
            # nothing -- truncated budget, or every key rate-limited -- reaches
            # the log and stops there, and the user sees an empty entities
            # block with no explanation. The skill distinguishes those two
            # causes precisely so somebody can act on the difference.
            warnings.extend(se_out.warnings)
        else:
            self._log_stage_skipped("structured_extraction", "not selected by planner")

        # ── Step 6: Assemble ──────────────────────────────────────────
        total_ms = (time.monotonic() - pipeline_start) * 1000
        self._log_pipeline_summary(file_name, skill_timings, total_ms)
        return PipelineResult(
            file_name=file_name,
            file_type=file_type,
            doc_type=doc_type,
            domain=domain,
            classification_confidence=class_conf,
            classification_method=class_method,
            summary=summary,
            summary_method=summary_method,
            questions=questions,
            question_extraction_method=q_method,
            raw_text=full_text,
            word_count=parsed_doc.word_count,
            page_count=parsed_doc.page_count,
            metadata=parsed_doc.metadata,
            tables=parsed_doc.tables,
            summary_citations=summary_citations,
            parsed_document=parsed_doc,
            extracted_entities=extracted_entities,
            errors=errors,
            warnings=warnings,
            processing_time_ms=total_ms,
            skill_timings=skill_timings,
            success=len(errors) == 0,
            partial=partial,
        )

    @staticmethod
    def _error_result(file_path: Path, error_msg: str) -> PipelineResult:
        return PipelineResult(
            file_name=file_path.name,
            file_type="unknown",
            doc_type="unknown",
            domain="General",
            classification_confidence=0.0,
            classification_method="none",
            summary="",
            summary_method="none",
            questions=[],
            question_extraction_method="none",
            raw_text="",
            word_count=0,
            page_count=0,
            metadata={},
            errors=[error_msg],
            success=False,
        )

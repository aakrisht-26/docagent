"""
Integration tests for DocumentAgent and SkillRegistry.

These tests exercise the full pipeline using synthetic text
(no Ollama, no actual PDF/Excel files for agent tests).
File-based tests create real temp files when possible.

Run with:
    pytest tests/test_agent.py -v
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from utils.logger import setup_logging

setup_logging(level="WARNING")


# ── SkillRegistry ─────────────────────────────────────────────────────────────

class TestSkillRegistry:
    def test_discover_finds_all_skills(self):
        from core.skill_registry import SkillRegistry

        registry = SkillRegistry()
        registry.reset()
        registry.discover()

        skills = registry.list_skills()
        expected = [
            "document_classifier",
            "excel_reader",
            "pdf_reader",
            "question_extraction",
            "summarization",
            "text_cleaner",
        ]
        for s in expected:
            assert s in skills, f"Expected skill '{s}' not found. Found: {skills}"

    def test_instantiate_known_skill(self):
        from core.skill_registry import SkillRegistry

        registry = SkillRegistry()
        registry.discover()
        skill = registry.instantiate("text_cleaner", config={"chunk_size": 500})
        assert skill is not None
        assert skill.name == "text_cleaner"

    def test_instantiate_unknown_returns_none(self):
        from core.skill_registry import SkillRegistry

        registry = SkillRegistry()
        result = registry.instantiate("nonexistent_skill")
        assert result is None

    def test_singleton_behavior(self):
        from core.skill_registry import SkillRegistry

        r1 = SkillRegistry()
        r2 = SkillRegistry()
        assert r1 is r2

    def test_manual_register(self):
        from core.skill_registry import SkillRegistry
        from skills.text_cleaner_skill import TextCleanerSkill

        registry = SkillRegistry()
        registry.reset()
        registry.register(TextCleanerSkill)
        assert "text_cleaner" in registry.list_skills()


# ── DocumentAgent (no real files) ─────────────────────────────────────────────

class TestDocumentAgentErrors:
    def _make_agent(self):
        from agents.document_agent import DocumentAgent
        return DocumentAgent(config={
            "groq": {"api_key": ""},
            "pdf": {"use_ocr_fallback": False},
        })

    def test_unsupported_extension(self):
        agent = self._make_agent()
        result = agent.run(Path("document.docx"))
        assert not result.success
        assert "Unsupported" in result.errors[0]

    def test_nonexistent_file(self):
        agent = self._make_agent()
        result = agent.run(Path("/nonexistent/path/to/file.pdf"))
        assert not result.success

    def test_result_has_file_name(self):
        agent = self._make_agent()
        result = agent.run(Path("test_doc.pdf"))
        assert result.file_name == "test_doc.pdf"


# ── DocumentAgent with a real CSV ─────────────────────────────────────────────

class TestDocumentAgentCSV:
    def _make_agent(self):
        from agents.document_agent import DocumentAgent
        return DocumentAgent(config={
            "groq": {"api_key": ""},
            "summarization": {"extractive_sentences": 5},
        })

    def test_csv_full_pipeline(self):
        csv_content = (
            "Name,Department,Salary,YearsExperience\n"
            "Alice Smith,Engineering,95000,5\n"
            "Bob Jones,Marketing,72000,3\n"
            "Carol White,Engineering,105000,8\n"
            "David Brown,HR,68000,2\n"
            "Eve Davis,Engineering,88000,4\n"
        )

        with tempfile.NamedTemporaryFile(suffix=".csv", mode="w",
                                        delete=False, encoding="utf-8") as f:
            f.write(csv_content)
            tmp_path = Path(f.name)

        try:
            agent = self._make_agent()
            result = agent.run(tmp_path)

            assert result.success or len(result.errors) == 0, f"Errors: {result.errors}"
            assert result.file_type == "excel"
            assert result.word_count > 0
            assert result.summary  # extractive should produce something
            assert result.processing_time_ms > 0
            assert "parse" in result.skill_timings
            assert "summarize" in result.skill_timings
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_csv_markdown_export(self):
        csv_content = "Col1,Col2\nA,1\nB,2\n"

        with tempfile.NamedTemporaryFile(suffix=".csv", mode="w",
                                        delete=False, encoding="utf-8") as f:
            f.write(csv_content)
            tmp_path = Path(f.name)

        try:
            agent = self._make_agent()
            result = agent.run(tmp_path)
            md = result.to_markdown()

            assert "# DocAgent" in md
            assert result.file_name in md
            assert "Summary" in md
        finally:
            tmp_path.unlink(missing_ok=True)


# ── PipelineResult ─────────────────────────────────────────────────────────────

class TestPipelineResult:
    def _make_result(self, **kwargs):
        from core.pipeline_result import PipelineResult

        defaults = dict(
            file_name="test.pdf",
            file_type="pdf",
            doc_type="questionnaire",
            domain="General",
            classification_confidence=0.82,
            classification_method="heuristic",
            summary="This is a test summary.",
            summary_method="extractive",
            questions=["What is your name?", "How old are you?"],
            question_extraction_method="regex",
            raw_text="Full raw text here.",
            word_count=100,
            page_count=3,
            metadata={"Author": "Test"},
            processing_time_ms=1234.5,
            skill_timings={"parse": 100, "summarize": 500},
            success=True,
        )
        defaults.update(kwargs)
        return PipelineResult(**defaults)

    def test_to_markdown_contains_sections(self):
        result = self._make_result()
        md = result.to_markdown()

        assert "# DocAgent" in md
        assert "Summary" in md
        assert "Questions" in md
        assert "What is your name?" in md
        assert "How old are you?" in md
        assert "test.pdf" in md

    def test_to_markdown_no_questions_for_normal(self):
        result = self._make_result(doc_type="normal_document", questions=[])
        md = result.to_markdown()
        assert "Questions" not in md

    def test_to_dict_serializable(self):
        import json
        result = self._make_result()
        d = result.to_dict()
        # Should be JSON-serializable
        json.dumps(d)
        assert d["file_name"] == "test.pdf"
        assert d["questions"] == ["What is your name?", "How old are you?"]

    def test_error_result_not_success(self):
        result = self._make_result(success=False, errors=["Something went wrong"])
        assert not result.success
        assert len(result.errors) == 1

    def test_metadata_in_markdown(self):
        result = self._make_result(metadata={"Author": "Jane Doe", "Pages": "10"})
        md = result.to_markdown()
        assert "Metadata" in md
        assert "Jane Doe" in md

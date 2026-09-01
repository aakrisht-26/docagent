"""
Unit tests for all DocAgent skills.

Run with:
    pytest tests/test_skills.py -v

Tests use synthetic text inputs — no real files, no network calls, no Ollama required.
All LLM calls are indirectly tested via the rule-based fallback paths (ollama_enabled=False).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure project root is on the path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.models import DocumentChunk, ParsedDocument, SkillInput
from utils.logger import setup_logging

setup_logging(level="WARNING")  # suppress noise during tests


# ── Fixtures ──────────────────────────────────────────────────────────────────

NORMAL_TEXT = """
Annual Report 2023 — Acme Corporation

Executive Summary
Acme Corporation achieved record revenues of $4.2 billion in fiscal year 2023,
representing a 15% increase over the prior year. The growth was driven by
strong performance in our cloud services division and expansion into Asia-Pacific markets.

Key Financial Highlights
- Total Revenue: $4.2B (+15% YoY)
- Operating Income: $820M (+22% YoY)
- Net Profit Margin: 19.5%

Market Outlook
Management remains cautiously optimistic about fiscal year 2024. Investments in
artificial intelligence and automation are expected to reduce operational costs
by approximately 8% while improving product quality.
""".strip()

QUESTIONNAIRE_TEXT = """
Customer Satisfaction Survey

Thank you for participating in our annual survey. Please select the most appropriate answer.

Q1. How satisfied are you with our product overall?
(a) Very satisfied
(b) Somewhat satisfied
(c) Neutral
(d) Dissatisfied

Q2. On a scale of 1 to 10, how likely are you to recommend our services?

Question 3. What features would you like to see improved? Please describe below:
_______________________________________________

Q4. How long have you been a customer?
(a) Less than 1 year
(b) 1-3 years
(c) More than 3 years

Please check all that apply:
[ ] Price reduction
[ ] Faster support
[ ] New features
[ ] Better documentation
""".strip()

EXCEL_TEXT = """
[Sheet: Sales Data]

Region         | Q1_2023 | Q2_2023 | Q3_2023 | Q4_2023
North America  | 1200000 | 1350000 | 1100000 | 1450000
Europe         |  850000 |  920000 |  780000 |  980000
Asia Pacific   |  430000 |  510000 |  620000 |  750000
""".strip()


def _make_parsed_doc(text: str, file_type: str = "pdf", n_pages: int = 1) -> ParsedDocument:
    """Helper: build a ParsedDocument from raw text."""
    chunks = [
        DocumentChunk(text=text, page_or_sheet=i + 1, chunk_index=i)
        for i in range(n_pages)
    ]
    return ParsedDocument(
        file_name="test.pdf",
        file_type=file_type,
        chunks=chunks,
        full_text=text,
        tables=[],
        metadata={"test": True},
        page_count=n_pages,
    )


# ── TextCleanerSkill ──────────────────────────────────────────────────────────

class TestTextCleanerSkill:
    def test_basic_cleaning(self):
        from skills.text_cleaner_skill import TextCleanerSkill

        skill = TextCleanerSkill()
        dirty = "Hello-\nworld\n\n\n\n  trailing  \nPage 1"
        doc = _make_parsed_doc(dirty)
        out = skill.safe_execute(SkillInput(data={"parsed_document": doc}))

        assert out.success
        cleaned: ParsedDocument = out.data
        assert "Helloworld" in cleaned.full_text          # hyphenation repaired
        assert "\n\n\n" not in cleaned.full_text           # no triple+ newlines

    def test_empty_text_passes(self):
        from skills.text_cleaner_skill import TextCleanerSkill

        skill = TextCleanerSkill()
        doc = _make_parsed_doc("")
        out = skill.safe_execute(SkillInput(data={"parsed_document": doc}))
        assert out.success

    def test_chunking_single_short(self):
        from skills.text_cleaner_skill import TextCleanerSkill

        skill = TextCleanerSkill(config={"chunk_size": 1000})
        chunks = skill.create_text_chunks("Short text.")
        assert len(chunks) == 1
        assert chunks[0] == "Short text."

    def test_chunking_long_text(self):
        from skills.text_cleaner_skill import TextCleanerSkill

        skill = TextCleanerSkill(config={"chunk_size": 200, "chunk_overlap": 20})
        long_text = ("This is a sentence. " * 50).strip()
        chunks = skill.create_text_chunks(long_text)
        assert len(chunks) > 1
        # All chunks should be non-empty
        for ch in chunks:
            assert ch.strip()

    def test_missing_input_returns_error(self):
        from skills.text_cleaner_skill import TextCleanerSkill

        skill = TextCleanerSkill()
        out = skill.safe_execute(SkillInput(data={}))
        assert not out.success
        assert "missing" in out.error.lower()


# ── DocumentClassifierSkill ───────────────────────────────────────────────────

class TestDocumentClassifierSkill:
    def _make_skill(self, threshold=0.3):
        from skills.document_classifier_skill import DocumentClassifierSkill
        return DocumentClassifierSkill(config={
            "questionnaire_threshold": threshold,
            "groq": {"enabled": False},  # disable LLM in unit tests
        })

    def test_normal_doc(self):
        skill = self._make_skill()
        out = skill.safe_execute(SkillInput(data={"full_text": NORMAL_TEXT}))
        assert out.success
        result = out.data
        assert result.doc_type == "normal_document"
        assert result.confidence < 0.5

    def test_questionnaire_classified(self):
        skill = self._make_skill(threshold=0.3)
        out = skill.safe_execute(SkillInput(data={"full_text": QUESTIONNAIRE_TEXT}))
        assert out.success
        result = out.data
        assert result.doc_type == "questionnaire"
        assert result.confidence >= 0.3

    def test_empty_text_is_not_given_a_verdict(self):
        """Renamed from `test_empty_text_returns_normal`, which asserted the
        bug: empty text was classified `normal_document`, and that verdict then
        rendered as "100% confidence" on a document with zero words in it.

        There is nothing to classify, so the skill reports `unknown`. Fuller
        cover, including why the agent's is_empty gate makes this unreachable
        end-to-end, is in tests/test_empty_document.py.
        """
        skill = self._make_skill()
        out = skill.safe_execute(SkillInput(data={"full_text": ""}))
        assert out.success
        assert out.data.doc_type == "unknown"
        assert out.data.method == "none"

    def test_signals_populated(self):
        skill = self._make_skill()
        out = skill.safe_execute(SkillInput(data={"full_text": QUESTIONNAIRE_TEXT}))
        assert isinstance(out.data.signals, dict)
        assert len(out.data.signals) > 0

    def test_method_heuristic(self):
        skill = self._make_skill()
        out = skill.safe_execute(SkillInput(data={"full_text": NORMAL_TEXT}))
        assert out.data.method == "heuristic"


# ── SummarizationSkill ────────────────────────────────────────────────────────

class TestSummarizationSkill:
    def _make_skill(self):
        from skills.summarization_skill import SummarizationSkill
        return SummarizationSkill(config={
            "groq": {"enabled": False},  # force extractive (env key must not be picked up)
            "extractive_sentences": 5,
        })

    def test_extractive_summary_produced(self):
        skill = self._make_skill()
        out = skill.safe_execute(SkillInput(data={"full_text": NORMAL_TEXT}))
        assert out.success
        assert out.data["method"] == "extractive"
        assert len(out.data["summary"]) > 0

    def test_empty_text_fails_gracefully(self):
        skill = self._make_skill()
        out = skill.safe_execute(SkillInput(data={"full_text": ""}))
        assert not out.success
        assert out.error is not None

    def test_summary_contains_key_terms(self):
        skill = self._make_skill()
        out = skill.safe_execute(SkillInput(data={"full_text": NORMAL_TEXT}))
        summary = out.data["summary"].lower()
        # Should mention at least one term from the document
        assert any(term in summary for term in ["acme", "revenue", "billion", "growth", "2023"])


# ── QuestionExtractionSkill ───────────────────────────────────────────────────

class TestQuestionExtractionSkill:
    def _make_skill(self):
        from skills.question_extraction_skill import QuestionExtractionSkill
        return QuestionExtractionSkill(config={
            "groq": {"api_key": ""},
            "dedup": True,
            "max_questions": 50,
        })

    def test_extracts_from_questionnaire(self):
        skill = self._make_skill()
        out = skill.safe_execute(SkillInput(data={
            "full_text": QUESTIONNAIRE_TEXT,
            "doc_type": "questionnaire",
        }))
        assert out.success
        assert len(out.data["questions"]) > 0

    def test_skips_normal_doc(self):
        skill = self._make_skill()
        out = skill.safe_execute(SkillInput(data={
            "full_text": NORMAL_TEXT,
            "doc_type": "normal_document",
        }))
        assert out.success
        assert len(out.data["questions"]) == 0
        assert out.data["method"] == "skipped"

    def test_force_extract_from_normal(self):
        skill = self._make_skill()
        out = skill.safe_execute(SkillInput(data={
            "full_text": QUESTIONNAIRE_TEXT,
            "doc_type": "normal_document",
            "force": True,
        }))
        assert out.success
        # force=True bypasses the doc_type check
        assert isinstance(out.data["questions"], list)

    def test_deduplication(self):
        skill = self._make_skill()
        # Repeat the same question multiple times
        repeated = (QUESTIONNAIRE_TEXT + "\n\n" + QUESTIONNAIRE_TEXT)
        out = skill.safe_execute(SkillInput(data={
            "full_text": repeated,
            "doc_type": "questionnaire",
        }))
        # Dedup should reduce duplicates
        assert out.success
        first_run_qs = out.data["questions"]
        assert len(first_run_qs) < 20  # should not be 2x

    def test_questions_capitalized(self):
        skill = self._make_skill()
        out = skill.safe_execute(SkillInput(data={
            "full_text": QUESTIONNAIRE_TEXT,
            "doc_type": "questionnaire",
        }))
        for q in out.data["questions"]:
            assert q[0].isupper(), f"Question not capitalized: {q!r}"

    def test_missing_input(self):
        skill = self._make_skill()
        out = skill.safe_execute(SkillInput(data={}))
        assert not out.success


# ── ExcelReaderSkill (structural) ─────────────────────────────────────────────

class TestExcelReaderSkill:
    def test_missing_file_fails(self):
        from skills.excel_reader_skill import ExcelReaderSkill
        skill = ExcelReaderSkill()
        out = skill.safe_execute(SkillInput(data={"file_path": "/nonexistent/file.xlsx"}))
        assert not out.success

    def test_missing_input(self):
        from skills.excel_reader_skill import ExcelReaderSkill
        skill = ExcelReaderSkill()
        out = skill.safe_execute(SkillInput(data={}))
        assert not out.success


# ── PDFReaderSkill (structural) ───────────────────────────────────────────────

class TestPDFReaderSkill:
    def test_missing_file_fails(self):
        from skills.pdf_reader_skill import PDFReaderSkill
        skill = PDFReaderSkill()
        out = skill.safe_execute(SkillInput(data={"file_path": "/nonexistent/file.pdf"}))
        assert not out.success

    def test_missing_input(self):
        from skills.pdf_reader_skill import PDFReaderSkill
        skill = PDFReaderSkill()
        out = skill.safe_execute(SkillInput(data={}))
        assert not out.success


# ── BaseSkill contract ────────────────────────────────────────────────────────

class TestBaseSkillContract:
    def test_validate_inputs_catches_missing(self):
        from skills.text_cleaner_skill import TextCleanerSkill
        skill = TextCleanerSkill()
        out = skill.validate_inputs(SkillInput(data={}))
        assert out is not None
        assert "missing" in out.lower()

    def test_validate_inputs_ok(self):
        from skills.text_cleaner_skill import TextCleanerSkill
        skill = TextCleanerSkill()
        doc = _make_parsed_doc("Hello world")
        out = skill.validate_inputs(SkillInput(data={"parsed_document": doc}))
        assert out is None  # no error

    def test_repr(self):
        from skills.text_cleaner_skill import TextCleanerSkill
        skill = TextCleanerSkill()
        assert "TextCleanerSkill" in repr(skill)
        assert "text_cleaner" in repr(skill)

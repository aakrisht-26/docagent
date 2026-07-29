# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the UI
streamlit run ui/app.py

# Install dependencies
pip install -r requirements.txt

# Editable install of the library packages (agents, core, skills, utils).
# There is no `docagent` console command — use `streamlit run ui/app.py`.
pip install -e .

# Run all tests
pytest tests/ -v

# Run a single test file
pytest tests/test_skills.py -v

# Run a single test by name
pytest tests/test_skills.py::TestClassName::test_method_name -v
```

Environment: Set `GROQ_API_KEY=gsk_...` (or place in `.env`). Config is loaded from `configs/default.yaml` and can be overridden by env vars prefixed with `DOCAGENT_`.

## FROZEN PIPELINE — DO NOT MODIFY

The core document processing pipeline in `agents/document_agent.py` is locked. **Do not change the step order, add steps, remove steps, or alter how steps hand off data to each other** without an explicit instruction from the user.

The pipeline is:

```
Step 1  Parse              — pdf_reader / excel_reader / audio_reader
Step 2  Clean              — text_cleaner
Step 3  Classify           — document_classifier  (heuristic + optional LLM)
Step 3.5 Structure Recog.  — structure_recognition (tables; planner-gated)
Step 4  Summarize          — summarization         (planner-gated)
Step 5  Extract Questions  — question_extraction   (planner-gated)
Step 5.5 Structured Ext.   — structured_extraction (planner-gated, optional)
Step 6  Assemble           — builds PipelineResult
```

- Steps 3.5, 4, 5, 5.5 are **planner-gated**: `PipelinePlanner.plan()` decides whether to include them based on `DocStats` (file type, word count, doc type, etc.). Do not bypass the planner.
- Data flows strictly through typed dataclasses: `SkillInput` → skill → `SkillOutput` → next skill. Do not add direct skill-to-skill calls.
- `_run_core_pipeline()` (steps 2–6) is shared by `run()` (file) and `run_youtube()` (YouTube). Changes there affect both paths.

## Architecture

### Agent-Skill Pattern

The system separates **orchestration** from **capabilities**:

- **Skills** (`skills/`) — stateless, atomic units. Each implements `BaseSkill.execute(SkillInput) → SkillOutput`. Skills never call other skills.
- **Agents** (`agents/`) — orchestrators that sequence skills. `DocumentAgent` runs a 6-step pipeline: Parse → Clean → Classify → Structure Recognition → Summarize → Extract Questions.
- **SkillRegistry** (`core/skill_registry.py`) — singleton that auto-discovers all `BaseSkill` subclasses at import time. Adding a new skill requires no changes to agents or config.

### Data Flow

```
File path → DocumentAgent.run()
  → ParsedDocument (chunks, tables, full_text, metadata)
  → ClassificationResult (doc_type, domain, confidence, method)
  → PipelineResult (summary, questions, classification, skill timings)
```

All inter-component communication uses typed dataclasses from `core/models.py`. `SkillInput` holds `data: Dict[str, Any]`; `SkillOutput` carries `success`, `data`, `error`, `warnings`, `duration_ms`.

### LLM Client

`utils/llm_client.py` wraps the OpenAI SDK pointed at Groq Cloud (`llama-3.3-70b-versatile`). Supports multi-key round-robin for rate-limit resilience. Default model, temperature (0.15), and timeout (180s) are in `configs/default.yaml`.

### Hybrid Classification

`DocumentClassifierSkill` uses two phases:
1. **Heuristic**: 20+ regex patterns with weighted scoring
2. **LLM disambiguation**: Only invoked when heuristic score is in borderline range (0.10–0.70); blended 60% LLM / 40% heuristic

### PDF Parsing Fallback Chain

`PDFReaderSkill` escalates: pdfplumber → PyMuPDF → Tesseract OCR (with Gaussian equalization + adaptive thresholding for scanned docs).

### Summarization Strategy

`SummarizationSkill` uses map-reduce: section-aware chunking → per-chunk bullet extraction → LLM synthesis. Falls back to heading-boosted extractive scoring if LLM is unavailable.

### UI

`ui/app.py` is the Streamlit entry point. Results display logic lives in `ui/components/results_view.py`. Custom glassmorphism styles are in `ui/styles/custom.css`.

### Configuration

`utils/config.py` defines typed dataclasses (`AppConfig`, `GroqConfig`, `PDFConfig`, etc.) loaded from `configs/default.yaml`. All values can be overridden by env vars.

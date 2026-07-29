# DocAgent

DocAgent is a high-performance, modular AI agent for intelligent document understanding. It goes beyond simple text extraction by using an **Agent-Skill architecture** to classify, summarise, and extract actionable data from PDFs, Excel files, CSVs, audio files, and YouTube videos — all powered by **Groq Cloud** for near-instant responses.

![Powered By Groq](https://img.shields.io/badge/Powered%20By-Groq%20Cloud-f59e0b)
![Python 3.10+](https://img.shields.io/badge/Python-3.10+-06B6D4)
![License](https://img.shields.io/badge/License-MIT-green)

---

## Key Features

- **Multi-Format Input** — Processes PDF, Excel (.xlsx / .xls), CSV, audio files (MP3, WAV, M4A, FLAC, OGG, WebM), and YouTube video URLs in a single unified pipeline.
- **Audio & YouTube Transcription** — Downloads audio from any YouTube link via yt-dlp, transcribes it using Groq's Whisper-large-v3 API, then runs the full analysis pipeline on the transcript.
- **Domain-Aware Summarisation** — Automatically detects the document domain (Technical, Financial, Legal, Educational) and adopts a specialist analyst persona for high-fidelity summaries with inline page citations.
- **Audience-Tuned Output** — Produces summaries at four tones (Expert, Professional, General, Student) and four lengths (Concise → Exhaustive), all configurable from the UI.
- **Hybrid Document Classification** — Combines 20+ weighted regex signals with an LLM disambiguation phase to classify documents as questionnaire/form or normal document, with confidence score and domain label.
- **Multi-Key API Resilience** — Round-robin rotation across multiple Groq API keys with automatic rate-limit (HTTP 429) recovery and retry logic.
- **Advanced Adaptive OCR** — OpenCV-driven pipeline using Gaussian equalisation and adaptive thresholding to extract text from scanned or photographed PDFs with poor lighting.
- **High-Fidelity Table Extraction** — PaddleOCR PP-Structure V3 for domain-targeted (Technical, Financial) document table extraction as structured HTML, preventing garbled text from complex layouts.
- **Multi-Turn Document Chat** — RAG-lite Q&A: keyword-overlap chunk selection provides relevant context to the LLM without needing a vector database.
- **Natural Language Document Editing** — Describe edits in plain English; the LLM rewrites the document or applies structured JSON operations to Excel sheets.
- **Form Filling** — Paste answers to extracted questions; DocAgent compiles the completed form as a downloadable PDF.
- **Glassmorphic Web UI** — Streamlit-based UI with real-time pipeline progress, tabbed results, light/dark mode, and one-click PDF/Markdown/JSON/CSV export.

---

## Getting Started

Follow these five steps in order on a fresh clone. Step 3 is the one people skip,
and it is the one that breaks audio and YouTube.

### 1. Clone and install Python dependencies

Requires **Python 3.10+**.

```bash
git clone https://github.com/aakrisht-26/docagent.git
cd docagent
pip install -r requirements.txt
```

### 2. Add your Groq API key

Get one from the [Groq Cloud Console](https://console.groq.com/keys), then:

```bash
cp .env.example .env
```

On Windows use `copy .env.example .env` (cmd) or `Copy-Item .env.example .env`
(PowerShell). Open `.env` and replace the placeholder keys with real ones.

`.env.example` documents every supported variable. Only the API key is required;
everything else has a working default in `configs/default.yaml`.

> **Multiple keys must be quoted if split across lines.** DocAgent rotates
> round-robin across keys and fails over on rate limits (429) and invalid keys
> (401). If you write the value unquoted across several lines, python-dotenv
> stops at the first newline, silently loads only the first key, and logs
> `Python-dotenv could not parse statement starting at line N`.

**Without a key:** the app still runs. Classification falls back to heuristics
and summarisation to extractive mode; chat, question extraction, and audio
transcription are unavailable.

### 3. Install the system binaries

These are **external programs, not pip packages**. `pip install ffmpeg-python`
does *not* give you ffmpeg.

| Binary | Needed for | Without it |
|---|---|---|
| **ffmpeg** + **ffprobe** | Audio and YouTube | YouTube downloads the stream, then fails in postprocessing with `ffprobe and ffmpeg not found`. Local audio files fail to convert. PDF, Excel and CSV are unaffected. |
| **tesseract** | OCR fallback for scanned PDFs | Scanned or photographed PDFs with no text layer produce an empty document. PDFs with a text layer are unaffected, since pdfplumber and PyMuPDF handle those first. |

```bash
# Windows (winget) — ffmpeg ships ffprobe alongside it
winget install --id Gyan.FFmpeg -e
winget install --id UB-Mannheim.TesseractOCR -e

# macOS
brew install ffmpeg tesseract

# Ubuntu / Debian
sudo apt-get install ffmpeg tesseract-ocr
```

Verify all three are visible before continuing. On Windows you may need a new
terminal after installing, since PATH changes do not reach already-running
processes:

```bash
ffmpeg -version && ffprobe -version && tesseract --version
```

### 4. Verify the install end to end

This runs the real pipeline against committed fixtures and the live Groq API —
a text-layer PDF, a scanned image-only PDF (which forces the Tesseract OCR
tier), an Excel workbook, an audio file, a YouTube video, and a two-turn RAG
exchange:

```bash
python tests/e2e/e2e.py all
```

Exit code 0 with six `PASS` rows means the install is good. Run a single stage
with `python tests/e2e/e2e.py pdf` (or `scanned`, `excel`, `audio`, `youtube`,
`rag`).

The `scanned` stage is the one that proves Tesseract is wired up correctly: it
asserts the OCR engine actually ran, so it fails loudly rather than silently
falling back if `tesseract` is missing from PATH.

Unit tests, which need no API key or network:

```bash
pytest tests/ -v
```

### 5. Run the app

```bash
streamlit run ui/app.py
```

Open `http://localhost:8501` in your browser.

### Environment variable reference

All settings can be overridden by environment variable; see `.env.example` for
the full annotated list.

| Variable | Purpose | Default |
|---|---|---|
| `GROQ_API_KEYS` / `GROQ_API_KEY` | Groq API key(s), comma-separated | — |
| `DOCAGENT_MAX_FILE_MB` | Max upload size in MB | 50 |
| `DOCAGENT_LOG_LEVEL` | Logging verbosity | INFO |
| `DOCAGENT_LOG_FILE` | Log file path | logs/docagent.log |
| `DOCAGENT_GROQ_ENABLED` | Set false to disable all LLM calls | true |
| `DOCAGENT_GROQ_URL` | OpenAI-compatible endpoint | https://api.groq.com/openai/v1 |
| `DOCAGENT_GROQ_MODEL` | LLM model name | llama-3.3-70b-versatile |
| `DOCAGENT_GROQ_TIMEOUT` | Per-request timeout in seconds | 180 |

---

## Supported Inputs

| Type | Extensions / Format |
|---|---|
| PDF | `.pdf` |
| Excel | `.xlsx`, `.xls` |
| CSV | `.csv` |
| Audio | `.mp3`, `.m4a`, `.wav`, `.flac`, `.ogg`, `.webm` |
| YouTube | Any `youtube.com/watch?v=` or `youtu.be/` URL |

Maximum file size: **50 MB** (configurable via `DOCAGENT_MAX_FILE_MB`).

---

## Pipeline Overview

Every input — regardless of type — flows through the same six-step pipeline:

```
Input (file / YouTube URL)
    │
    ▼
1. Parse ──────── PDFReaderSkill  (pdfplumber → PyMuPDF → Tesseract)
                  ExcelReaderSkill (openpyxl + pandas)
                  AudioReaderSkill (yt-dlp download → Groq Whisper)
    │
    ▼
2. Clean ─────── TextCleanerSkill  (ftfy + regex normalisation)
    │
    ▼
3. Classify ──── DocumentClassifierSkill  (heuristic + LLM hybrid)
    │
    ▼
4. Structure ──── StructureRecognitionSkill  (PP-Structure; Technical/Financial only)
    │
    ▼
5. Summarise ──── SummarizationSkill  (map-reduce LLM; extractive fallback)
    │
    ▼
6. Questions ──── QuestionExtractionSkill  (regex + LLM; Jaccard dedup)
    │
    ▼
PipelineResult  (summary, questions, tables, citations, timings, metadata)
```

---

## Architecture

DocAgent is built on a strict **Agent-Skill separation**:

- **Skills** (`skills/`) — Stateless, atomic units. Each inherits `BaseSkill` and implements `execute(SkillInput) → SkillOutput`. Skills never call other skills.
- **Agents** (`agents/`) — Orchestrators that sequence skills. `DocumentAgent` manages the 6-step pipeline and routes by file type.
- **SkillRegistry** (`core/skill_registry.py`) — Singleton that auto-discovers all `BaseSkill` subclasses at import time. Adding a new skill requires zero changes to any other file.
- **Typed Data Models** (`core/models.py`) — `SkillInput`, `SkillOutput`, `ParsedDocument`, `ClassificationResult` define strict contracts between every component.

### Directory Structure

```
doc-agent/
├── agents/
│   ├── base_agent.py          # Abstract BaseAgent interface
│   └── document_agent.py      # Main orchestrator + run_youtube()
├── core/
│   ├── models.py              # DocumentChunk, ParsedDocument, SkillInput/Output
│   ├── pipeline_result.py     # Final typed output (to_markdown, to_dict)
│   └── skill_registry.py      # Singleton auto-discovery registry
├── skills/
│   ├── base_skill.py          # Abstract BaseSkill (validate + safe_execute)
│   ├── pdf_reader_skill.py    # 3-tier PDF engine (pdfplumber→fitz→OCR)
│   ├── excel_reader_skill.py  # Excel/CSV parser (openpyxl + pandas)
│   ├── audio_reader_skill.py  # Audio/YouTube transcription (yt-dlp + Whisper)
│   ├── text_cleaner_skill.py  # Text normalisation (ftfy + regex)
│   ├── document_classifier_skill.py  # Heuristic + LLM classification
│   ├── structure_recognition_skill.py # PP-Structure table extraction
│   ├── summarization_skill.py # Map-reduce LLM summarisation
│   ├── question_extraction_skill.py  # Regex + LLM question extraction
│   ├── document_chat_skill.py # RAG-lite multi-turn Q&A
│   ├── document_editor_skill.py # NL document editing
│   └── form_filling_skill.py  # Form compilation
├── utils/
│   ├── config.py              # Typed config (YAML + env vars)
│   ├── llm_client.py          # Groq Cloud client (OpenAI-compatible)
│   ├── file_handler.py        # File validation + temp management
│   └── logger.py              # Centralised Rich logging
├── ui/
│   ├── app.py                 # Streamlit entry point
│   └── components/
│       └── results_view.py    # Tabbed results + PDF/MD/JSON export
├── configs/
│   └── default.yaml           # Default configuration
└── tests/
    ├── test_skills.py         # Skill unit tests
    └── test_agent.py          # Agent + registry integration tests
```

---

## Development

```bash
# Run all tests
pytest tests/ -v

# Run a specific test class
pytest tests/test_skills.py::TestDocumentClassifierSkill -v

# Development install (enables `docagent` CLI entry point)
pip install -e .
```

### Adding a New Skill

1. Create `skills/my_skill.py` inheriting `BaseSkill`
2. Set `name`, `description`, `required_inputs`
3. Implement `execute(inputs: SkillInput) → SkillOutput`
4. That's it — `SkillRegistry` discovers it automatically at startup

```python
from skills.base_skill import BaseSkill
from core.models import SkillInput, SkillOutput

class MySkill(BaseSkill):
    name = "my_skill"
    description = "Does something useful."
    required_inputs = ["text"]

    def execute(self, inputs: SkillInput) -> SkillOutput:
        result = process(inputs.data["text"])
        return SkillOutput(success=True, data=result)
```

---

## Technical Stack

| Component | Technology |
|---|---|
| LLM / Chat | Groq Cloud (llama-3.3-70b-versatile) |
| Transcription | Groq Whisper (whisper-large-v3) |
| YouTube Download | yt-dlp |
| Audio Conversion | pydub + ffmpeg |
| PDF Parsing | pdfplumber, PyMuPDF |
| Table Extraction | PaddleOCR PP-Structure V3 |
| OCR | Tesseract + OpenCV + numpy |
| Excel / CSV | openpyxl, pandas |
| Text Repair | ftfy |
| UI | Streamlit |
| PDF Export | ReportLab |
| Config | PyYAML + dataclasses |
| Logging | Rich |

---

## License

Distributed under the MIT License.

---

*Built for Speed. Built for Accuracy. Powered by Groq.*

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
- **Multi-Turn Document Chat** — Semantic Q&A: chunks are retrieved by embedding similarity (`all-MiniLM-L6-v2`, local, no API) with numpy cosine scoring — no vector database. Falls back to keyword overlap when the model is unavailable. Measured **18/18** retrieval on the eval set, against 10/18 for the original keyword approach ([details](tests/e2e/rag_eval/RESULTS.md)).
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

> **First run downloads the embedding model.** The first document you analyse
> pulls `all-MiniLM-L6-v2` (**87 MB**) into `~/.cache/huggingface` and takes
> **roughly 30–55 seconds** depending on network and disk; every run after that
> is ~0.03 s. The UI shows a spinner during it. See [Document chat retrieval](#document-chat-retrieval).

Retrieval quality can be scored on its own, with no API calls and no network:

```bash
python tests/e2e/rag_eval/run_eval.py
```

Unit tests, which need no API key or network:

```bash
pytest tests/ -v
```

### 5. Run the app

```bash
streamlit run ui/app.py
```

Open `http://localhost:8501` in your browser.

### Document chat retrieval

The Chat tab answers questions over one document. Which chunks reach the model is
decided here.

| | |
|---|---|
| **Model** | `all-MiniLM-L6-v2` via `sentence-transformers`, 384 dimensions |
| **Runs** | Locally, on CPU. Groq has no embeddings endpoint, so there is nothing to call |
| **Scoring** | numpy cosine over an L2-normalised matrix — **no vector database** |
| **Where vectors live** | `history.db`, column `content_embeddings_b64` (base64 float32), alongside `embedding_model` |
| **Fallback** | Keyword overlap, used whenever the model cannot be imported or loaded |
| **Loading** | Lazy — on first use, never at import |

**Persistence.** Vectors are computed once, when a document is saved to history,
and stored with the model name that produced them. A document reloaded from
history is queryable without re-embedding. Vectors written by a *different*
model are ignored rather than compared, since they are not comparable.

**Fallback.** If `sentence-transformers` is missing or the weights cannot be
downloaded, chat keeps working on keyword overlap. Every query logs which path
served it:

```
Chunk retrieval via embedding: selected [17, 18, 15] from 20 chunk(s) (top score 0.510)
Chunk retrieval via keyword_overlap: selected [9, 13, 18] from 20 chunk(s) (top score 3.000)
```

#### Measured retrieval accuracy

18 question/expected-source pairs over the fixtures, scored on whether the chunk
holding the answer was **ranked** into the selected set. Full method and
per-case results in [`tests/e2e/rag_eval/RESULTS.md`](tests/e2e/rag_eval/RESULTS.md).

| Method | ranked | PDF | Workbook |
|---|---|---|---|
| Keyword overlap, as originally shipped | 10/18 | 9/12 | 1/6 |
| Keyword overlap, tokenizer bug fixed | 13/18 | 9/12 | 4/6 |
| **Embeddings (current)** | **18/18** | **12/12** | **6/6** |

| Category | keyword (orig) | tokenizer-fixed | embeddings |
|---|---|---|---|
| `direct` | 4/4 | 4/4 | 4/4 |
| `vocab_overlap` | 4/6 | 6/6 | 6/6 |
| `cross_boundary` | 2/3 | 3/3 | 3/3 |
| `synonym` | 0/4 | 0/4 | **4/4** |
| `adversarial_sibling` | 0/1 | 0/1 | **1/1** |

**Not all of that gain belongs to embeddings.** Three of the eight improvements
over the original baseline were a tokenizer bug, not the retrieval method:
`_tokenize` matched `[a-z]{3,}` and so never saw `Q1` or `Q3`, which made every
quarterly sheet in a workbook score identically. Fixing that regex alone takes
keyword overlap from **10/18 to 13/18**, and it has been fixed independently
because the fallback path deserves to work.

The genuine embedding win is the **five cases no regex reaches**: the four
`synonym` questions, which share no content word with the chunk that answers
them, and the adversarial sibling case, which asks about "the second quarter"
without ever writing `Q2`.

Run either path yourself:

```bash
python tests/e2e/rag_eval/run_eval.py            # embeddings
python tests/e2e/rag_eval/run_eval.py --keyword  # the fallback, scored alone
```

### Groq rate limits — read this before you debug a stalled run

Groq enforces **four** separate limits. Two are reported in response headers,
two are not, and **the one that will actually stop this pipeline is invisible**.

| Limit | Meaning | Free-tier value | Visible in headers? |
|---|---|---|---|
| **RPM** | requests per minute | — | no |
| **RPD** | requests per **day** | 1,000 | yes — `x-ratelimit-*-requests` |
| **TPM** | tokens per **minute** | 12,000 | yes — `x-ratelimit-*-tokens` |
| **TPD** | tokens per **day** | **100,000** | **no** |

**The header names are asymmetric. Do not assume they pair up:**

```
x-ratelimit-limit-requests  ->  requests per DAY      (RPD)
x-ratelimit-limit-tokens    ->  tokens   per MINUTE   (TPM)
```

They look symmetric and describe different windows. The `reset` fields follow
their own metric, so `reset-requests` may read `1h12m` while `reset-tokens`
reads `3.6s` on the same response.

**TPD is the binding constraint for this pipeline.** A single document run costs
roughly 2,000–3,000 tokens across classification, summarisation and structured
extraction, so **100,000 tokens/day is on the order of 30–50 documents**. Whisper
transcription is billed per second of audio and does not draw on TPD.

The trap: TPD appears in no header, so a key can report healthy headroom
(`requests/day 988/1000`, `tokens/min 11453/12000`) and still be refused on the
very next call. The limit is only ever stated in the body of a 429:

```
Rate limit reached for model `llama-3.3-70b-versatile` in organization
`org_...` service tier `on_demand` on tokens per day (TPD):
Limit 100000, Used 99682, Requested 2267. Please try again in 28m3.936s.
```

**Limits are enforced per API key, not pooled across an organisation** — verified
by observing two keys refused in the same run at different consumption levels
(99,654/100,000 and 98,412/100,000). Configuring several keys in `GROQ_API_KEYS`
therefore multiplies your daily budget, and `LLMClient` rotates automatically.

How the client responds:

- A **short** 429 (per-minute limits, seconds) → back off and retry.
- A **long** 429 (per-day limits, minutes or hours) → **park that key** until its
  window elapses and switch to the next key immediately. Parking expires by
  itself; it is not the permanent retirement applied to a 401.
- TPD figures scraped from a refusal are cached per key and reported as
  **derived from the last refusal, not live**. A key that has never been refused
  reports `unknown` rather than a guess — no request is ever sent purely to
  discover a limit.

To see per-key headroom:

```bash
DOCAGENT_LOG_LEVEL=DEBUG python tests/e2e/e2e.py pdf
```

To inspect limits directly, `tests/e2e/ratelimit_probe.py` reads the header
counters and `tests/e2e/ratelimit_scope_check.py` reports which keys are
currently accepting requests.

### Environment variable reference

All settings can be overridden by environment variable; see `.env.example` for
the full annotated list.

| Variable | Purpose | Default |
|---|---|---|
| `GROQ_API_KEYS` / `GROQ_API_KEY` | Groq API key(s), comma-separated | — |
| `DOCAGENT_MAX_FILE_MB` | Max upload size in MB | 50 |
| `DOCAGENT_LOG_LEVEL` | Logging verbosity | INFO |
| `DOCAGENT_LOG_FILE` | Log file path | logs/docagent.log |
| `DOCAGENT_DEBUG` | Re-raise pipeline errors instead of catching them | false |
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
│   ├── document_chat_skill.py # Multi-turn Q&A (embedding retrieval)
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

# Editable install of the library packages (agents, core, skills, utils)
pip install -e .
```

There is no `docagent` console command. The app is started with
`streamlit run ui/app.py` — see [Run the app](#5-run-the-app).

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

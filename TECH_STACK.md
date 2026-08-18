# DocAgent — Technology Stack

DocAgent is a production-quality document understanding system built on a modular Agent-Skill architecture. It uses **Groq Cloud** as its primary intelligence engine for both language generation and audio transcription.

---

## Core Intelligence

### LLM — Groq Cloud (Text)
- **Provider:** [Groq Cloud](https://groq.com/) via OpenAI-compatible API
- **Model:** `openai/gpt-oss-120b` (default; configurable via `DOCAGENT_GROQ_MODEL`)
- **Integration:** `openai` Python SDK pointed at `https://api.groq.com/openai/v1`
- **Multi-key rotation:** `GROQ_API_KEYS` (comma-separated list); automatic HTTP 429 backoff + retry across keys
- **Temperature:** 0.15 (summaries), 0.0 (question extraction), 0.1 (editing), 0.2 (form filling)
- **Timeout:** 180s per request

### Whisper — Groq Cloud (Audio)
- **Model:** `whisper-large-v3`
- **Endpoint:** `POST https://api.groq.com/openai/v1/audio/transcriptions`
- **Supported formats:** MP3, M4A, WAV, FLAC, OGG, WebM
- **File size limit:** 25 MB per Groq's API constraint (mitigated by downloading YouTube audio as 128K MP3 instead of uncompressed WAV)
- **Language:** Auto-detect (multilingual, 99 languages)

---

## Audio & Video Processing

### YouTube Download — yt-dlp
- **Package:** `yt-dlp >= 2024.01.01`
- **Strategy:** Python module (`import yt_dlp`) with automatic CLI binary fallback (`shutil.which("yt-dlp")` + subprocess) — resilient against cached Streamlit processes that loaded before the package was installed
- **Output format:** MP3 @ 128K (compressed to stay under Groq's 25 MB limit)
- **Timeout:** 300s via subprocess wrapper

### Audio Conversion — pydub + ffmpeg
- **Package:** `pydub >= 0.25.1`, `ffmpeg-python >= 0.2.0`
- **System requirement:** `ffmpeg` binary (brew / apt / choco)
- **Purpose:** Format detection, conversion, and chunking for unsupported or oversized audio files

---

## Document Parsing

### PDF — pdfplumber (Primary)
- **Package:** `pdfplumber >= 0.10.3`
- **Mode:** `layout=True` for character-position-aware text extraction
- **Tables:** Bbox-based masking — detected tables are physically removed from text stream, extracted separately, and re-inserted as `[TABLE] … [/TABLE]` blocks to prevent duplicate/garbled text
- **Failure trigger:** Empty or garbage text after extraction

### PDF — PyMuPDF / fitz (Fallback)
- **Package:** `PyMuPDF >= 1.23.0`
- **Use case:** Encrypted PDFs, complex vector layouts that pdfplumber cannot handle
- **Mode:** `page.get_text("text")` — simple linear extraction

### PDF — Tesseract OCR (Final fallback)
- **Packages:** `pytesseract >= 0.3.10`, `opencv-python-headless == 4.8.1.78`, `numpy == 1.26.4`, `Pillow >= 10.0.0`
- **Trigger:** Both PDF engines produce empty or garbage text
- **Pre-processing pipeline:**
  1. Render page to 400 DPI via PyMuPDF
  2. Gaussian blur (5×5 kernel) for noise reduction
  3. Adaptive Gaussian thresholding for contrast normalisation
  4. Deskew via `cv2.minAreaRect` on largest contour
  5. Tesseract PSM 3 (automatic page segmentation), OEM 3 (LSTM neural engine)
- **Language:** `eng` (configurable)

### Table Structure — PaddleOCR PP-Structure V3
- **Packages:** `paddlepaddle >= 2.6.2`, `paddleocr >= 2.6.0.3`
- **Trigger:** Activated only for domains where table fidelity matters: Technical, Financial, Research, Scientific
- **Process:** Renders each page at 200 DPI → numpy BGR array → PP-Structure engine → extracts `type == 'table'` HTML → appends to chunk text
- **GPU support:** Auto-detects CUDA via `paddle.device.cuda.device_count()`
- **Lazy loading:** Engine created only on first call to avoid VRAM overhead at startup

### Excel / CSV — openpyxl + pandas
- **Packages:** `openpyxl >= 3.1.2`, `pandas >= 2.0.0`
- **Excel:** `openpyxl` in `data_only` mode (evaluates formulas to values unless `include_formulas=True`)
- **CSV:** `pandas.read_csv` with encoding fallback chain: UTF-8 → Latin-1 → CP1252
- **Smart sampling:** If sheet > 600 rows, takes head(300) + tail(300) with separator for token efficiency

---

## Text Processing

### Encoding Repair — ftfy
- **Package:** `ftfy >= 6.1.3`
- **Purpose:** Fixes mojibake (garbled UTF-8), normalises Unicode, corrects misread special characters from PDF extraction artifacts

### Text Normalisation (in-house regex pipeline)
Runs inside `TextCleanerSkill` on every parsed document:
1. `ftfy.fix_text()` — encoding repair
2. Line ending normalisation (`\r\n` → `\n`)
3. PDF hyphenation repair (`word-\nbreak` → `wordbreak`)
4. Page number line removal (heuristic regex; heading lines protected by guard pattern)
5. Lone bullet-only line removal
6. Excess blank line collapse (`\n{3,}` → `\n\n`)
7. Trailing whitespace stripping

---

## LLM Client Architecture

### Multi-key Round-Robin (`utils/llm_client.py`)
```
Request → LLMClient.chat()
        → Try current key (index 0)
        → HTTP 429? → rotate key index → sleep 1s → retry
        → All keys exhausted? → return None
```
- Keys sourced from: `GROQ_API_KEYS` env → `GROQ_API_KEY` env → `configs/default.yaml`
- Client is lazily created and cached; reset on key rotation

### Whisper Key Resolution (`skills/audio_reader_skill.py`)
- Same priority chain as LLMClient for API key lookup
- Creates a separate `openai.OpenAI` instance pointing to `api.groq.com/openai/v1/audio/transcriptions`

---

## Document Intelligence

### Classification — Hybrid Heuristic + LLM
Two-phase approach in `DocumentClassifierSkill`:
1. **Heuristic phase:** 20+ compiled regex patterns (form titles, Q-numbering, Likert scales, checkbox glyphs, fill-in-blank markers, ABCD options, consent blocks, Yes/No pairs). Weighted sum normalised to 0–1.
2. **LLM phase (borderline only):** If 0.10 ≤ heuristic score ≤ 0.70, LLM classifies first 3,000 chars and returns JSON `{type, confidence, domain}`. Blended 70% LLM + 30% heuristic.
3. **Domain detection:** LLM always runs if available to extract domain label (Technical, Financial, Healthcare, Legal, General, etc.)

### Summarisation — Map-Reduce LLM
Three-path strategy in `SummarizationSkill`:
1. **Single-chunk:** Full document fits in context window → direct LLM call with persona + tone
2. **Map-reduce (multi-chunk):**
   - Map: Each chunk → LLM extracts key facts, entities, metrics, dates (temp=0.1)
   - Reduce: All bullet summaries → LLM synthesises full report with inline `[Source: Pages X, Y]` citations
3. **Extractive fallback:** TF-IDF scored sentence extraction if LLM unavailable

Chunk strategy: **Section-aware** (uses heading detection to keep logical sections together before falling back to character-boundary splitting with sentence overlap).

### Question Extraction — Regex + LLM
Three-layer pipeline in `QuestionExtractionSkill`:
1. **12 regex patterns** targeting Q-numbered questions, sentence-ending `?`, Likert headers, rating scales, Yes/No blocks, field labels
2. **LLM windows:** 5,000-char sliding windows with 200-char overlap; LLM returns strict JSON `{"questions": [...]}` at temperature 0.0
3. **Jaccard deduplication:** Token-set Jaccard ≥ 0.72 → merge to longer variant; normalised before comparison

### RAG-Lite Chat
No external vector database. `DocumentChatSkill` scores chunks by query keyword overlap count, picks top 3 scored + first + last chunk (deduplicated, capped at 6,000 chars). History truncated with token budget (20K token limit, keeps first 2 turns as anchor).

---

## User Interface

### Streamlit
- **Package:** `streamlit >= 1.35.0`
- **Entry point:** `ui/app.py`
- **Theme:** Custom CSS3 glassmorphism — purple (#6d28d9) / cyan (#0891b2) accent palette; light and dark mode with CSS variable overrides
- **Caching:** `@st.cache_resource` for `DocumentAgent` (per summary_length + summary_tone combination); `st.session_state` for file bytes and pipeline results (survive theme-toggle reruns)
- **Progress:** Live progress bar via monkey-patch of `agent._log_step` — always restored in `finally` block to prevent stale closures on repeated analysis

### PDF Export — ReportLab
- **Package:** `reportlab >= 4.0.0`
- **Style:** Purple headings, cyan subheadings, metadata table, skill timing breakdown
- **Fallback:** Returns Markdown bytes if ReportLab unavailable

---

## Configuration System

### Layered Config (`utils/config.py`)
```
configs/default.yaml
    → env var overrides (DOCAGENT_*, GROQ_API_KEY/S)
    → Typed dataclasses (AppConfig, GroqConfig, PDFConfig, etc.)
    → Cached singleton (_CONFIG_CACHE)
```

Supported `DOCAGENT_*` variables: `MAX_FILE_MB`, `GROQ_MODEL`, `GROQ_URL`, `GROQ_TIMEOUT`, `LOG_LEVEL`, `LOG_FILE`.

---

## Logging

### Rich + File Logging (`utils/logger.py`)
- **Console:** `rich.logging.RichHandler` with coloured output, traceback highlighting, `[HH:MM:SS]` timestamps
- **File:** Rotating at `logs/docagent.log`; DEBUG level always (even if console is INFO)
- **Namespace:** All loggers under `docagent.*` hierarchy (e.g., `docagent.skill.pdf_reader`, `docagent.document_agent`)

---

## Testing

- **Framework:** `pytest >= 7.4.0`, `pytest-mock >= 3.11.0`
- **Scope:** Unit tests for all skills; integration tests for `DocumentAgent` + `SkillRegistry`
- **Strategy:** No real files or network calls in tests; synthetic `ParsedDocument` fixtures; mock LLM responses

---

## Package Versions (requirements.txt)

| Category | Package | Version |
|---|---|---|
| PDF | pdfplumber | >= 0.10.3 |
| PDF | PyMuPDF | >= 1.23.0 |
| PDF | Pillow | >= 10.0.0 |
| OCR | pytesseract | >= 0.3.10 |
| OCR | opencv-python-headless | == 4.8.1.78 |
| OCR | numpy | == 1.26.4 |
| Tables | paddlepaddle | >= 2.6.2 |
| Tables | paddleocr | >= 2.6.0.3 |
| Excel | openpyxl | >= 3.1.2 |
| Excel | pandas | >= 2.0.0 |
| Text | ftfy | >= 6.1.3 |
| LLM | openai | >= 1.12.0 |
| Audio | yt-dlp | >= 2024.01.01 |
| Audio | pydub | >= 0.25.1 |
| Audio | ffmpeg-python | >= 0.2.0 |
| Export | reportlab | >= 4.0.0 |
| Config | PyYAML | >= 6.0.1 |
| UI | streamlit | >= 1.35.0 |
| Infra | python-dotenv | >= 1.0.0 |
| HTTP | requests | >= 2.31.0 |
| Logging | rich | >= 13.7.0 |
| Testing | pytest | >= 7.4.0 |
| Testing | pytest-mock | >= 3.11.0 |

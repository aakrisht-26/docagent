# DocAgent — Technology Stack

DocAgent is a production-quality document understanding system built on a modular Agent-Skill architecture. It uses **Groq Cloud** as its primary intelligence engine for both language generation and audio transcription.

---

## Core Intelligence

### LLM — Groq Cloud (Text)
- **Provider:** [Groq Cloud](https://groq.com/) via OpenAI-compatible API
- **Model:** `openai/gpt-oss-120b` (default; configurable via `DOCAGENT_GROQ_MODEL`)
- **Integration:** `openai` Python SDK pointed at `https://api.groq.com/openai/v1`
- **Multi-key rotation:** `GROQ_API_KEYS` (comma-separated list); a 429 or 413 moves to another key, a 401 retires the key, and a key refused against a daily limit is parked until its window passes
- **Temperature:** 0.15 by default; 0.1 for one summarisation call and for editing; 0.0 for classification, question extraction and structured extraction; 0.2 for form filling
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
- **Package:** `yt-dlp >= 2026.8.19` (floored and deliberately not pinned; `requirements.txt` says why)
- **Strategy:** Python module (`import yt_dlp`) with automatic CLI binary fallback (`shutil.which("yt-dlp")` + subprocess) — resilient against cached Streamlit processes that loaded before the package was installed
- **Output format:** MP3 @ 128K (compressed to stay under Groq's 25 MB limit)
- **Timeout:** 300s via subprocess wrapper

### Audio Conversion — ffmpeg
- **Binary:** `ffmpeg` on PATH (brew / apt / winget), or the binary bundled in the `imageio-ffmpeg >= 0.4.9` wheel, which `AudioReaderSkill._find_ffmpeg()` falls back to
- **Purpose:** audio conversion, including yt-dlp's extraction of a downloaded stream to MP3
- `pydub` and `ffmpeg-python` are no longer dependencies: neither was imported anywhere

---

## Document Parsing

### PDF — pdfplumber (Primary)
- **Package:** `pdfplumber >= 0.10.3`
- **Mode:** `layout=True` for character-position-aware text extraction
- **Tables:** Bbox-based masking — detected tables are physically removed from text stream, extracted separately, and appended to the page text under a `[TABLE — Page N, #M]` header to prevent duplicate/garbled text
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
- **Packages:** `paddlepaddle >= 2.6.2`, `paddleocr >= 2.6.0.3`, both marked `sys_platform != "linux"`, so the deployment does not install them
- **Trigger:** Activated only for domains where table fidelity matters: Technical, Financial, Research, Scientific
- **Process:** Renders each page at 200 DPI → numpy BGR array → PP-Structure engine → extracts `type == 'table'` HTML → appends to chunk text
- **GPU gate:** runs only when `_detect_gpu()` finds a CUDA GPU; on CPU it skips unless `pdf.allow_cpu_structure: true`, because a page takes 15–25 minutes there (GPU_SETUP.md)
- **Lazy loading:** Engine created only on first call to avoid VRAM overhead at startup

### Excel / CSV — openpyxl + pandas
- **Packages:** `openpyxl >= 3.1.2`, `pandas >= 2.0.0, < 3`
- **`.xls`:** accepted by the uploader but not readable: openpyxl refuses the legacy format, so the parse fails
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

### Key Rotation (`utils/llm_client.py`)
Every API call, chat and transcription alike, goes through `_run_with_rotation()`:
- **401** → the key is retired for the rest of the process
- **429** → rotate to the next live key; a refusal against a daily limit parks the key until its window passes
- **413** → rotate at once: that key has too little of its rolling per-minute allowance left for the request
- **connection / timeout / 5xx** → retry with exponential backoff
- The OpenAI SDK's own retries are off (`max_retries=0`), so every 429 reaches the rotation
- Key state is shared by every client in the process; replies are cached in an in-process LRU (`utils/llm_cache.py`)
- Keys come from `resolve_groq_api_keys()`, which combines `GROQ_API_KEYS`, `groq.api_keys`, `GROQ_API_KEY` and `groq.api_key`, deduplicated, and rejects placeholders

### Whisper
- `AudioReaderSkill._transcribe_audio()` builds an `LLMClient` and calls `transcribe()`, so transcription uses the same keys and the same rotation

---

## Document Intelligence

### Classification — Hybrid Heuristic + LLM
Two-phase approach in `DocumentClassifierSkill`:
1. **Heuristic phase:** 19 compiled regex patterns (form titles, Q-numbering, Likert scales, checkbox glyphs, fill-in-blank markers, ABCD options, consent blocks, Yes/No pairs) plus a question-mark density bonus. The weighted sum is divided by the total weight (1.81) and capped at 1.
2. **LLM phase (every document, when a key is configured):** one call on the first 3,000 chars returns JSON `{type, confidence, domain}`. Blended 70% LLM + 30% heuristic, unless the heuristic alone is at least 0.85.
3. **Domain:** comes from that same call; without a key it stays `General`

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
1. **10 regex patterns** targeting Q-numbered questions, sentence-ending `?`, Likert headers, rating scales, Yes/No blocks, field labels
2. **LLM windows:** 5,000-char sliding windows with 200-char overlap; LLM returns strict JSON `{"questions": [...]}` at temperature 0.0
3. **Jaccard deduplication:** Token-set Jaccard ≥ 0.72 → merge to longer variant; normalised before comparison

### RAG-Lite Chat
No external vector database. `DocumentChatSkill` ranks overlapping ~100-word passages by embedding similarity (`all-MiniLM-L6-v2`, local CPU, numpy cosine), falling back to keyword overlap when the model cannot load. It takes the top 3 sources plus the first and last chunks as anchors, capped at 6,000 chars; across the history corpus it keys on (document, page) and drops the anchors. History truncated with a token budget (20K tokens, keeps the first 2 turns as anchor).

### Structured Extraction — LLM, then two checks
`StructuredExtractionSkill` fills a domain schema (Financial, Legal, Healthcare, Research; the planner skips documents that resolve to General) with one call at temperature 0.0 and a 2,448-token budget. Two checks run on the reply: patient identifiers are withheld from every field of the Healthcare schema, and a value carrying a figure absent from the document is dropped (`DOCAGENT_EXTRACTION_VERIFY=false` disables that one). Scored by `tests/e2e/extraction_eval/run_eval.py`.

---

## User Interface

### Streamlit
- **Package:** `streamlit == 1.63.0`, pinned to the deployed release; `tests/test_streamlit_compat.py` fails if the pin or the installed version drifts
- **Entry point:** `ui/app.py`
- **Theme:** design tokens as CSS custom properties, the same roles in both themes: dark values in `ui/styles/custom.css` (accent #3b82f6), light values in `_LIGHT_TOKENS` in `ui/app.py` (accent #1d4ed8), switched by a sidebar radio
- **Caching:** `@st.cache_resource` for `DocumentAgent` (per summary_length + summary_tone combination); `st.session_state` for file bytes and pipeline results (survive theme-toggle reruns)
- **Progress:** Live progress bar via monkey-patch of `agent._log_step` — always restored in `finally` block to prevent stale closures on repeated analysis

### PDF Export — ReportLab
- **Package:** `reportlab >= 4.0.0`
- **Style:** near-black headings, teal (#0891b2) subheadings, navy (#1E3A5F) table headers, metadata table, skill timing breakdown
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

`DOCAGENT_*` variables read by the config loader: `MAX_FILE_MB`, `LOG_LEVEL`, `LOG_FILE`, `DEBUG`, `GROQ_ENABLED`, `GROQ_URL`, `GROQ_MODEL`, `GROQ_TIMEOUT`. Read elsewhere: `HOSTED` (`is_hosted()` in `utils/config.py`), `EXTRACTION_VERIFY` (structured extraction) and `EXTRACT_GENERAL` (the planner).

---

## Logging

### Rich + File Logging (`utils/logger.py`)
- **Console:** `rich.logging.RichHandler` with coloured output, traceback highlighting, `[HH:MM:SS]` timestamps
- **File:** `logs/docagent.log`, a plain `FileHandler` (not rotated); DEBUG level always (even if console is INFO)
- **Namespace:** All loggers under `docagent.*` hierarchy (e.g., `docagent.skill.pdf_reader`, `docagent.document_agent`)

---

## Testing

- **Framework:** `pytest >= 7.4.0`, `pytest-mock >= 3.11.0`
- **Unit suite:** `pytest tests/`, about 600 tests in roughly five minutes; synthetic fixtures and stubbed LLM replies
- **End to end:** `python tests/e2e/e2e.py all`, eight stages on the real sample files against the live API
- **Evals:** `tests/e2e/rag_eval/run_eval.py` (retrieval, no API calls) and `tests/e2e/extraction_eval/run_eval.py` (structured extraction, live API)

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
| Tables | paddlepaddle | >= 2.6.2, not on Linux |
| Tables | paddleocr | >= 2.6.0.3, not on Linux |
| Excel | openpyxl | >= 3.1.2 |
| Excel | pandas | >= 2.0.0, < 3 |
| Text | ftfy | >= 6.1.3 |
| LLM | openai | >= 1.12.0 |
| Audio | yt-dlp | >= 2026.8.19 |
| Audio | imageio-ffmpeg | >= 0.4.9 |
| Retrieval | sentence-transformers | == 5.4.0 |
| Retrieval | torch | == 2.7.1+cpu, Linux only |
| Export | reportlab | >= 4.0.0 |
| Config | PyYAML | >= 6.0.1 |
| UI | streamlit | == 1.63.0 |
| Infra | python-dotenv | >= 1.0.0 |
| HTTP | requests | >= 2.31.0 |
| Logging | rich | >= 13.7.0 |
| Testing | pytest | >= 7.4.0 |
| Testing | pytest-mock | >= 3.11.0 |

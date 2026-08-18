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

`utils/llm_client.py` wraps the OpenAI SDK pointed at Groq Cloud (`openai/gpt-oss-120b`). Supports multi-key round-robin for rate-limit resilience. Default model, temperature (0.15), and timeout (180s) are in `configs/default.yaml`.

### Token budgets and reasoning models

`openai/gpt-oss-120b` reasons before it answers, and **`max_tokens` covers the
reasoning as well as the reply**. A budget sized against the expected answer can
therefore be consumed before the answer begins: the API returns
`finish_reason: "length"` with **empty content**, which `chat()` reports as
`None` — indistinguishable, until recently, from the model simply failing.

That is not hypothetical. `_llm_classify` asked for 80 tokens for a 25-token
JSON reply, got `reasoning_tokens: 78`, and LLM classification was silently off
for an entire model migration. Domain fell back to `General`, which gates
`structure_recognition`, and the questionnaire blend lost its LLM term.

- **Never size a budget from the expected reply.** Measured on a real 20-page
  document: classification needs ~16 content tokens but ~200 total; the
  summarisation map step ~943; the reduce step ~2604.
- **`chat()` now warns** on `finish_reason: "length"`, distinguishing "no
  content, budget too small" from "truncated partial answer", and exposes
  `_last_finish_reason`. It still returns `Optional[str]`: four call sites treat
  falsy as a hard error and three use it as a deliberate fallback, so raising
  would break the fallbacks.
- **It is ~4x slower per call** than the retired 70b — summaries went from ~2s
  to 12-18s — and free-tier keys have an 8,000 TPM limit a large document now
  brushes. Use several keys via `GROQ_API_KEYS`.

### Hybrid Classification

`DocumentClassifierSkill` uses two phases:
1. **Heuristic**: 20+ regex patterns with weighted scoring
2. **LLM disambiguation**: Only invoked when heuristic score is in borderline range (0.10–0.70); blended 60% LLM / 40% heuristic

### PDF Parsing Fallback Chain

`PDFReaderSkill` escalates: pdfplumber → PyMuPDF → Tesseract OCR (with Gaussian equalization + adaptive thresholding for scanned docs).

### Summarization Strategy

`SummarizationSkill` uses map-reduce: section-aware chunking → per-chunk bullet extraction → LLM synthesis. Falls back to heading-boosted extractive scoring if LLM is unavailable.

### Document Chat Retrieval

`DocumentChatSkill.score_chunks()` ranks chunks by **embedding similarity**
(`all-MiniLM-L6-v2` via `sentence-transformers`, local CPU, 384 dims), scored as
a numpy cosine product. There is no vector database — at tens of chunks per
document a dot product is exact and instant.

- **Keyword overlap is the fallback**, used whenever the model cannot be
  imported or loaded. Every query logs which path served it.
- **The model loads lazily**, on first use, never at import. The first load
  costs ~30–55s including an 87 MB download; the UI shows a spinner during it.
- **Vectors persist** in `history.db` (`content_embeddings_b64`) with the model
  name, so history reloads do not re-embed. Vectors from a different model are
  ignored rather than compared.
- **Retrieval ranks over passages, not pages.** `utils/chunking.sub_chunk()`
  splits each page or sheet into overlapping ~100-word passages for the
  retrieval index only. `ParsedDocument.chunks` is untouched, so summarisation
  still sees whole pages. Every passage keeps its `page_or_sheet`, so citations
  still resolve to "Page 3".
- `_select_chunks()` takes the top 3 plus first/last **anchors** and caps at
  `_MAX_CONTEXT_CHARS`. Its three slots are three distinct **sources**, not
  three chunks — with passages, several fragments of one page would otherwise
  sweep the budget and displace a page that is genuinely needed.

**Chat spans one document or the whole corpus.** Mode is derived from the
chunks, never from a caller flag: single-document chunks carry no `document`
key. Across a corpus the selector keys on `(document, page)` — a bare page label
conflates files and made one passage unreachable at any rank — drops anchors,
and spends 6 slots. Labels become `report.pdf, Page 3`, and `used_sources`
carries document and page as separate fields.

Measured on 13 cross-document cases: retrieval 11/13 → **12/13**, citations
naming their document 0/13 → **13/13**, unresolvable citations 11/13 → **0/13**.
Cross-corpus retrieval is **meaningfully worse than single-document** — mean
rank 2.15 against 1.18, ranked first 54% against 85% — and one case answers
confidently from the wrong document with a correct citation. Both are
quantified in `docs/multi-document-chat.md`, which is the thing to read before
raising `MAX_CORPUS_DOCUMENTS` (25).

Retrieval is measured, not assumed. On the current eval set (33 meaningful
cases): **33/33 retrieved, 28/33 ranked first**, mean rank 1.18. Page-level
chunking scores the same 33/33 but 25/33 ranked first — sub-chunking improves
*ranking*, not recall. The keyword fallback scores 27/33 with mean rank 3.09.
Score it with `python tests/e2e/rag_eval/run_eval.py` (no API calls), or
`--keyword` for the fallback alone. Full method, per-case flips and costs in
`docs/retrieval-sub-chunking.md`; the earlier embeddings-vs-keyword experiment
is in `tests/e2e/rag_eval/RESULTS.md`.

**Which figures depend on the LLM.** Every retrieval number above is produced
without an API call — verified by scoring identically with
`DOCAGENT_GROQ_ENABLED=false` — so it is a property of the embedding model and
the chunking, and the Groq model can change without invalidating it. The
figures that *do* depend on the LLM are answer and citation correctness
(`--with-answers`), and those name their model: **33/33 answers and 27/27
correct prose citations, 0 wrong, on `openai/gpt-oss-120b`**. The same
citation figure on the previous default, `llama-3.3-70b-versatile`, was also
27/27 before Groq retired it on 17 June 2026.

**If you change the model OR the chunking**, stored vectors become
incomparable — a vector describes specific text, so re-splitting invalidates it
exactly as changing the model does. `MODEL_NAME` and `chunk_scheme` are both
recorded per row and checked on load, so stale rows are skipped and fall back to
keyword until re-analysed. Re-run the eval after either change.

**The embedding model truncates at 256 tokens**, silently. Anything longer is
cut before embedding with no warning. This is the main reason passages exist:
5 of the 8 pages in `sample_dense_manual.pdf` exceeded it, so page-level
embedding was ranking most of that document from a lossy copy.

### UI

`ui/app.py` is the Streamlit entry point. Results display logic lives in `ui/components/results_view.py`. Custom glassmorphism styles are in `ui/styles/custom.css`.

### Configuration

`utils/config.py` defines typed dataclasses (`AppConfig`, `GroqConfig`, `PDFConfig`, etc.) loaded from `configs/default.yaml`. All values can be overridden by env vars.

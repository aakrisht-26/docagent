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
- **Summary length presets carry a reasoning allowance.** The numbers in
  `_LENGTH_CONFIGS` are CONTENT budgets; `_with_reasoning_room()` adds 1024 at
  the call so a preset delivers the length it names. Reasoning does not scale
  with input (a 5.5x range of prompt sizes moved it from 45 to 42 tokens) but
  does scale with the directive (Standard 34, Exhaustive 902), and it is noisy
  run to run — hence a flat constant clearing the worst observation rather than
  a fitted curve.
- **Groq refuses, it does not truncate** — and the limit is a ROLLING WINDOW,
  not a per-request size cap. prompt + `max_tokens` over what remains of the
  per-minute allowance returns 413. The same key accepted a 34,072-token
  request and refused an 8,600-token one minutes later, and two measurement
  sessions disagreed about which keys refuse what. A refusal describes that key
  at that instant, not the request.

  **So no preset value is guaranteed to be accepted, and none is claimed to
  be.** What makes a refusal survivable is that `_run_with_rotation` now
  rotates to another key on a 413 instead of returning None; previously one
  unlucky key selection dropped the whole summary to extractive in silence.
  When every key refuses, the give-up log says it was the tier declining the
  size rather than the model failing, `_last_failure` carries
  `FAILURE_RATE_LIMIT`, and the user gets a warning saying a shorter length
  would fit.

  "Exhaustive" was lowered 8000 → **6000**, not for reliability but because it
  never used the room: measured across three documents its longest output was
  3783 tokens. Only Standard comes near its ceiling (3722 against 4024). Above
  roughly 4000, length is set by the DIRECTIVE and not by `max_tokens` —
  which is also why the four presets are not reliably ordered by length.
  Concise is clearly distinct at 5.5–7.7x; Standard, Detailed and Exhaustive
  overlap within run-to-run variance (Standard measured 15830 chars in one run
  and 13232 in another). Re-spacing the numbers cannot fix an ordering the
  numbers do not control.

### Hybrid Classification

`DocumentClassifierSkill` uses two phases:
1. **Heuristic**: 19 regex signals with weighted scoring, normalised by their
   total (1.81). Silent on anything that is not a textbook form — all seven e2e
   fixtures score 0.000 — but not dead: a form with Likert scales, underscored
   fields and a signature block fires 15 of 19 and reaches 0.862, which crosses
   the `> 0.85` gate and bypasses the LLM entirely.
2. **LLM disambiguation**: blended `0.7 * p(questionnaire) + 0.3 * heuristic`.

**`classification_confidence` is P(QUESTIONNAIRE), not confidence in the
verdict.** A confidently classified normal document scores near **zero**: 0.02
means the classifier was 98% sure it was *not* a questionnaire. Six of seven
fixtures are normal documents, so the raw field reads backwards on almost
everything, and it was reported as a suspected defect in three consecutive
sessions before being traced. The logic was correct each time.

Use `core.models.confidence_in_verdict(score, doc_type)` for anything a human
reads — UI, markdown export, logs, the e2e harness. The stored field stays raw:
it is persisted in `history.db`, asserted on in tests, and `doc_type` derives
from it via the 0.4 threshold, so changing its meaning would be a data
migration rather than a display fix.

**`confidence_in_verdict` IS PARTIAL — it returns `Optional[float]`, and None
means there is no verdict.** It is defined on `questionnaire` and
`normal_document` (`CLASSIFIED_DOC_TYPES`) and undefined on everything else.
Handle the None; do not coerce it to 0.0, which reads as a confident negative
rather than an absence.

The first version was total, treating anything that was not `questionnaire` as
`normal_document`. So `unknown` — the doc_type a failed parse or download
carries — mapped to 1 - 0.0 = **100%, rendered green**, and a failed YouTube
download displayed "Normal Document · Domain: General · 100% confidence" above
"0 words · 0 pages". The maths was right at every step; nothing asked whether
there had been any content to classify.

**Three layers independently invented that verdict**, and all three are fixed:
the confidence function (above), `_dict_to_pipeline_result`'s reload default,
and the results banner's `else` branch. Fixing any one alone leaves the other
two lying. `ui/components/results_view._banner_for()` and
`confidence_in_verdict` normalise against the same constant so the label and
the number cannot disagree.

**The classifier's own empty-text branch reports `unknown`, and no end-to-end
test can reach it** — the agent gates on `ParsedDocument.is_empty` before step
3. Verified by mutation rather than assumed: reverting that branch leaves the
`empty` e2e stage green, while reverting `confidence_in_verdict` turns it red.
It is covered by `tests/test_empty_document.py`, which mutation-tests all four
layers; the end-to-end case is `tests/e2e/e2e.py::empty` over
`sample_blank.pdf`.

Two shipped tests had asserted the old behaviour —
`confidence_in_verdict(0.0, "") == 1.0` under the heading of "tolerating stored
shapes", and `test_empty_text_returns_normal`. Both were the bug written down.

**The 0.70 ceiling is intended.** With a silent heuristic the blend cannot
exceed 0.70, so the top of the range is unreachable. That is not a bug to
normalise away: it makes the LLM conviction needed to reach the 0.4 threshold
slide with how much the heuristic corroborates it — 0.571 uncorroborated, 0.486
at weak support, 0.211 at strong. Renormalising would drop the uncorroborated
bar to 0.400, a 30% cut in required conviction for nothing. Observed
p(questionnaire) is 0.01–0.04 on normal documents and 0.96 on the questionnaire,
against a 0.571 boundary — the headroom is enormous and the ceiling costs
nothing.

### The YouTube path can fail for reasons outside this repo

YouTube answers some downloads with "Sign in to confirm you're not a bot". It
is keyed on **IP reputation**, not on the request: observed on a home
connection minutes after the same video downloaded fine, and far more frequent
from shared egress (Streamlit Community Cloud, CI, VPNs, NAT). Documented as a
known limitation in README and DEPLOYMENT.md.

**The cause used to be destroyed twice on its way out.** The skill replaced
yt-dlp's text with `"Failed to download audio from YouTube"`, and
`run_youtube` replaced that with `"YouTube audio extraction failed"` — so a bot
check, a deleted video, a missing ffmpeg and a genuine bug all reached the user
and the harness as the same sentence. Both now carry the reason through.

`utils/youtube_errors.classify_download_error()` sorts it into **BLOCKED**
(transient refusal of this client), **UNAVAILABLE** (the video: removed,
private, age-gated, geo-blocked), **SETUP** (ffmpeg missing) or **UNKNOWN**.

**UNKNOWN is the default, and that asymmetry is the whole design.** This
classifier decides whether the e2e harness treats a red `youtube` stage as a
real failure. Guessing "probably external" on unrecognised text would turn
every genuine YouTube bug into a skipped stage. A missed BLOCKED signature only
costs a confusing red run; a false BLOCKED hides a bug silently.

**The harness has a third outcome: `BLOCKED`, exit code 3.** Not PASS (the
stage verified nothing) and not FAIL (the code is fine). An ambiguous red is
its own hazard — after a few runs where red meant "the bot check again", a real
regression gets waved through. `_blocked_reason()` is narrow in three ways:
only a failure at the **download step** qualifies, only allowlisted signatures
qualify, and a **removed video does NOT** (that means the fixture needs
replacing, which is work for this repo and stays red). A genuine failure
anywhere outranks a block. All of it is mutation-tested in
`tests/test_youtube_blocked.py`.

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

**The corpus prompt forbids substitution.** Not permission to decline — the
model already declines 39/39 across three runs when nothing is on topic, and
always did. What it did instead was answer a headcount question by summing an
unrelated workbook's departmental figures and citing that workbook correctly.
The prompt therefore prohibits computing a figure from numbers not presented as
a total, and offering a similar fact about a different depot, tier, period or
organisation, while explicitly allowing a multi-part question to be part
answered and part declined. Fabrication on the worst case fell from 5/5 to
2 in 25 trials; its answerable half survived 22/22. Score it with
`run_eval.py --multi --with-answers`, which reports the 13 no-answer cases
alongside the answerable ones.

**No similarity threshold can do this job**, and it was measured rather than
assumed: an unanswerable question about a depot the corpus does not cover scores
0.7784, higher than 11 of the 12 answerable cross-document cases, while six of
33 correct single-document answers score below the highest unanswerable
question. The cause of the remaining failure is **competition, not dilution and
not the slot budget**: a page entirely *about* the question's subject outranks
the page that *states* the answer. Both alternatives were implemented or
measured and neither held — per-part retrieval does not recover the case, and a
purpose-built fixture found heterogeneity correlating with rank at −0.084 with
both its losses on the page with the fewest topics. **Re-chunking cannot address
this**, so do not spend a session trying. See `docs/dilution-probe.md` and
`docs/multi-document-chat.md`.

Cross-corpus retrieval is **meaningfully worse than single-document** — required
sources lead the ranking 9/13 (69%) against 32/33 (97%) — and one case answers
confidently from the wrong document with a correct citation. Both are
quantified in `docs/multi-document-chat.md`, which is the thing to read before
raising `MAX_CORPUS_DOCUMENTS` (25).

Retrieval is measured, not assumed. On the current eval set (33 meaningful
cases): **33/33 retrieved, 32/33 required sources leading the ranking**
(worst-rank diagnostic 28/33, mean worst rank 1.18). Page-level
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

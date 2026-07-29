# Session Log — overnight-hardening

Branch: `overnight-hardening` · Baseline tag: `working-baseline` (`c842d84`)

> **End-of-session report lives at the top of this file.** It is filled in when
> the queue completes; until then the per-task log below is the source of truth.

---

## End of Session Report

_Pending — filled in when the task queue completes._

---

## Setup

**Environment verified before any work started.**

| Binary | Version | Location |
|---|---|---|
| ffmpeg | 8.1.2-full_build | `%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_…\ffmpeg-8.1.2-full_build\bin` |
| ffprobe | 8.1.2-full_build | same directory |
| tesseract | 5.4.0.20240606 | `C:\Program Files\Tesseract-OCR` (langs: `eng`, `osd`) |

Notes:

- With ffmpeg now on PATH, `_find_ffmpeg()` takes the **PATH branch** rather than
  the imageio-ffmpeg fallback. That branch had never executed on this machine
  before, so the YouTube stage was re-verified specifically. It passes.
- The agent's shell inherited a stale PATH from before the install; PATH is
  refreshed from the registry per command. No install was performed by the agent.
- **Fragility for DEPENDENCIES.md:** the ffmpeg PATH entry embeds the version
  (`ffmpeg-8.1.2-full_build`). The next `winget upgrade` replaces that folder and
  silently breaks the PATH entry. Fix later by copying/symlinking the three exes
  into `%LOCALAPPDATA%\Microsoft\WinGet\Links`.

Baseline confirmed before branching: all 5 e2e stages pass, tag `working-baseline`
created at `c842d84` and pushed.

---

## Task Log

### Task 1 — Move e2e harness into the repo ✅

**Committed:** see `feat(tests): move e2e harness and fixtures into repo`

Moved from the agent scratchpad into `tests/e2e/`:

- `tests/e2e/e2e.py` — absolute scratchpad paths replaced with repo-relative
  resolution (`ROOT = Path(__file__).resolve().parents[1]`), so it runs from any
  cwd in a fresh clone. Added an `all` mode that runs every stage and prints a
  pass/fail matrix, and a clear error when a fixture is missing.
- `tests/e2e/make_samples.py` — regenerates the PDF and Excel fixtures
  cross-platform; the WAV is Windows-SAPI-only and is skipped with a notice on
  other platforms (the committed copy is used instead).
- `tests/e2e/samples/` — `sample_report.pdf` (3.3 KB), `sample_sales.xlsx`
  (5.7 KB), `sample_audio.wav` (1.3 MB).

Two things had to be fixed for a fresh clone to actually work:

1. **`.gitignore`** excluded the fixtures via blanket `*.pdf` / `*.xlsx` rules.
   Added a targeted `!tests/e2e/samples/*` negation. The blanket rules still
   apply everywhere else — verified that a stray `scratch_check.pdf` at the repo
   root is still ignored, so user documents cannot be committed by accident.
   (A directory-level negation `!tests/e2e/samples/` does *not* work here: the
   files are matched by `*.pdf` directly, so the negation must match the files.)
2. **`.gitattributes` did not exist** and `core.autocrlf=true`. Git classified
   `sample_report.pdf` as text and warned it would convert LF→CRLF inside the
   binary, which would corrupt the fixture on clone. Added `.gitattributes`
   marking the binary fixture formats explicitly.

**Verification:** `python tests/e2e/e2e.py all` → exit 0, all 5 stages PASS
(pdf, excel, audio, youtube, rag), run from the in-repo location.

Additionally proved the acceptance criterion rather than assuming it: cloned the
pushed branch to a clean directory with **no `.env`**, injected the API key by
environment variable only, and ran `python tests/e2e/e2e.py all` inside the
clone → exit 0, all 5 PASS. Fixture SHA-256 hashes match between the committed
blobs and the working files, confirming the `.gitattributes` fix.

---

### Task 2 — Make a fresh clone runnable ✅

**Committed:** see `docs: make a fresh clone runnable`

Three parts:

**`.env.example`** — new file documenting all 9 variables the code actually
reads. Enumerated by grepping for both direct reads (`os.getenv`,
`os.environ.get`) and indirect ones (`DOCAGENT_GROQ_ENABLED` is read through the
`_env_bool` helper, so a naive grep misses it). Placeholder values only; no real
key appears anywhere. The multiline `GROQ_API_KEYS` form is shown **quoted**,
with an explanation of the silent single-key truncation that happens without
quotes.

**`.streamlit/config.toml`** — removed the `.gitignore` rule that excluded the
whole `.streamlit/` directory and committed the theme config. It contains only
theme keys (`base`, `font`, and four colours) — no secrets. Replaced the blanket
rule with a targeted `.streamlit/secrets.toml` ignore, so the one file in that
directory that *would* hold credentials stays untrackable. Verified: a planted
`secrets.toml` is still ignored, and `.env` is still ignored and unstaged.

**`README.md`** — replaced the Getting Started section with five ordered
fresh-clone steps: clone/install, API key, system binaries, verify, run. The
binaries now have their own table stating exactly what breaks without each
(ffmpeg → YouTube fails in postprocessing after downloading, local audio fails
to convert, file uploads unaffected; tesseract → scanned PDFs with no text layer
produce an empty document, text-layer PDFs unaffected). Added the missing
`winget` commands for Windows, a PATH-propagation warning, the verification
command, and an env-var reference table expanded from 4 rows to all 9.

**Verification:** `python tests/e2e/e2e.py all` → exit 0, all 5 stages PASS.

---

### Task 3 — Exercise the OCR fallback tier ✅

**Committed:** see `test(e2e): add scanned-PDF stage exercising the OCR tier`

**The OCR tier was not broken — it had simply never been given an input that
reached it.** Every PDF tested previously had a text layer, so pdfplumber
returned text and `doc.is_empty` was never true. No code fix was needed.

Added `build_scanned_pdf()` to `make_samples.py`: rasterises `sample_report.pdf`
to an image-only PDF with no text layer, applying deterministic (fixed-seed)
scan artefacts so the OCR preprocessing is genuinely exercised — 150 DPI, a 1.2°
skew inside the 0.5–15° window the deskew step handles, a left-edge brightness
gradient for the adaptive thresholding to work against, and light sensor noise.
JPEG-encoded, as a real scanner would produce.

Confirmed the fixture has no text layer: pdfplumber 0 chars, PyMuPDF 0 chars.

Confirmed the tier fires: `engine='ocr_tesseract'`, `ocr=True`, 178 words across
2 pages, resolving `C:\Program Files\Tesseract-OCR\tesseract.exe` through the
existing Windows path discovery in `PDFReaderSkill.__init__`.

Added `scanned` as the 6th e2e stage. It does **not** just check `success` — it
asserts `engine == "ocr_tesseract"` and a minimum word count, so the stage fails
loudly if the fixture is ever regenerated with a text layer or if Tesseract goes
missing from PATH. Without that assertion the stage would pass while testing
nothing.

One fixture correction during the task: the first version used heavier noise
(σ=4.0, 0.82 gradient), and Tesseract misread "14 customer sites" as "414",
which propagated into the LLM summary. A fixture that bakes in a factual error
is a poor regression baseline, so the noise was reduced to σ=2.0 / 0.88 — still
representative of a real scan, and the figure now reads correctly. Also switched
PNG → JPEG, cutting the fixture from 4.1 MB to 296 KB.

**Verification:** `python tests/e2e/e2e.py all` → exit 0, all **6** stages PASS.
`pytest tests/ -q` → 41 passed.

---

### Task 4 — Filter placeholder and malformed API keys ✅

**Committed:** see `fix(config): filter placeholder and malformed Groq keys`

Reproduced first: on this machine `resolve_groq_api_keys()` returned **9** keys,
with `PASTE_YOUR_GROQ_API_KEY_HERE` sitting at **position 2**. It was being sent
to the Groq API, guaranteeing a 401 and consuming a rotation slot on every
rate-limit failover.

Filtering added in `resolve_groq_api_keys()` (`utils/config.py`) — the single
resolution point used by `DocumentAgent.__init__`, `LLMClient.from_config` and
`AppConfig.validate`. A candidate is rejected when it contains a placeholder
marker (`PASTE`, `REPLACE_ME`, `YOUR_`, `_HERE`, `CHANGE_ME`, `DUMMY`,
case-insensitive), is under 20 chars, or contains whitespace. Markers were
chosen conservatively — each contains an underscore or is a full word — so they
cannot collide with a real Groq key, which is `gsk_` plus alphanumerics.

Rejections log at WARNING with the reason and character count but **never the
value**, since a rejected candidate may still be a real secret.

Result: 9 keys → 8, placeholder gone. Verified against a table of cases
including both shipped YAML placeholders, the `.env.example` samples, the README
stand-in, a too-short value, and a whitespace-containing value — all rejected;
a realistic 56-char key accepted.

**`CODEBASE_GUIDE.md`** — the section claimed `LLMClient` already filtered this
and showed a line of code doing so. **That code did not exist anywhere in the
repo.** Replaced with an accurate description of where filtering actually
happens and what the rules are, marked as a correction.

While correcting it I checked whether `AudioReaderSkill._transcribe_audio()` was
also affected — it resolves keys itself rather than calling
`resolve_groq_api_keys()`. It is safe in the production path, because
`DocumentAgent` passes it already-filtered keys, but the placeholder could still
reach it if the skill were instantiated directly with raw YAML config on a
machine with no `.env`. Documented as a known residual gap rather than
overclaimed; it is Task 12's job to consolidate the call sites, so fixing it
here would have been duplicated work.

**Verification:** all 6 e2e stages PASS (exit 0); `pytest tests/ -q` → 41 passed.

---

### Task 5 — Quote the multiline `GROQ_API_KEYS` in the local `.env` ✅

**Committed:** see `fix(config): make the .env key parser agree with dotenv`
(the `.env` edit itself is local only — that file is gitignored and was never
committed)

The `.env` was rewritten programmatically so key material was never printed, and
the original was backed up outside the repo first. 8 keys, all 56 chars, now in
the quoted multiline form that `.env.example` documents. The
`Python-dotenv could not parse statement starting at line 7` warning is gone.

**Quoting alone made things worse, which is exactly what the task asked me to
check.** `_read_multiline_env_keys()` did not strip the surrounding quotes, so it
returned the first key with a leading `"` and the last with a trailing `"`.
Resolution went from 9 keys to **10** — the 8 good ones from dotenv plus 2
quote-corrupted duplicates, each of which would cost a 401 during rotation. The
two parsers disagreed more after quoting than before.

Three fixes in `_read_multiline_env_keys()`:

1. Strip a matched pair of surrounding quotes from the assembled value, then any
   stray quote from individual keys.
2. Match the header with a regex tolerating whitespace around `=`
   (`GROQ_API_KEYS = "..."` previously did not match at all, since the old code
   used `startswith("GROQ_API_KEYS=")`).
3. Read with `utf-8-sig` instead of `utf-8`. Found accidentally: my first test
   harness wrote the file with PowerShell's `Set-Content -Encoding utf8`, which
   emits a BOM, and every format returned **zero keys** because the BOM blocked
   the header match. Notepad does the same thing by default, so a user editing
   `.env` on Windows could silently lose all keys.

Result: dotenv and `_read_multiline_env_keys()` now return **identical lists —
same 8 keys, same order**, all `gsk_`-prefixed and 56 chars.
`resolve_groq_api_keys()` returns exactly 8.

Verified against 8 `.env` layouts with fake keys (unquoted multiline, quoted
multiline, single line, single line quoted, spaced equals, single quotes, UTF-8
BOM, trailing comment) — all 8 parse correctly. The real `.env` was restored in a
`finally` block and re-verified afterwards.

**Verification:** all 6 e2e stages PASS (exit 0); `pytest tests/ -q` → 41 passed;
no dotenv warning anywhere in the run.

---

### Task 6 — Per-stage timing logging at pipeline boundaries ✅

**Committed:** see `feat(logging): stage numbering, cumulative timing, skip visibility`

Logging was already stdlib (`logging`, with an optional Rich handler) and
`_log_step` already timed each skill, so this was additive rather than a rewrite.

Added to `BaseAgent`: `_begin_pipeline()`, `_elapsed_ms()`, `_stage_prefix()`,
`_log_stage_skipped()` and `_log_pipeline_summary()`. `DocumentAgent` declares a
`stage_numbers` map with the canonical numbers from the frozen pipeline
(1 parse → 3.5 structure → 5.5 structured extraction). Output now carries the
stage number, per-stage duration, cumulative elapsed, an explicit line for
planner-skipped stages, and a closing breakdown with percentage shares.

**`_log_step`'s signature was deliberately left untouched**, and no new call
sites were added to it, because `ui/app.py` wraps that method positionally to
drive the progress bar — every call advances it one step. Skipped stages go
through the separate `_log_stage_skipped()` so the bar is not advanced for work
that never ran. A comment at the definition records this constraint. Verified the
signature still matches what `app.py` expects.

**The real find was that these logs were invisible in the harness.** Stages 3.5,
4 and 5.5 produced no output at all, including the pre-existing `_log_step`
lines, while their timings still appeared in `skill_timings`.

Root cause: **`import paddle` resets the root logger level from INFO to
WARNING** as an import side effect — confirmed directly
(`root.level BEFORE: INFO` → `root.level AFTER: WARNING`). paddle is pulled in by
`StructureRecognitionSkill._detect_gpu()`, which runs at stage 3.5, precisely
where the output stopped. The harness configured logging with `basicConfig()`
only, so the `docagent` loggers had no handlers of their own, propagated to root,
and inherited the new WARNING level. Every INFO record after that point was
dropped silently.

The app was never affected: `ui/app.py` calls `setup_logging()`, which gives
`docagent` its own handlers and sets `propagate=False`. The fix is for the
harness to do the same. Documented the trap in `utils/logger.py`'s module
docstring so the next entry point does not repeat it.

Two wrong hypotheses were checked and discarded first (that `paddleocr`'s import
broke logging, and that something called `logging.disable()` — the disable level
was 0 throughout).

Incidentally surfaced by the new breakdown: `structure_recognition` consumes
**19.7%** of PDF wall clock while doing nothing on a CUDA-less machine. That is
Task 11.

**Verification:** all 6 e2e stages PASS (exit 0); `pytest tests/ -q` → 41 passed;
Streamlit boots clean (health 200, empty stderr).

<!-- task-6-end -->

---

### Task 7 — Stop the UI swallowing pipeline errors ✅

**Committed:** see `fix(ui): log tracebacks and surface error type; add debug flag`

The handler at `ui/app.py` is kept (removing it would crash the whole Streamlit
script on any per-file failure) but it no longer hides anything:

- `logger.exception(...)` replaces `logger.error(..., exc_info=True)` and now runs
  **first**, so the traceback reaches `logs/docagent.log` even if the subsequent
  UI render itself fails.
- The message now includes the exception **type**: `Processing failed —
  KeyError: 'simulated_missing_key'`. This matters more than it looks — a bare
  `str(exc)` is empty or cryptic for exactly the exceptions you most want to
  identify (`IndexError` stringifies to `""`, `KeyError('x')` to `"'x'"`), so the
  old message could read `Processing failed:` with nothing after it.
- The full traceback is available in the UI behind a "Show traceback" expander.
- A `DOCAGENT_DEBUG` flag re-raises instead of catching, so Streamlit shows its
  own traceback and debuggers can break on the failure.

New config: `AppConfig.debug`, from `DOCAGENT_DEBUG` env or `app.debug` in
`configs/default.yaml`, defaulting to false. Documented in `.env.example` and the
README env table.

**Both branches were exercised, not just read.** A harness forced a `KeyError`
inside the guarded block and ran it twice:

- `DOCAGENT_DEBUG=false` → exception caught, traceback logged, `st.error` shows
  `Processing failed — KeyError: 'simulated_missing_key'`, caption and a
  391-char traceback rendered.
- `DOCAGENT_DEBUG=true` → exception propagates out of `_run_pipeline`, after
  still being logged.

**Verification:** all 6 e2e stages PASS (exit 0); `pytest tests/ -q` → 41 passed;
Streamlit boots clean (health 200, empty stderr).

---

### Task 8 — DEPENDENCIES.md ✅ (documentation only)

**Committed:** see `docs: document OpenCV collision and pandas version drift`

**No pip command was run.** Confirmed after writing: all three OpenCV
distributions are still installed and `cv2` still resolves to 4.10.0, unchanged.

New `DEPENDENCIES.md` covering:

**OpenCV, three-way collision.** `opencv-contrib-python` 4.10.0.84,
`opencv-python` 4.13.0.92 and `opencv-python-headless` 4.8.1.78 are all
installed, and all three ship the same top-level `cv2` package — verified via
`importlib.metadata`, they own 135 / 92 / 90 files under `cv2/` respectively and
overwrite each other. `requirements.txt` pins
`opencv-python-headless==4.8.1.78`, but **4.10.0 is what actually loads**, from a
distribution not listed in `requirements.txt` at all. The pin is decorative.

Established which variant is genuinely needed rather than guessing: enumerated
every `cv2.*` symbol used anywhere in the codebase (18 of them), confirmed there
are **no GUI calls** and **no contrib-only modules**. So `opencv-python-headless`
alone is correct — matching what `requirements.txt` already declares. The
documented resolution uninstalls all three before reinstalling, because removing
two leaves the survivor with files deleted by the others' uninstall.

Flagged the specific risk: `minAreaRect`'s angle convention changed across the
4.x line and the OCR deskew step branches on that angle, so a silent OpenCV swap
can change OCR output with no error.

**pandas ahead of its accelerators.** pandas 3.0.3 with `numexpr` 2.8.7 (wants
≥2.10.2) and `Bottleneck` 1.3.7 (wants ≥1.4.2) — two UserWarnings on every
Excel/CSV run. Noted that the more serious half is `pandas>=2.0.0` in
`requirements.txt` being unbounded across a major version, so a clean install can
resolve anywhere from 2.0 to 3.x. Gave both resolutions (upgrade accelerators;
bound the pandas range) and noted nothing in the code depends on pandas-3-only
behaviour, so either bound works.

**Also recorded:** `python-dotenv` 0.21.0 installed against a `>=1.0.0` pin — the
environment does not satisfy its own requirements file; the ffmpeg PATH entry
embedding `ffmpeg-8.1.2-full_build` and the WinGet Links fix for it (your note);
and the CPython 3.13 `.pyc` files under a 3.12.7 interpreter.

**Verification:** all 6 e2e stages PASS (exit 0); `pytest tests/ -q` → 41 passed;
dependency state confirmed untouched.

---

### Task 9 — Broken `docagent` console script ✅

**Decision: drop the claim** (your call, asked and answered).

**Committed:** see `fix(packaging): drop the non-functional docagent console script`

Two independent reasons the command could never work, both verified:

1. `find_packages(exclude=["tests*", "ui*"])` resolves to
   `['agents', 'core', 'skills', 'utils']` — `ui` is not packaged, so a
   non-editable `pip install .` produced a `docagent` command that raised
   `ModuleNotFoundError: ui`. `pip install -e .` masked this, because the source
   tree stays on `sys.path`, which is presumably why it went unnoticed.
2. Even with `ui` packaged it still would not work. `ui/app.py` calls
   `st.set_page_config` at import and needs the Streamlit runtime; calling
   `main()` directly runs the script in bare mode and exits without serving.

So "add `ui` to packages" was never the fix, which is what made this a real
decision rather than a typo.

Removed `entry_points` from `setup.py` and the "enables `docagent` CLI entry
point" claim from `README.md` and `CLAUDE.md`, replacing each with the accurate
statement that `pip install -e .` installs the library packages and the app is
started with `streamlit run ui/app.py`. `setup.py`'s docstring records why the
entry point was removed, so it does not get re-added.

Verified `setup.py` still parses and produces valid metadata
(`python setup.py --name --version` → `docagent` / `1.0.0`), and grepped for
leftover CLI claims — none remain.

**Verification:** all 6 e2e stages PASS (exit 0); `pytest tests/ -q` → 41 passed.

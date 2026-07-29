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

<!-- task-9-end -->

---

### Task 10 — Make the upload limits agree, and reject before upload ✅

**Committed:** see `fix(ui): align Streamlit upload limit with app config`

Before: Streamlit's dropzone advertised **200 MB** (its `server.maxUploadSize`
default) while `validate_file` rejected anything over **50 MB** — *after* the
whole file had already been transferred. A 180 MB upload would complete, then be
thrown away.

- `.streamlit/config.toml` now sets `server.maxUploadSize = 50`, matching
  `app.max_file_size_mb`. Streamlit enforces this **in the browser**, so the file
  is refused before transfer rather than after. This is the "reject before
  upload" half, and it only became possible because Task 2 started committing
  `.streamlit/config.toml`.
- The uploader help text now derives from `_cfg.max_file_size_mb` instead of a
  hardcoded "max 50 MB".
- Added a startup drift check: `ui/app.py` compares
  `st.get_option("server.maxUploadSize")` against `_cfg.max_file_size_mb` and
  raises a sidebar configuration issue if they differ. The two values live in
  different files and `DOCAGENT_MAX_FILE_MB` can move one without the other, so
  the mismatch is now surfaced instead of being discovered by a slow upload.

`validate_file` is intentionally left in place as the server-side backstop.

**Both directions verified in the running app**, not just read:

- Default (`config.toml` = 50): dropzone renders "Limit 50MB per file", no
  configuration issue shown.
- Forced mismatch (`--server.maxUploadSize=200`): dropzone renders "Limit 200MB
  per file" and the sidebar shows *"Upload limit mismatch: Streamlit accepts 200
  MB … Set maxUploadSize in .streamlit/config.toml to 50."*

**Verification:** all 6 e2e stages PASS (exit 0); `pytest tests/ -q` → 41 passed;
app boots clean (health 200, empty stderr).

---

### Task 11 — Skip the paddle import when the GPU is unusable ✅

**Committed:** see `perf(structure): skip paddle import when GPU is unusable`

`StructureRecognitionSkill._detect_gpu()` imported paddle purely to discover
there was no usable GPU, costing **2237 ms** — 19.7% of PDF wall clock, per the
Task 6 breakdown.

**The obvious check turned out to be the wrong one.** A CUDA-driver probe
(`ctypes.CDLL("nvcuda.dll")`) returns **True** here: this machine has a real
**NVIDIA RTX 3050 Laptop GPU**, driver 592.82. The driver was never the problem.
The blocker is that the installed wheel is the **CPU-only `paddlepaddle` 3.0.0**;
`paddlepaddle-gpu` is not installed, so paddle cannot use the GPU that is
physically present. Had I stopped at the driver check, this task would have
"passed" while changing nothing.

Two cheap short-circuits now run before the expensive probe:

1. **No CUDA driver library** → no CUDA device exists at all. Universally
   correct.
2. **Driver present but only the CPU-only wheel installed** → paddle cannot use
   CUDA regardless of hardware. This is the case that actually applies here, and
   is the common one on a gaming laptop.

Distribution detection uses two *direct* `importlib.metadata.version()` lookups.
The first implementation scanned `md.distributions()` and cost ~560 ms in this
Anaconda environment — better than 2237 ms but still wasteful; direct lookups
cut it to ~13 ms.

Rule 2 is escapable via `pdf.force_gpu_probe: true` for unusual builds shipping
CUDA under the plain `paddlepaddle` name. **Verified both paths return the same
answer**: default → `False` in 13 ms without importing paddle;
`force_gpu_probe=true` → `False` in 1974 ms with paddle imported.

Results: stage 3.5 drops from **2125 ms (19.7%) to 0 ms**, paddle is never
imported during a run, and unit-test warnings fall from 4 to 2 (paddle's ccache
warning is gone). A side benefit is that the root-logger mutation documented in
Task 6 no longer happens at all in normal operation.

**Verification:** all 6 e2e stages PASS (exit 0); `pytest tests/ -q` → 41 passed.

---

### Task 11a — Resolve the OpenCV collision ✅

**Committed:** see `build: collapse three OpenCV installs onto the pinned headless build`

Authorised one-off exception to the no-pip rule, scoped to this task only.

**Before:** `opencv-python` 4.13.0.92, `opencv-contrib-python` 4.10.0.84 and
`opencv-python-headless` 4.8.1.78 all installed, `import cv2` → **4.10.0**.
**After:** `opencv-python-headless` 4.8.1.78 alone, `import cv2` → **4.8.1**.

Ran exactly the documented sequence: uninstalled all three (confirmed `cv2` was
fully gone — `ModuleNotFoundError` and no `cv2` directory left behind), then
installed the pin with `--no-cache-dir`.

> Note on the version string: `cv2.__version__` reports **`4.8.1`**, not
> `4.8.1.78`. The trailing component is the packaging build number and is not
> part of the module version. `pip list` confirms the distribution is
> `4.8.1.78`. Flagging because the task asked to verify "4.8.1.78" specifically.

**OCR comparison — no difference whatsoever.**

Captured the OCR tier directly from `PDFReaderSkill` (pre-text-cleaner,
pre-LLM, so OpenCV is the only variable), twice before and twice after. Both
pairs were byte-identical, establishing that OCR is deterministic here and the
comparison is valid rather than coincidental.

| | Before | After |
|---|---|---|
| `cv2.__version__` | 4.10.0 | 4.8.1 |
| Engine | `ocr_tesseract` | `ocr_tesseract` |
| Words | 178 | 178 |
| Characters | 1256 | 1256 |
| Full text | \<identical\> | \<identical\> |

Byte-identical, so there was nothing to report as changed and no temptation to
tune anything.

**The deskew branch was confirmed to actually run**, because "no change" is
only meaningful if the risky path executed. Measured under 4.8.1:

```
page 1: minAreaRect raw=1.3020  adjusted=-1.3020  deskew_branch_fires=True
page 2: minAreaRect raw=1.2313  adjusted=-1.2313  deskew_branch_fires=True
```

Both match the 1.2° skew baked into the fixture and fall inside the
`0.5° < |angle| < 15°` window that triggers `warpAffine`, so `minAreaRect` and
`warpAffine` were both exercised on both versions.

**Honest scope limit:** this shows the convention did not change between 4.8.1
and 4.10.0 *for near-1° positive angles*. The `minAreaRect` convention change is
most visible near ±45° and ±90°, which this fixture does not cover. The
`scanned` stage is a regression detector for this configuration, not coverage of
the angle space.

`DEPENDENCIES.md` section 1 updated to RESOLVED with the measured evidence.

**Unrelated pre-existing conflicts** surfaced by `pip check`, none involving
OpenCV, none introduced here — recorded, not acted on: `streamlit 1.37.1`
requires `pandas<3` but 3.0.3 is installed; `shap` wants `numpy>=2`;
`opentelemetry-proto` and `streamlit` disagree with `protobuf 7.35.0`. The
streamlit/pandas one reinforces the DEPENDENCIES.md note about the unbounded
`pandas>=2.0.0` specifier. Left alone per your instruction.

**Verification:** all 6 e2e stages PASS (exit 0); `pytest tests/ -q` → 41 passed
(warnings still 2); app boots clean (health 200, empty stderr).

---

## Phase B — reliability

### Task 12 — One shared wrapper for every LLM call ✅

**Committed:** see `feat(llm): shared retry, backoff and 401-aware key rotation`

Surveyed the call sites first: **6 of 7 skills plus `results_view.py` already
routed through `LLMClient`**. Only `AudioReaderSkill._transcribe_audio()` built
its own `openai.OpenAI` — the exact gap flagged in Task 4. So this was hardening
one wrapper and pulling audio into it, not rewriting seven call sites.

**`LLMClient._run_with_rotation()`** is now the single place API failure handling
lives. Policy:

| Failure | Handling |
|---|---|
| **401** | Key is permanently invalid. Recorded in `_dead_key_idxs`, skipped for the rest of the process, rotate immediately — no backoff, since it will never start working |
| **429** | Transient. Rotate to next live key, exponential backoff with jitter |
| **5xx / connection / timeout** | Transient. Rotate and retry with backoff |
| **anything else** | Not retryable — give up, return `None` |

Backoff is `0.5s × 2^attempt`, capped at 8s, jittered to 50–100% to avoid
synchronised retries. Clients are cached per key index rather than rebuilt.

`chat()` and the new `transcribe()` both go through it, so the retry policy
cannot drift between skills.

**`AudioReaderSkill` now uses `LLMClient.transcribe()`.** Previously it resolved
keys itself (bypassing the placeholder filter from Task 4), used only the *first*
key, and had no retry whatsoever — a single 429 killed the whole transcription.
The transcription model stays an explicit call-site choice
(`self._transcription_model`), not a default hidden in the shared client. The
orphaned `import os` left by this change was removed. **This closes the residual
gap documented in Task 4.**

`transcribe()` reopens the file handle per attempt — a retry on a consumed handle
would upload zero bytes.

**A real defect was caught by testing rather than reading.** The first
implementation selected keys with `live[attempt % len(live)]`, which conflates
attempt number with key position. Because the live list shrinks as keys are
retired, it **skipped keys**: the 401 test tried `[0, 2]` instead of `[0, 1]`,
and the all-401 test tried `[0, 2, 1]`. It still found a working key, but burned
the attempt budget in arbitrary order. Replaced with a rotation pointer advanced
explicitly on each rotation, giving strict round-robin.

Verified against **real `openai` exception types** (constructed with genuine
`httpx` responses, not mocks of my own invention) — **9/9 checks pass**:
success-first-try; 401→rotate; **retired key skipped entirely on the next call**
(`[1]`, not `[0, 1]`); 429→backoff→rotate; 500→retry; timeout→retry; all-keys-401
→ `None` with all retired; non-retryable → immediate `None`.

**Verification:** all 6 e2e stages PASS (exit 0), transcription confirmed working
through the new path for both local audio (59 words) and YouTube (37 words);
`pytest tests/ -q` → 41 passed.

---

### Task 13 — Token usage and estimated cost per run ✅

**Committed:** see `feat(llm): log token usage and estimated cost per run`

Usage is captured from each chat response's `usage` object and accumulated in a
module-level `UsageTotals` record. It has to be module-level rather than
per-instance because **every skill builds its own `LLMClient`**, so a per-client
counter would only ever see a fraction of a run. `BaseAgent._begin_pipeline()`
zeroes it and `_log_pipeline_summary()` reports it, so totals are strictly
per-run.

Tokens are recorded **before** the response is inspected for usable choices —
they were spent whether or not the reply turned out to be usable.

Output appended to the existing per-stage breakdown:

```
tokens: 2,199 total (1,638 prompt + 561 completion) over 3 LLM call(s), 0 cache hit(s)
  llama-3.3-70b-versatile: 3 call(s), 1,638 prompt + 561 completion
  transcription: 1 call(s) — billed per second of audio, not tokens; excluded from the estimate
estimated cost: $0.001410 USD (estimate, not a billed figure)
```

Cache hits are counted separately, so a run served from cache is visibly cheap
rather than looking like it made no calls.

**Deliberate honesty about the estimate.** Rates live in `groq.pricing` in
`configs/default.yaml` (0.59 / 0.79 USD per million in/out for
llama-3.3-70b-versatile) rather than hardcoded, are labelled in the config,
docstring and log line as **estimates rather than billed figures** with a pointer
to verify against current pricing, and can be zeroed to disable the estimate
entirely (verified: emits "not calculated"). **Whisper is excluded rather than
guessed at** — it is billed per second of audio and the `response_format="text"`
reply carries no usage object, so transcription calls are counted and explicitly
noted as excluded instead of being given a fabricated number.

`GroqConfig.pricing` was added to the dataclass, the YAML loader and `to_dict()`
— without the last one the rates would have been silently dropped on the way to
the agent.

**Verified per-run isolation** rather than assuming it: 1500 tokens recorded,
reset, then 15 more → reports 15, not 1515. All 6 stages report distinct costs
($0.000428–$0.001418), confirming counters do not leak between runs.

**Verification:** all 6 e2e stages PASS (exit 0); `pytest tests/ -q` → 41 passed;
app boots clean (health 200, empty stderr).

**Correction (later):** cost estimation disabled at the maintainer's request —
this project runs on free-tier keys where rate limits, not dollars, are the
constraint. `groq.pricing` rates are zeroed, so the cost line reads
"not calculated". **Token counts are retained.** No Whisper cost estimate was
added.

---

### Task 13b — Rate-limit scope diagnostic, header headroom, and 429 parking ✅

**Committed:** see `feat(llm): rate-limit headroom logging and window-aware 429 parking`

#### Part 1 — the diagnostic (run before any code was written)

**Question: are Groq limits per key or per organisation?**
**Answer: per key. Rotation genuinely works.**

I did *not* drive a key to 429 as the primary method. Groq returns
`x-ratelimit-*` headers on every response, so counters can be read directly —
non-destructive, immediate, and unambiguous, where an induced 429 would burn a
real chunk of daily quota and still leave the result suggestive rather than
conclusive.

Evidence:

1. **RPD counters diverge per key**, with independent reset windows:
   986 / 947 / 947 / 999×5, resetting in 20m, 1h16m, and 1m26s respectively. A
   shared pool would report one number.
2. **Decisive:** during a live run, key 1 was refused at **TPD 99,654/100,000**
   and key 2 at **98,412/100,000** — *different* consumption figures in the same
   run. Pooled quota would show identical values. A ~1500-token request on the
   least-used key also succeeded while key 1 was refused.

The 429 body names an organisation (`org_01kp6ehgn9fbzt3jfsskjgpxzf`), which is
what prompted the question. That identifies *whose* limit it is, not that it is
pooled across keys.

**Not established:** whether all 8 keys belong to that one organisation — only
key 1's refusal exposed an org ID. Rotation demonstrably works either way, so no
further quota was spent closing this.

**The bigger find: a fourth limit that appears in no header.** Headers expose
only RPD (1000/day) and TPM (12,000/min). **TPD — tokens per day, 100,000 — is
invisible until it refuses you**, and it is the limit actually being hit. Any
headroom display based purely on headers looks healthy right up to the moment
TPD stops the run.

Diagnostics kept in the repo: `tests/e2e/ratelimit_probe.py` (header counters,
interleave, token-pool test) and `tests/e2e/ratelimit_scope_check.py` (per-key
accept/refuse, `--large` for the decisive oversized request).

#### Part 2 — header headroom logging

`_record_headroom()` reads the `x-ratelimit-*` headers from every response via
`with_raw_response`, logging per-key headroom at DEBUG and **warning at INFO when
a key drops below 10%** of either budget. The asymmetric naming is labelled
explicitly rather than assumed symmetric:

```
key 1/8 headroom: requests/day 988/1000 (resets 17m16.8s), tokens/min 11453/12000 (resets 2.735s)
```

`tests/e2e/e2e.py` now honours `DOCAGENT_LOG_LEVEL` so this can be surfaced
without editing the file.

#### Part 3 — window-aware 429 handling

A 429 can be RPM, RPD, TPM or TPD, and they need opposite responses. The handler
now parses the `retry-after` header (preferred) and the message body (fallback):

- **Short wait** (≤ `groq.park_threshold_seconds`, default 15s — typical of
  per-minute TPM/RPM): back off and retry, as before.
- **Long wait** (per-day TPD/RPD, quoted in minutes or hours): the key is
  exhausted for the window, so **park it and rotate immediately** rather than
  burning retry attempts on a guaranteed refusal.

**Parking expires**, unlike 401 retirement — daily windows do reset, and a parked
key rejoins the rotation automatically once its deadline passes.

A parsing bug was caught by testing: the regex character class also matched the
sentence-ending period, yielding `"28m3.936s."`, which failed to parse and would
have **silently disabled parking entirely**. Fixed in both the client and the
diagnostic.

#### Verification

`tests/test_llm_ratelimit.py` — **14 tests**, using real `openai` exception types
carrying real `httpx` responses and headers. Covers duration parsing (compound
`1h16m19.2s`, `185ms`, bare seconds, unparseable), TPD/TPM body parsing, long-429
parking, parked keys skipped on later calls, park expiry, short-429 retry without
parking, `retry-after` header taking precedence over the body, all-keys-parked,
and 401 still retiring permanently rather than being downgraded to a park.

**Proven against the live API, not just in tests:** during the full run keys 1
and 2 were repeatedly refused on TPD, parked with their real retry windows
(5–51 minutes), and every stage still completed through the remaining keys.

**Verification:** all 6 e2e stages PASS (exit 0); `pytest tests/ -q` → **55
passed** (41 existing + 14 new).

---

### Task 13c — Derived TPD headroom and rate-limit documentation ✅

**Committed:** see `feat(llm): derive TPD headroom from refusals; document Groq limits`

TPD is stated in exactly one place — the body of a 429 — so its figures are now
cached per key (`limit`, `used`, `requested`, and a reset deadline computed from
the retry window) at the moment of refusal, and surfaced alongside the RPD and
TPM header figures:

```
key 3/8 headroom: requests/day 931/1000 (resets 1h39m21.6s),
                  tokens/min 8406/12000 (resets 17.97s),
                  daily: unknown (no refusal seen yet)
```

Three states, all honest about provenance:

| State | Reported as |
|---|---|
| Never refused | `daily: unknown (no refusal seen yet)` |
| Refused, window still open | `TPD: ~318/100,000 left [derived from refusal 4m ago, not live, resets in 28m]` |
| Refusal window elapsed | `TPD: window has since reset (was 318/100,000 left); unknown until next refusal` |

No number is ever invented, and **no request is sent purely to discover a limit**.
Per-minute refusals are explicitly not cached as daily figures.

5 new tests (60 total): unknown-before-refusal, figures cached correctly from the
body, the note labelled derived-and-not-live, reset handling once the window
passes, and per-minute refusals not polluting daily state.

**An honest limitation.** Because a key is *parked* the moment it is refused, it
is not called again inside that window — so the derived figure rarely appears in
a headroom line during a single run. Where it does show up reliably is the
parking log itself (`429 TPD 99880/100000, retry in 50m1.536s`) and the
`daily_limit_report()` accessor. The derived note earns its place across a
long-lived process such as the Streamlit app; within one CLI run the parking
message is the practical source. Left as-is rather than adding machinery to
force it into view.

**Documentation:** new README section, "Groq rate limits — read this before you
debug a stalled run", covering all four limits, which two appear in headers, the
naming asymmetry (`limit-requests` is per DAY, `limit-tokens` is per MINUTE),
that TPD at 100K/day is the binding constraint (≈30–50 documents), that limits
are per-key so extra keys multiply the budget, and how the client parks versus
retries.

**Verification:** all 6 e2e stages PASS (exit 0); `pytest tests/ -q` → **60
passed**.

---

## Phase C — UI

### Task 14 — Per-stage progress in Streamlit ✅

**Committed:** see `fix(ui): position-based progress with per-stage timings`

**The `_log_step` monkeypatching at `app.py` was not touched** — the walk-back
over `__wrapped_orig__`, the assignment and the restore in `finally` are all
byte-identical. Only the body of the callback and the surrounding display
changed, per the constraint.

Two real defects in the old display:

1. **The denominator was wrong.** `total = len(STEP_LABELS)` counted **7**, but
   one of those keys was `"done"` — never a skill name, so never a callback.
2. **Progress was counted, not positioned.** The planner routinely skips stages
   (question extraction unless questionnaire, structure recognition unless
   table-heavy), so dividing callback count by a fixed total drifted out of step
   with the label. `structured_extraction` had no label at all and rendered as
   "Structured Extraction…".

Concretely, the audio run just verified executes **4** stages. Old behaviour:
4/7 = 57% while displaying "Generating summary…", then a jump to 100%. New
behaviour: summarize is stage 4 of 6 → 67%, correctly positioned.

Progress is now derived from `STAGE_POSITIONS`, the canonical stage numbers from
the frozen pipeline, so a skipped stage no longer shifts the bar out of step.
Added the missing `structured_extraction` label. Failed stages render `✗ … FAILED`
instead of silently reading as progress. Each stage appends a `✓ name — 2.6s`
entry, the last four shown live and the full list on completion.

**Verified by driving the real UI**, not by reasoning about it: started the app,
selected YouTube input, submitted a URL and clicked Analyze through the browser.
Result:

```
Complete · 4 stage(s) in 10.9s
✓ Parsing document — 7.4s · ✓ Normalising text — 0.1s
✓ Classifying document — 0.6s · ✓ Generating summary — 2.6s
```

Results, tabs, stats and download buttons all rendered; app stderr clean.

**Verification:** all 6 e2e stages PASS (exit 0); `pytest tests/ -q` → 60 passed.

---

### Task 15 — Persist document and chat state ✅

**Committed:** see `fix(ui): persist document content and repair history reload`

Within-session rerun persistence already worked (`doc_result_*`, `chat_history_*`
and `chat_chunks_*` are all keyed by filename in `session_state`). Driving the
real UI surfaced **two genuine defects instead**, both pre-existing.

**Defect 1 — history reload silently bounced back to the empty screen.**
`_render_upload()` runs *after* the history-restore block. When the input mode is
"Upload Files" (the default) and the uploader is empty, the `elif` at
`ui/app.py` popped `_file_data` — undoing the restore on the very same rerun.
Clicking any history item therefore did nothing visible. This was only masked
earlier because I had switched the app to "YouTube URL" mode, where the branch
never fires.

Fixed by dropping only *uploader-backed* entries: an entry restored from history
carries no `"bytes"` key and now survives the clear.

**Defect 2 — history results had no document content at all.**
`PipelineResult.to_dict()` — what `DocumentStore` persists — excludes `raw_text`
and chunks. A reloaded document showed `0 CHARACTERS`, and the Chat and Edit tabs
queried an *empty* document while looking perfectly normal, producing confident
"not in the document" answers.

`DocumentStore` now stores `content_text` and `content_chunks_json` in their own
columns, added by migration so existing databases keep working. They are
deliberately **not** added to `to_dict()`, because that feeds the user-facing
"Download JSON" export and stuffing the whole document into it would change what
that download contains. `load()` merges them back as `raw_text` and
`content_chunks`; `app.py` restores the chunks into the chat state. A guard in
the chat tab reports "No document content available" for pre-migration entries
rather than answering from nothing.

**A third bug found while testing:** `DocumentStore._connect()` returned a bare
`sqlite3.Connection`, and every call site used `with self._connect() as conn:`.
That reads as if it closes, but sqlite3's context manager only commits or rolls
back — it **never closes**. Every operation leaked a connection until garbage
collection, holding a Windows file lock and accumulating handles in a long-lived
Streamlit session. It surfaced as `PermissionError` deleting a temp DB in the new
tests. `_connect` is now a `@contextmanager` that closes; call sites unchanged.

**Verified in the running app, after a server restart:** clicking the
`sample_report.pdf` history entry now renders (`1.4k CHARACTERS`, previously `0`)
and chat answers correctly from persisted chunks —

> "MTBF improved from 410 hours in Q2 to 690 hours in Q3, which represents a
> +68% change. (Page 1) — Sources: 1, 2"

citing the table and both chunks. Migration was also checked against the real
`~/.docagent/history.db`: 2 existing rows preserved, columns added, old entries
correctly reporting no content.

5 new tests (65 total): content round-trip, export unchanged, result without a
parsed document, migration preserving legacy rows, migration idempotent.

**Verification:** all 6 e2e stages PASS (exit 0); `pytest tests/ -q` → **65
passed**.

---

### Task 16 — Remove `.DS_Store` and stale CPython 3.13 bytecode ✅

**No commit — nothing was tracked.** The task described these as "committed",
which was true of the *original* `doc-agent` repo but not of this one: the clean
`docagent` repo was created from a tree where both were already gitignored.
Verified before deleting anything —

```
git log --all -- "*.DS_Store"  -> (empty, never committed)
git log --all -- "*.pyc"       -> (empty, never committed)
.gitignore:178  .DS_Store
.gitignore:2    __pycache__/
```

So this was a local disk cleanup only, and `git status` is unchanged.

Deleted from the working tree:

| Item | Count |
|---|---|
| `.DS_Store` | 1 |
| `*.cpython-313*.pyc` | 33 |

**Left alone, deliberately:**

- **`*.cpython-310*.pyc` — 10 files, equally stale.** Bytecode for an
  interpreter that is not installed either, so the same argument applies. Not
  named in task 16, and the standing rule is not to delete beyond what that task
  names, so it is reported rather than removed. Say the word and it goes.
- **`*.cpython-312*.pyc` — 37 files.** The *active* interpreter (3.12.7).
  Deleting these would only force a harmless recompile.

**Verification:** all 6 e2e stages PASS (exit 0); `pytest tests/ -q` → 65 passed;
`git status` clean with no changes to commit.

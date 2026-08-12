# Dependency Notes

Known dependency problems in this environment, why they matter, and the exact
commands to resolve them.

> **Status.** Sections 1 and 2 are **resolved** — the commands in them were run
> and the outcome is recorded inline. Section 3 documents a dependency that was
> added deliberately. Section 4 records smaller things that are still open and
> have *not* been acted on.
>
> Versions were observed on the development machine on 2026-07-29. The pipeline
> passes all 6 e2e stages and all 101 unit tests as-is.

---

## 1. Three conflicting OpenCV distributions — ✅ RESOLVED

> **Resolved on 2026-07-29.** The commands in this section were run. The
> environment now has a single OpenCV distribution:
>
> ```
> opencv-python-headless  4.8.1.78      →  import cv2 → 4.8.1
> ```
>
> `opencv-python` and `opencv-contrib-python` are gone, and the pin in
> `requirements.txt` now matches what actually loads.
>
> **OCR output is byte-identical across the change.** The `minAreaRect` risk
> described below was tested rather than assumed — see
> "Verified impact on OCR" at the end of this section. The rest of the section
> is kept as the record of what the problem was.

### What was installed

| Distribution | Version | Files owned under `cv2/` |
|---|---|---|
| `opencv-contrib-python` | 4.10.0.84 | 135 |
| `opencv-python` | 4.13.0.92 | 92 |
| `opencv-python-headless` | 4.8.1.78 | 90 |

All three ship the **same top-level `cv2` package**, so they overwrite each
other's files in `site-packages`. There is no isolation: whichever was installed
last wins, and pip does not warn about it.

### What actually loads

```
import cv2 → 4.10.0
             C:\Users\Aakrisht\anaconda3\Lib\site-packages\cv2\__init__.py
```

`requirements.txt` pins `opencv-python-headless==4.8.1.78`. **That is not what
loads.** The resolved runtime is 4.10.0 — two minor versions ahead of the pin,
from a distribution that is not in `requirements.txt` at all.

### Why it matters

- The pin in `requirements.txt` is decorative. Anyone reproducing this
  environment gets a different OpenCV than the developer is running.
- Reinstalling *any* of the three silently changes the OpenCV that the OCR path
  executes against, with no import error to signal it.
- The OCR preprocessing chain in `skills/pdf_reader_skill.py` (`adaptiveThreshold`,
  `warpAffine`, `minAreaRect`) is version-sensitive in its numerical output.
  `minAreaRect`'s angle convention in particular changed across the 4.x line, and
  the deskew step branches on that angle — a silent OpenCV swap can change OCR
  results without any error.

### Which one this project actually needs

Every OpenCV symbol used anywhere in the codebase:

```
ADAPTIVE_THRESH_GAUSSIAN_C  BORDER_CONSTANT  BORDER_REPLICATE
COLOR_GRAY2BGR  COLOR_RGB2BGR  COLOR_RGB2GRAY  COLOR_RGBA2GRAY  COLOR_RGBA2RGB
GaussianBlur  IMWRITE_JPEG_QUALITY  INTER_CUBIC  THRESH_BINARY
adaptiveThreshold  cvtColor  getRotationMatrix2D  imencode  minAreaRect  warpAffine
```

- **No GUI calls** (`imshow`, `waitKey`, `namedWindow`, …) → the `headless` build
  is sufficient, and is the correct choice for a Streamlit/server app.
- **No contrib-only modules** (`xfeatures2d`, `ximgproc`, `face`, `aruco`,
  `tracking`, …) → `opencv-contrib-python` is not needed.

So `opencv-python-headless` alone is correct, exactly as `requirements.txt`
already declares.

### Resolution

Remove the two unneeded distributions, then reinstall the pinned one so its
files are restored intact (the uninstall of the others will have deleted shared
files):

```bash
pip uninstall -y opencv-python opencv-contrib-python opencv-python-headless
pip install --no-cache-dir opencv-python-headless==4.8.1.78
```

Uninstalling all three first is deliberate. Removing only two leaves the
survivor with files deleted by the others' uninstall, producing a broken `cv2`
that imports but fails at call time.

Verify afterwards:

```bash
python -c "import cv2; print(cv2.__version__, cv2.__file__)"
```

Expect `4.8.1` and a path under `site-packages/cv2/`. Then re-run
`python tests/e2e/e2e.py scanned` — that is the stage that exercises the OCR
preprocessing chain, and the one that would reveal a behavioural change.

> Note: `cv2.__version__` reports **`4.8.1`**, not `4.8.1.78`. The trailing
> component is the packaging build number and does not appear in the module
> version. `pip list` is the place to confirm `4.8.1.78`.

### Verified impact on OCR

The concern was that `minAreaRect`'s angle convention changed across the 4.x
line and the deskew step branches on that angle, so moving 4.10.0 → 4.8.1 could
silently change OCR output. This was measured, not assumed.

**Method.** The OCR tier was captured directly from `PDFReaderSkill`
(pre-text-cleaner, pre-LLM, so OpenCV is the only variable), twice before the
change and twice after. Both pairs were byte-identical, confirming OCR is
deterministic here and that a comparison is meaningful.

**Result — no difference:**

| | Before | After |
|---|---|---|
| `cv2.__version__` | 4.10.0 | 4.8.1 |
| Engine | `ocr_tesseract` | `ocr_tesseract` |
| Words | 178 | 178 |
| Characters | 1256 | 1256 |
| Full text | \<identical\> | \<identical\> |

**The deskew branch genuinely runs**, so this is not a case of the risky code
path being skipped. Under 4.8.1 the measured angles are:

```
page 1: minAreaRect raw=1.3020  adjusted=-1.3020  deskew_branch_fires=True
page 2: minAreaRect raw=1.2313  adjusted=-1.2313  deskew_branch_fires=True
```

Those match the 1.2° skew deliberately baked into the fixture, and both fall
inside the `0.5° < |angle| < 15°` window that triggers `warpAffine`. So
`minAreaRect` and `warpAffine` were both exercised and produced identical
downstream text on both versions.

**Scope of this result.** It shows the convention did not change *between
4.8.1 and 4.10.0 for near-1° positive angles*. It is not a general guarantee:
the convention change is most visible near ±45° and ±90°, which this fixture
does not cover. Treat the `scanned` stage as a regression detector for this
configuration, not as full coverage of the angle space.

---

## 2. pandas 3.0.3 is ahead of its acceleration dependencies — ✅ RESOLVED

> **Resolved on 2026-07-29.** Both halves are fixed, by one change rather than
> two: pandas was moved into the range Streamlit actually supports.
>
> ```
> requirements.txt:  pandas>=2.0.0   ->  pandas>=2.0.0,<3
> installed:         pandas 3.0.3    ->  pandas 2.3.3
> ```
>
> **Why `<3` and not an accelerator upgrade.** The upper bound is not this
> project's own constraint — it is Streamlit's. `streamlit 1.37.1` declares
> `pandas<3,>=1.3.0`, so pandas 3.0.3 made `pip check` report an incompatible
> environment. Bounding pandas satisfies that *and* removes the unbounded
> major-version span in one move.
>
> **The accelerator warnings disappeared as a side effect.** pandas 2.3.3 has
> lower minimums for `numexpr` and `Bottleneck` than 3.0.x, and the installed
> 2.8.7 / 1.3.7 satisfy them — so no upgrade of either was needed and neither
> warning is emitted any more. Unit-test warnings dropped from 4 to 2.
>
> **Excel output is byte-identical across the change**, verified rather than
> assumed: the parsed text of both workbook fixtures hashes the same under
> pandas 3.0.3 and 2.3.3.
>
> | | pandas 3.0.3 | pandas 2.3.3 |
> |---|---|---|
> | `sample_sales.xlsx` | 2 sheets, 719 chars, sha `2c8c4939567cfca1` | identical |
> | `sample_large_sales.xlsx` | 8 sheets, 2827 chars, sha `9ef344a7302bef35` | identical |
>
> **Still outstanding, unrelated to pandas.** `pip check` continues to report
> three pre-existing conflicts that were present before this change and are not
> caused by it:
>
> ```
> opentelemetry-proto 1.40.0 requires protobuf<7.0,>=5.0, but protobuf 7.35.0 is installed
> streamlit 1.37.1    requires protobuf<6,>=3.20,  but protobuf 7.35.0 is installed
> shap 0.52.0         requires numpy>=2,           but numpy 1.26.4 is installed (pinned here)
> ```
>
> **A note on a fresh clone.** A clean resolve of `requirements.txt` selects
> `streamlit 1.60.0`, which permits pandas 3 — so the conflict is a property of
> the *installed* streamlit 1.37.1, not of the requirements file. The `<3` bound
> is still correct: it guarantees a consistent environment on both, rather than
> depending on which Streamlit a resolver happens to pick.
>
> The original problem statement is kept below as the record.

### What was installed

| Package | Installed (before) | pandas 3.0.3 required |
|---|---|---|
| `pandas` | 3.0.3 | — |
| `numexpr` | 2.8.7 | **>= 2.10.2** |
| `Bottleneck` | 1.3.7 | **>= 1.4.2** |
| `numpy` | 1.26.4 | satisfied |

Every run that touched the Excel/CSV path emitted:

```
UserWarning: Pandas requires version '2.10.2' or newer of 'numexpr'
             (version '2.8.7' currently installed).
UserWarning: Pandas requires version '1.4.2' or newer of 'bottleneck'
             (version '1.3.7' currently installed).
```

### Why it matters

- `numexpr` and `Bottleneck` are **optional accelerators**. pandas silently
  disables them when they are too old, so the Excel path is correct but slower,
  and every run carries two warnings that train you to ignore warnings.
- `requirements.txt` says `pandas>=2.0.0`. pandas **3.0** is a major release with
  breaking changes; the unbounded specifier means a fresh `pip install -r` on a
  clean machine can resolve anywhere from 2.0 to 3.x and get materially
  different behaviour. This is the more serious half of the problem.

### Resolution

Two independent choices. **Pick deliberately — do not do both blindly.**

**a. Silence the warnings by upgrading the accelerators** (keeps pandas 3.0.3):

```bash
pip install --upgrade "numexpr>=2.10.2" "Bottleneck>=1.4.2"
```

**b. Constrain the pandas range so the environment is reproducible.** Edit
`requirements.txt` — decide first whether this project intends to be on pandas 3:

```
# currently:  pandas>=2.0.0
# either pin the major line actually in use:
pandas>=3.0,<4.0
# or hold at the 2.x line the code was written against:
pandas>=2.0,<3.0
```

Nothing in the codebase currently depends on pandas 3-only behaviour — the Excel
reader uses `read_excel`, `DataFrame`, and `to_string`, all stable across 2.x and
3.x — so either bound is viable. Whichever you choose, re-run
`python tests/e2e/e2e.py excel` afterwards.

---

## 3. `sentence-transformers` — the heaviest dependency in the project

Added for document-chat retrieval. Pinned in `requirements.txt` as
`sentence-transformers==5.4.0`.

### Install footprint

This is by far the largest thing the project installs, and almost none of it is
`sentence-transformers` itself:

| Package | Installed | Notes |
|---|---|---|
| `sentence-transformers` | 5.4.0 | the pinned direct dependency |
| `torch` | 2.7.1+cu118 | **5.2 GB on this machine** — transitive |
| `transformers` | 5.5.4 | transitive |
| `scikit-learn` | 1.9.0 | transitive |
| `scipy` | 1.13.1 | transitive |

**5.2 GB is the CUDA build.** It is what happened to be installed here; the
project never asks for GPU support and runs the model on CPU. A CPU-only torch
is a small fraction of that:

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

**`torch` is deliberately not pinned.** Pinning it would force one platform's
build on everyone — a CUDA wheel onto machines without a GPU, or a CPU wheel
onto machines that want one. `sentence-transformers` is pinned instead, and torch
resolves to whatever suits the platform.

### First-run behaviour

The model weights are **not** bundled. They download on first use and are cached
afterwards:

| | |
|---|---|
| Model | `all-MiniLM-L6-v2`, 384 dimensions |
| Download size | **87 MB** |
| Cache location | `~/.cache/huggingface` (`%USERPROFILE%\.cache\huggingface` on Windows) |
| First encode, incl. download and load | **~30–55 s** (measured 27 s, 30 s, 32 s, 53 s across runs — varies with network and disk cache) |
| Every later encode, 20 chunks | **~25 ms** (23–29 ms observed) |
| Import cost | **0 ms** — loading is lazy, never at import |

**Where that 30 s lands:** at document ingest, inside `DocumentStore.save()`,
which embeds the chunks. That is *after* the progress bar reads "Complete", so
without a spinner the page appears to freeze at the moment it looks finished.
`ui/app.py` wraps it in `st.spinner` with wording that only mentions the
download when a download will actually happen.

**Offline first run.** If the weights cannot be fetched, the load fails, the
failure is logged once, and chat falls back to keyword overlap. Nothing crashes,
and the rest of the pipeline is unaffected — no other stage uses embeddings. To
pre-warm the cache deliberately:

```bash
python -c "from utils import embeddings; embeddings.encode(['warm up'])"
```

### The model silently truncates at 256 tokens

**This is the one behaviour in this section most likely to cost you something
without your noticing.**

`all-MiniLM-L6-v2` has `max_seq_length = 256`. Anything longer is **cut before
it is embedded**. There is no exception, no warning in the app's logs, and no
indication in the returned vector that anything was dropped — the tail of the
text simply does not exist as far as retrieval is concerned.

**The symptom is degraded ranking, not an error.** A chunk whose second half was
discarded still gets a perfectly valid-looking vector; it just describes half a
document. Retrieval then ranks it on material it can no longer see, so the
answer sits lower than it should or loses to a shorter chunk that happens to be
fully visible. Nothing anywhere reports a problem.

Measured on the eval fixtures, which is how it was found at all:

| fixture | words/page | max tokens/page | pages over the 256 limit |
|---|---|---|---|
| `sample_dense_manual.pdf` | ~222 | 282 | **5 of 8** |
| `sample_large_report.pdf` | ~46 | 110 | 0 of 20 |

So most of the dense fixture was being ranked from a lossy copy of its own text,
while the older fixture never came close to the limit — which is exactly why the
problem stayed invisible for as long as it did.

**This is part of why the shipped passage size is what it is.** The default in
`utils/chunking.py` is 100 words with 20 words of overlap. That figure was
chosen by a retrieval sweep (see
[`docs/retrieval-sub-chunking.md`](docs/retrieval-sub-chunking.md)), but it is
also **bounded from above by this limit rather than by retrieval quality alone**.
At the measured ratio of ~1.21 tokens per word on these fixtures, 100 words is
about 120 tokens — comfortably inside the window with room for word-heavy or
non-English text. The ceiling expressed in words is roughly **212**, so a
passage size much above ~150 starts risking truncation on dense text even though
the sweep shows larger passages are not much worse for ranking.

Sizes are specified in words rather than tokens on purpose: tokenising at index
time would make chunking depend on the embedding model being installed, and the
keyword fallback has to work without it. The trade is that the word→token ratio
is an estimate, which is another reason for headroom rather than a size that
only just fits.

#### If you change the model, re-derive this

**A different model has a different limit, and nothing in the code discovers it
for you.** `max_seq_length` varies widely — 256 here, 512 for many BERT-family
encoders, 8192 for some newer long-context embedding models. Swapping the model
without revisiting the passage size gets you one of two silent outcomes:

* a **smaller** limit than the passages you are producing — truncation returns,
  with the same invisible symptom; or
* a **much larger** limit — no breakage, but the passage size is now tuned for a
  constraint that no longer exists, and you may be splitting more finely than
  you need to.

The check takes a moment and needs no fixtures:

```python
from utils import embeddings
m = embeddings._get_model()
print(m.max_seq_length)                       # the hard ceiling, in tokens
print(len(m.tokenizer.encode("your text")))   # what a given chunk actually costs
```

Then re-run `python tests/e2e/rag_eval/run_eval.py` (free, no API calls) and
sweep `DOCAGENT_PASSAGE_WORDS` around the new ceiling. Note that changing the
model **also** invalidates every stored vector: `MODEL_NAME` and `chunk_scheme`
are both recorded per row in `history.db` and checked on load, so stale rows are
skipped rather than mis-compared — but they retrieve by keyword until the
document is re-analysed.

### If you want it gone

Retrieval degrades rather than breaks. Uninstalling it leaves chat working on
keyword overlap at **27/33 retrieved and 17/33 ranked first**, against 33/33 and
28/33 with embeddings — and mean rank falls from 1.18 to 3.09, which is the
figure that best captures how much worse the ordering gets. The loss is
concentrated in synonym-phrased questions, where no amount of word overlap
helps. Verify with:

```bash
python tests/e2e/rag_eval/run_eval.py --keyword
```

---

## 4. Also worth knowing

These were observed alongside the two issues above and are recorded so they are
not rediscovered later. No action taken.

### `python-dotenv` is below its own pin

`requirements.txt` declares `python-dotenv>=1.0.0`; **0.21.0** is installed. The
environment does not satisfy its own requirements file. This is the parser that
produced the `could not parse statement` warning on the multiline
`GROQ_API_KEYS` value. That specific symptom is resolved (the value is now
quoted, and `_read_multiline_env_keys()` agrees with dotenv), but the version
mismatch remains.

```bash
pip install --upgrade "python-dotenv>=1.0.0"
```

### The ffmpeg PATH entry embeds its version

`ffmpeg` and `ffprobe` resolve through:

```
%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.2-full_build\bin
```

The version is baked into the directory name. The next `winget upgrade` replaces
that folder with an `ffmpeg-8.x.y-full_build` directory under a new name, and the
stale PATH entry silently stops resolving — at which point `_find_ffmpeg()` falls
back to the `imageio-ffmpeg` binary, and YouTube/audio behaviour changes without
any explicit error.

The durable fix is to put the three executables somewhere version-independent
that is already on PATH:

```bash
copy "%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.2-full_build\bin\ffmpeg.exe"  "%LOCALAPPDATA%\Microsoft\WinGet\Links\"
copy "%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.2-full_build\bin\ffprobe.exe" "%LOCALAPPDATA%\Microsoft\WinGet\Links\"
copy "%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.2-full_build\bin\ffplay.exe"  "%LOCALAPPDATA%\Microsoft\WinGet\Links\"
```

`%LOCALAPPDATA%\Microsoft\WinGet\Links` is already on PATH and is not
version-scoped, so upgrades stop breaking resolution.

### Stale bytecode from a different interpreter

`__pycache__` contains `.pyc` files compiled for **CPython 3.13** while the
active interpreter is **3.12.7**. Harmless — Python ignores bytecode whose magic
number does not match — but it indicates the project has been run under more than
one interpreter, which is worth knowing if you hit an inexplicable import
problem. Removal is covered by Task 16.

---

## 5. The stylesheet depends on Streamlit's internal test IDs

`ui/styles/custom.css` styles Streamlit's own widgets by targeting
`[data-testid="…"]` attributes. **These are internal, undocumented, and get
renamed between Streamlit versions.**

**Verified against `streamlit==1.37.1`.**

### Why this is worth writing down

**The failure mode is silent.** A selector that no longer matches does not
error, warn, or log — the styling simply reverts to Streamlit's default and the
surface quietly looks wrong, often only in one theme. It can sit broken for a
long time without anyone noticing.

That is not hypothetical here. An audit of every test ID the stylesheet targeted
— 25 distinct widget targets, written as 28 selector strings because the three
button rules carry two spellings each — found **four that had never matched
anything in this build**:

```
stBaseButton-secondary        dead  ->  baseButton-secondary
stBaseButton-primary          dead  ->  baseButton-primary
stBaseButton-headerNoPadding  dead  ->  baseButton-headerNoPadding
stSidebarCollapsedControl     dead  ->  (no equivalent; rule removed)
```

Streamlit 1.37.1 builds the button IDs as `"baseButton-".concat(kind)` —
confirmed by grepping the shipped frontend bundle, where the literal
`"stBaseButton-primary"` never appears but `"data-testid":"baseButton-".concat(t)`
does. The `stBaseButton-*` spelling belongs to a later release. Every button rule
in the stylesheet was therefore inert, in both themes, for as long as those rules
had existed.

A fifth, `stNumberInput`, is a real ID in this version but the app renders no
number inputs, so that rule was removed as dead weight rather than as a bug.

The rules now carry **both spellings**, so they work on 1.37.x and keep working
after the rename rather than silently dying at the next upgrade.

> **The three `stBaseButton-*` selectors are inert on this version.** They match
> nothing in 1.37.1 and are present for forward compatibility only — they exist
> so the button styling survives the upgrade that renames `baseButton-*`. Do not
> read them as working rules, and do not "fix" a button by editing them: on this
> version only the `baseButton-*` half of each rule has any effect. If you are
> debugging button styling here, the `baseButton-*` selector is the live one.
>
> Once this project moves to a Streamlit version that emits `stBaseButton-*`, the
> pair inverts and the `baseButton-*` half becomes the dead one. Neither half is
> safe to delete until the supported version range covers only one spelling.

### If styling looks wrong after a Streamlit upgrade

Suspect this first. To re-audit, dump what the running app actually emits and
compare against what the stylesheet targets:

```javascript
// in the browser console, with the app open
[...new Set([...document.querySelectorAll('[data-testid]')]
  .map(e => e.getAttribute('data-testid')))].sort()
```

```bash
grep -oE 'data-testid="[^"]+"' ui/styles/custom.css | sort -u
```

Anything in the second list and not the first is either dead or belongs to a
widget that is not on the page you sampled — check the shipped bundle to tell
those apart:

```bash
grep -rl '"stAlert"' "$(python -c 'import streamlit,os;print(os.path.dirname(streamlit.__file__))')/static/static/js"
```

### Reducing the exposure

Streamlit primitives are preferred over CSS wherever they can do the job, and
the theme is driven by design tokens so a broken rule affects one surface rather
than a whole theme. The remaining rules are commented with what each is for, so
a future reader can judge whether a dead one still matters.

---

## 6. `config.toml` pins the base theme, and some widgets ignore our tokens

**If a surface renders black-on-white in light mode, look here first.**

`.streamlit/config.toml` pins `base = "dark"`. Light mode is then applied by
swapping the design-token values in a `:root` block — see `_LIGHT_TOKENS` in
`ui/app.py`. That works for everything the stylesheet paints, because those
rules are written against `var(--token)`.

It does **not** work for widgets Streamlit paints from its *own* theme config.
Those read `secondaryBackgroundColor` / `textColor` straight out of the pinned
dark base, so they stay dark no matter what the token block says. The symptom is
always the same: a near-black slab or invisible text on a white page, in light
mode only.

**Known instances, all now pinned to tokens in `ui/styles/custom.css`:**

| Widget | Test ID | Was |
|---|---|---|
| Chat input | `stChatInput` | `rgb(17,17,24)` container — a black bar under the chat |
| Progress bar track | `stProgress` | black unfilled track; the fill also used the pinned `primaryColor` |
| Status panel state icons | `stExpanderIconCheck` / `stExpanderIconError` | `rgb(241,245,249)` — the dark theme's text colour, invisible on white |
| Code blocks | `stCodeBlock` | near-black slab; the "Extracted text" tab became a full-height wall of it |

Four separate times, the same root cause. If a fifth turns up, the fix is the
same shape — find the element, set `background` / `color` / `fill` to the
relevant token with `!important`, and add it to this table:

```javascript
// in the browser console, in LIGHT mode, on the offending page
[...document.querySelectorAll('*')]
  .filter(e => {
    const b = getComputedStyle(e).backgroundColor;
    const m = b.match(/[0-9]+/g);
    return m && m.length >= 3 && (+m[0] + +m[1] + +m[2]) < 120;
  })
  .map(e => e.closest('[data-testid]')?.getAttribute('data-testid'))
  .filter(Boolean);
```

**Why not just unpin the base theme?** Removing `base = "dark"` would make
Streamlit follow the OS preference, which the in-app Dark/Light control then
fights — the two disagree on first paint and the page flashes. Pinning one base
and swapping tokens is deliberate; this section is the cost of that choice,
written down.

---

## Verifying any dependency change

After changing anything above, the full check is:

```bash
python tests/e2e/e2e.py all
```

```bash
pytest tests/ -q
```

Expect 6 `PASS` rows with exit code 0, and 101 passing unit tests.

Retrieval quality is scored separately, with no API calls:

```bash
python tests/e2e/rag_eval/run_eval.py
```

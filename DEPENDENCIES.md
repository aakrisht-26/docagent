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

> **Resolved on 2026-07-29 LOCALLY. The hosted build was still reintroducing
> it until the paddle exclusion** — `paddleocr` pulls `paddlex[ocr-core]`, which
> depends on `opencv-contrib-python==4.10.0.84`, so a Community Cloud container
> installed that beside the pinned `opencv-python-headless==4.8.1.78`. Found by
> resolving the hosted set against cp312; see DEPLOYMENT.md. Both paddle lines
> are now `sys_platform != "linux"`, so the deployment has one OpenCV again.
> Fixing an environment locally does not fix the one you ship.
>
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
> **Since 2026-09-14 streamlit is pinned to 1.63.0**, which declares
> `pandas<4,>=1.4.0` and `protobuf<8,>=5.26.1`. The `<3` bound is kept, but it
> is now this project's choice rather than Streamlit's, and the
> streamlit/protobuf conflict above no longer appears in `pip check`
> (section 8).
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
reader uses `DataFrame`, `to_string` and, for CSV, `read_csv`, all stable across
2.x and 3.x (workbooks are opened with openpyxl, not pandas) — so either bound is
viable. Whichever you choose, re-run
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
keyword overlap at **27/33 retrieved and 17/33 on the worst-rank diagnostic**,
against 33/33 and 28/33 with embeddings — and mean worst rank falls from 1.18 to
3.09, which is the figure that best captures how much worse the ordering gets.
(28/33 is that diagnostic's ceiling, not a shortfall; see
`docs/retrieval-sub-chunking.md`.) (These are
LLM-independent: the eval makes no API call, so they hold whatever Groq model
is configured.) The loss is
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

**Verified against `streamlit==1.63.0`, the pinned version**, on 2026-09-14: in the
browser in both themes, and by `tests/test_stylesheet_test_ids.py`, which fails
if the stylesheet names a test ID the installed Streamlit never renders. What
follows up to "What the move from 1.37.1 to 1.63.0 broke" is the 1.37.1 record.

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

> **Resolved by the pin.** 1.63.0 emits only `stBaseButton-*`, so the
> `baseButton-*` half of each pair was removed. The pairs had also hidden a trap:
> each was a selector *list*, so the `st` half never had the scope written on the
> other half. Once it went live on 1.63.0, the sidebar history-button styling
> reached every secondary button on the page, and
> `[data-testid="stBaseButton-headerNoPadding"]` hid the sidebar's collapse
> button.

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
widget that is not on the page you sampled. `tests/test_stylesheet_test_ids.py`
tells those apart by reading the shipped bundle. The advice that used to be here,
grepping the bundle for `'"stAlert"'`, finds nothing on 1.63.0, which writes
these IDs in backticks; an empty grep proves nothing.

**What that test cannot see**, and what broke anyway on the move to 1.63.0: class
names (`.main`), `data-baseweb` attributes, and structural selectors
(`> div > div`). Those need the browser, in both themes, on the landing page,
during an analysis, and on every results tab. Force a restyle before reading a
computed style (`document.body.style.display = 'none'; document.body.offsetHeight;
document.body.style.display = ''`). With the browser pane not drawing, stale
values were read as real twice: a light page "painted dark" (section 6's
neighbour in `custom.css`, now corrected) and radios in the wrong checked state.
And read the colour of the element that holds the text: a tab element measured
1.10:1 on 1.63.0 while its label, a `p` coloured `--text-primary`, was 17.85:1.

### What the move from 1.37.1 to 1.63.0 broke, measured

Light theme unless noted. The same stylesheet, served by 1.37.1 and by 1.63.0,
before the rules were re-anchored.

| Surface | 1.37.1 | 1.63.0 before the fix | Cause |
|---|---|---|---|
| Content container | 1440px max, 16px top | no max width, 96px top | `.main` not rendered |
| Page headings | H1 28px, H3 16.8px | H1 44px, H3 28px | `.main` not rendered |
| Unselected radio | hollow `#cbd3e0` ring | solid `rgb(17,17,24)` dot | no `data-baseweb="radio"` |
| Selectbox | white control, value 17.85:1 | `rgb(17,17,24)` control, near-white value | no `data-baseweb="select"` |
| Dropdown menu | not measured (would not open in the test browser) | `rgb(9,9,15)` | `stSelectboxVirtualDropdown` |
| Code block | `rgb(246,247,249)`, bordered | `rgb(13,13,20)` slab | `stCodeBlock` is now `stCode` |
| Progress track / fill | `rgb(238,241,246)` / accent | `rgb(17,17,24)` / pale grey | `stProgressBarTrack`, one div fewer |
| Status header, mid-analysis | white, label 17.85:1 | `rgb(13,13,20)`, label 1.08:1 | the summary row is painted |
| Chat input | typed text 17.85:1 | 1.05:1 on an inner `rgb(17,17,24)` layer | new inner layer |
| Uploaded-file chip | no dark surface | `rgb(9,9,15)` chip, near-white name | new `stFileChip`, inside the dropzone |
| Tab strip | 4px gap, 1px underline, 16px padding | 16px gap, no underline, no padding | no `data-baseweb="tab"` |
| Secondary buttons, main area | accent tint, centred, weight 600 | sidebar styling: white, left, 400 | unscoped `stBaseButton-secondary` half |
| Sidebar collapse button | shown | hidden | unscoped `stBaseButton-headerNoPadding` half |

After re-anchoring, 1.63.0 measures the 1.37.1 value on every measured row, in
both themes. A scan of every rendered element in light mode (landing page,
mid-analysis, every results tab) finds one dark surface, which 1.37.1 has as
well: the Edit tab's text-area frame. In dark mode no rendered text is dark, on
any tab. Two hover rules now behave as written rather than as they did on
1.37.1, where the unscoped half of each selector list applied them permanently:
the primary button's raised shadow, and the sidebar history buttons' hover fill
and accent border, now show on hover only.

**The dropdown's option rows were measured last, and are right in both themes.**
They mount only while the page is actually drawing, so no hidden pane or tab
ever rendered them and the table above could only give the menu's background. A
screenshot through Chrome's DevTools protocol forces a frame; a read taken
straight after it finds all four `role="option"` rows, each matched by the
`stSelectboxVirtualDropdown` option rule. Contrast is against the ground actually
painted under the text, with translucent tints composited:

| Theme | Row | Text | Ground | Contrast |
|---|---|---|---|---|
| Light | unselected | `rgb(15,23,42)` | `rgb(246,247,249)` | 16.65:1 |
| Light | selected (`rgba(29,78,216,0.1)`) | `rgb(15,23,42)` | `rgb(213,218,237)` | 12.83:1 |
| Dark | unselected | `rgb(241,245,249)` | `rgb(17,17,24)` | 17.16:1 |
| Dark | selected (`rgba(59,130,246,0.14)`) | `rgb(241,245,249)` | `rgb(42,50,75)` | 11.53:1 |

Deleting the three `stSelectboxVirtualDropdown` rules in the page returns the
menu to `rgb(9,9,15)` with near-white text in both themes. So in Light those
rules are what keep a dark menu off a light page; in Dark they swap one dark
ground for another and legibility does not depend on them. Measured locally on
1.63.0. Production's Light menu background was measured earlier at the same
`rgb(246,247,249)`, but its option rows were not.

**Never matched on either version**, so not part of the move, and left alone:
the `.btn-pdf` / `.btn-md` / `.btn-json` and `.options-bar` descendant rules (each
wrapper `<div>` comes from its own `st.markdown` call and closes before the
widgets it was meant to contain), `[data-testid="stFileUploader"] > div:first-child`,
and `[data-testid="stWidgetLabel"] label` (that test ID is the label itself).

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
| Code blocks | `stCode` (was `stCodeBlock`) | near-black slab; the "Extracted text" tab became a full-height wall of it |
| Selectbox control (1.63.0) | `stSelectbox` `[role="group"]` | `rgb(17,17,24)` bar with near-white value |
| Selectbox menu (1.63.0) | `stSelectboxVirtualDropdown` | `rgb(9,9,15)` menu |
| Status header (1.63.0) | `stExpander` `summary` | `rgb(13,13,20)` while an analysis runs; its label 1.08:1 |
| Chat input inner layer (1.63.0) | `stChatInput > div` | `rgb(17,17,24)` behind the typed text, 1.05:1 |
| Uploaded-file chip (1.63.0) | `stFileChip` | `rgb(9,9,15)` chip with a near-white name |

Nine times now, the same root cause, five of them new in 1.63.0. If another turns
up, the fix is the same shape — find the element, set `background` / `color` / `fill` to the
relevant token with `!important`, and add it to this table:

```javascript
// in the browser console, in LIGHT mode, on the offending page
// force a restyle first: computed values can be stale while the page is not drawing
document.body.style.display = 'none'; document.body.offsetHeight; document.body.style.display = '';
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

---

## 7. `yt-dlp` is deliberately NOT pinned — only floored

**Decision: keep it unpinned, and raise the floor to the version verified to
work.** `yt-dlp>=2024.01.01` became `yt-dlp>=2026.8.19`.

This is the opposite of the call made for `torch` and `sentence-transformers`
in §3, so the asymmetry is worth stating rather than leaving implicit.

### What prompted it

A YouTube download failed against **2026.7.4**. Upgrading to **2026.8.19**
fixed it, with no change to this repository. The videos were fine; the
installed extractor was stale. Nothing in the project was wrong, and nothing in
the project could have prevented it.

Unpinned is why the fix was one `pip install -U` away. It is also why a fresh
clone gets whatever is current rather than what was tested. Both halves are
real.

### Why the reproducibility argument does not transfer from §3

`torch` is pinned because four retrieval-eval margins sit between 0.028 and
0.047, narrow enough that a numerical shift across releases could **flip a
scored case with no visible symptom**. That is the shape of risk pinning exists
to remove: a dependency inside the measurement path, whose drift is silent.

`yt-dlp` is not that dependency, on three counts, each checkable:

- **It is in no measurement path.** No test and no eval imports `yt_dlp`
  (`grep -rl yt_dlp tests/` is empty). No number this project reports depends
  on its version. Drift cannot move a result, because it produces no result —
  it produces an MP3 or an error.
- **Its failures are loud.** A stale extractor raises, `_download_youtube_audio`
  returns None, and the pipeline reports the failure at the top of the page.
  That is exactly how the 2026.7.4 breakage presented: visibly, immediately,
  with an error the user could read. Reproducibility protects against silent
  divergence between environments; here divergence announces itself on the
  first run.
- **The API surface it is pinned against is tiny and old.**
  `AudioReaderSkill` calls `YoutubeDL(opts).download([url])` with six options —
  `format`, `postprocessors`/`FFmpegExtractAudio`, `outtmpl`, `quiet`,
  `no_warnings`, `ffmpeg_location`. These are the most stable parts of the
  library. The churn is in the extractors, which is precisely the part a pin
  would freeze in a broken state.

### Why a pin would have a guaranteed expiry date

Measured from PyPI on 1 September 2026, stable releases only (pip does not
install the `.dev0` builds, which are far more frequent):

| | |
|---|---|
| stable releases since 2025-01-01 | 36 |
| median gap between them | 11 days |
| mean gap | 16.7 days |
| longest gap | 84 days |
| total stable releases | 137 |

That cadence is not a project that cannot sit still. It is a project tracking
**server-side changes at YouTube that this repository does not control and
cannot vendor**. A pinned `yt-dlp==2026.8.19` would keep working exactly until
the next such change, and would then be broken for every fresh clone, with no
commit in this repo having caused it. Restoring it would need a commit —
roughly every 11 days, forever, to stay current.

An unpinned dependency that breaks is fixed by `pip install -U yt-dlp`. A
pinned one that breaks is fixed by editing the repository and shipping it. The
recovery for the failure a pin *causes* is strictly more expensive than the
recovery for the failure it *prevents*.

### The honest cost of not pinning

An unpinned install can pick up a **bad** yt-dlp release, and at this cadence
that is not far-fetched. If it happens, a fresh clone is born broken through no
fault of the clone — the same class of failure as the pin, arriving by the
opposite route.

Two things bound it. The failure is loud, so it is diagnosed in one run rather
than mistaken for a project bug. And the fix is to pin *temporarily* to the
last good version — a local, reversible action — rather than to carry a
permanent pin against a hypothetical.

### What the floor actually buys

`>=2024.01.01` was doing no work: it admits **58** stable releases, nearly all
of which are now too old to download from YouTube at all. It expressed no
knowledge.

`>=2026.8.19` states the thing that is actually known: this version was
verified against this code, by the `youtube` stage of `tests/e2e/e2e.py`
downloading and transcribing a real video. It cannot promise a future release
works — nothing can — but it does refuse to install one already known not to.

That is the reproducibility guarantee available for a dependency in an arms
race: **a lower bound on known-good, not an upper bound freezing time.**

### When to revisit

If a yt-dlp upgrade ever breaks the six options above — an API change rather
than an extractor change — that is the signal this reasoning no longer holds,
and an upper bound belongs on the line. Extractor breakage is not that signal,
however often it happens.

---

## 8. Streamlit is pinned to the deployed release — and its internals move

**`requirements.txt` pins `streamlit==1.63.0`**, since 2026-09-14. It used to
say `streamlit>=1.35.0`, so Community Cloud installed the newest release when
it built: **1.63.0** on 2026-09-13, identified by the
`static/js/index.ByR4Z2EF.js` the deployed app serves, which is the file in the
1.63.0 wheel. This machine had **1.37.1**. Every test, every e2e stage and every
in-browser check in this repo ran on 1.37.1 until that date.
`tests/test_streamlit_compat.py` now fails if that line stops being an exact
pin, or if the installed Streamlit is not the pinned one.

That gap took the site down. `ui/app.py` imported `RerunException` from
`streamlit.runtime.scriptrunner.exceptions`, which does not exist on 1.63.0,
and the app died at import with `ModuleNotFoundError`.

### Where `RerunException` has lived

Read from every release's wheel, 1.35.0 to 1.63.0 (50 releases):

| Releases | Defined in | Re-exported by `streamlit.runtime.scriptrunner` |
|---|---|---|
| 1.35.0 – 1.36.0 | `streamlit.runtime.scriptrunner.script_runner` | yes |
| 1.37.0 – 1.37.1 | `streamlit.runtime.scriptrunner.exceptions` | yes |
| 1.38.0 – 1.63.0 | `streamlit.runtime.scriptrunner_utils.exceptions` | yes |

The hard import was valid on two releases of the fifty. `ui/streamlit_compat.py`
now tries the defining modules and the package re-export. If none provides the
class it substitutes a placeholder, so the app starts with the guard off and
logs that it is off. **Import Streamlit internals only through that module**:
`tests/test_streamlit_compat.py` reads every import and fails on a new one.

### What the tests can and cannot catch

`tests/test_streamlit_compat.py` imports `ui/app.py` in a fresh interpreter, in
both the local and the hosted branch. **Run on 1.37.1 it passes with the broken
import restored** — verified by mutation — because that import is valid on
1.37.1. It fails only on 1.63.0. A test speaks for the Streamlit it runs on,
which is why the pin comes with a test that the installed version IS the
pinned one: under the floor, the local version and the deployed one differed
with nothing noticing.

**To upgrade:** change the pin, `pip install` the same version locally, and run
the full suite and `python tests/e2e/e2e.py all`. To try a release before
pinning it, use a venv made with `--system-site-packages` and
`pip install streamlit==<version>` into it.

### What moving this machine from 1.37.1 to 1.63.0 changed

Measured on 2026-09-14, after `pip install streamlit==1.63.0` into the base
environment:

- **Installed alongside it:** httptools 0.8.0, python-multipart 0.0.32,
  starlette 1.6.0, uvicorn 0.53.0, websockets 16.1.1. Nothing else was
  upgraded, downgraded or removed.
- **`pip check`:** three conflicts became two. `streamlit 1.37.1 requires
  protobuf<6` is gone, because 1.63.0 declares `protobuf<8,>=5.26.1`. The
  opentelemetry-proto/protobuf and shap/numpy conflicts are unchanged and
  unrelated.
- **conda follows it.** 1.37.1 came from a conda package; after pip replaced
  it, `conda list` reports `streamlit 1.63.0 pypi_0 pypi`.
- **Tests:** 596 passed on 1.37.1 and 598 on 1.63.0, the two extra being the
  version checks above. Compared per test, nothing that passed on 1.37.1
  fails on 1.63.0. `python tests/e2e/e2e.py all`: 8/8 PASS on 1.63.0.
- **What did break was the stylesheet**, which no test read against a live page:
  thirteen surfaces, measured and fixed in section 5.

### `runner.fastReruns` must stay off

The mid-run guard in `ui/app.py` defers a widget change until an analysis has
finished. **It never worked in the running app until this was set**, on either
version, and its tests passed throughout because they raise `RerunException` by
hand. With Streamlit's default `fastReruns = true`, a change mid-run makes
Streamlit *stop* the script and start a new one; nothing is raised for the
guard to catch. Measured in the browser: a theme switch ended the analysis 0.4s
later on 1.37.1 and 2.2s later on 1.63.0. `.streamlit/config.toml` sets it to false and
`tests/test_run_interruption.py` pins it, including a check that Streamlit
itself loads it — a renamed option is ignored, not rejected.

**The file was not enough in production, so `ui/app.py` also turns it off.**
After the commit that set it, and again after a reboot on 2026-09-14,
llm-docagent.streamlit.app discarded an analysis 1.0s after a mid-run theme
switch, which is the fastReruns-on behaviour. Its colours match a 1.63.0 server
reading the repo's `.streamlit/config.toml`, so Cloud reads that file and
something above it sets this one option. An environment variable or a
command-line flag would: locally on 1.63.0, `STREAMLIT_RUNNER_FAST_RERUNS=true`
with the same config discarded the run 0.1s after the switch, while the config
alone kept it (the analysis completed 18.3s after the switch, which then
applied at 19.5s). Which one Cloud sets cannot be seen from here. `ui/app.py`
turns the option off at import whatever set it, and logs the source, so Cloud's
log will name it. `tests/test_run_interruption.py` applies the option the way
`streamlit run` applies a flag or environment variable, and checks the app
overrides it. The variable alone does nothing in a plain Python process: the
CLI reads it, not the config module.

**The cost, measured on 1.63.0 with a server-side timing hook:**

| Situation | Measured |
|---|---|
| No analysis running | change applies in 0.11 – 0.22s (4 switches) |
| Change reaches the running script | 0.78s and 2.98s after the click (two runs) |
| Longest gap between Streamlit calls in a run | ~9.5s, one model call |
| Change applies, warm process | 25.2s after the click, when the analysis finished |
| Change applies, first analysis after a cold start | 82.2s: the analysis plus loading the embedding model |

So during an analysis a widget change waits for the analysis, by design, and
nothing visible happens until then. Not measured: a stage that makes no
Streamlit call for a long time, such as OCR on a long scanned PDF, where the
change would also wait to be delivered.

## 9. `xlrd` — legacy `.xls`, supported rather than dropped

**The finding.** `.xls` was advertised by the uploader, admitted by
`ALLOWED_EXTENSIONS` and `SUPPORTED_EXTENSIONS`, and listed on the public
deployment, and no real `.xls` could be read. `ExcelReaderSkill` sent every
non-CSV file to openpyxl, which refuses the legacy BIFF8 format outright. The
error box said "Parsing failed"; the status panel's stage list carried
openpyxl's sentence telling a website visitor to use xlrd. Every other
advertised format was checked from a real file and reads.

**What hid it.** xlrd 2.0.2 was already installed on the development machine,
put there by the Anaconda distribution, required by nothing in this project and
absent from `requirements.txt`. The reader never called it, and a hosted build
would not have had it. `tests/test_xls_reader.py` fails if the requirement line
goes.

**Why support it rather than stop advertising it.** Dropping `.xls` costs
nothing to build and would turn an unreadable upload into a clear rejection.
Supporting it costs a dependency, and that cost measured small:

| | |
|---|---|
| wheel | `xlrd-2.0.2-py2.py3-none-any.whl`, 96 kB, pure Python, no dependencies |
| installed | 383 KB, 29 files |
| Python 3.12 | resolves for cp312 on manylinux as a binary wheel |
| import | +4 MB |

Measured on a 6.9 MB Excel-written `.xls` of 30,000 rows × 18 columns and the
same data saved as `.xlsx`, each case in a fresh process. The figures are
Windows peak working set; the hosted tier is Linux, so read them as the size of
the cost rather than the exact number:

| | peak above start | time |
|---|---|---|
| xlrd: open, and read every cell | +32 MB | 1.1 s |
| openpyxl on the `.xlsx`: the same | +237 MB | 12 s |
| `ExcelReaderSkill` end to end, `.xls` | +104 MB | 1.9 s |
| `ExcelReaderSkill` end to end, `.xlsx` | +292 MB | 13.5 s |

On the 1 GB hosted tier the new path is the cheapest spreadsheet read the app
does. A `.xls` at the 10 MB upload cap was not measured; scaled linearly from
the row above it would be roughly +150 MB, still about half what the existing
`.xlsx` path costs for 6.9 MB of data.

**How it is used.** The reader chooses its engine from the file's first bytes,
not its name. An OLE2 compound document goes to xlrd. A ZIP goes to openpyxl,
opened from a file object, because openpyxl refuses by extension before reading
a byte, so an `.xlsx` renamed `.xls` used to fail on its name alone. Anything
else is refused with a sentence the user can act on. xlrd returns every number
as a float and every date as a serial, so cells are converted to the text the
`.xlsx` path produces; one workbook reads identically saved either way, checked
on three Excel-written pairs, one holding dates, times of day, booleans, a
formula error and a merged cell.

**What it does not do.**
- Formula text: through xlrd, BIFF8 gives values only, so `include_formulas`
  does not apply to `.xls`.
- `.xlsb`, which is neither advertised nor read.
- A web page or SpreadsheetML XML saved with a `.xls` name is refused with a
  message, not converted.

**Untrusted bytes.** xlrd parses attacker-supplied files on the public
deployment. It is pure Python, so a malformed file raises rather than
corrupting memory, and the reader turns that into a sentence: a truncated file
used to surface a bare `IndexError` after warnings on stdout, and now reads
"may be damaged". The upload cap bounds the size.

**After changing it.** A `requirements.txt` change needs a reboot on Community
Cloud (DEPLOYMENT.md, "Redeploying after a push"). Then run
`pytest tests/test_xls_reader.py tests/test_advertised_formats.py`.

---

## Verifying any dependency change

After changing anything above, the full check is:

```bash
python tests/e2e/e2e.py all
```

```bash
pytest tests/ -q
```

Expect 8 `PASS` rows with exit code 0, and 379 passing unit tests. The
eighth stage is `empty`, which asserts that a document with no content
produces no classification verdict (see CLAUDE.md).

Retrieval quality is scored separately, with no API calls:

```bash
python tests/e2e/rag_eval/run_eval.py
```

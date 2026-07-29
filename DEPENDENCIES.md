# Dependency Notes

Known dependency problems in this environment, why they matter, and the exact
commands to resolve them.

> **Nothing in this document has been run.** These are documented resolutions for
> you to execute deliberately, not a changelog. Every version below was observed
> on the development machine on 2026-07-29 via read-only inspection.
>
> Both issues are currently **latent** — the full pipeline passes all 6 e2e
> stages and all 41 unit tests as-is. They are recorded because they make the
> environment non-reproducible, not because anything is failing today.

---

## 1. Three conflicting OpenCV distributions

### What is installed

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

---

## 2. pandas 3.0.3 is ahead of its acceleration dependencies

### What is installed

| Package | Installed | pandas 3.0.3 requires |
|---|---|---|
| `pandas` | 3.0.3 | — |
| `numexpr` | 2.8.7 | **>= 2.10.2** |
| `Bottleneck` | 1.3.7 | **>= 1.4.2** |
| `numpy` | 1.26.4 | satisfied |

Every run that touches the Excel/CSV path emits:

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

## 3. Also worth knowing

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

## Verifying any dependency change

After changing anything above, the full check is:

```bash
python tests/e2e/e2e.py all
```

```bash
pytest tests/ -q
```

Expect 6 `PASS` rows with exit code 0, and 41 passing unit tests.

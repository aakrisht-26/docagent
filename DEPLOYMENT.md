# Deploying DocAgent to Streamlit Community Cloud

How this app gets to a public URL, what it needs there, and why several things
are set the way they are. Written to be followed cold — if you are reading this
having forgotten the whole project, start at [Deploy it](#deploy-it) and come
back for the reasoning.

**Nothing here changes local development.** Every hosted behaviour is behind
`is_hosted()` in `utils/config.py`, and the one dependency difference is scoped
by a platform marker. Running `streamlit run ui/app.py` on your machine behaves
exactly as it always has.

---

## Deploy it

### 1. Push the branch and merge it

Community Cloud deploys from a GitHub branch. It needs the repo to be readable
by your account — it already is.

### 2. Create the app

1. Go to <https://share.streamlit.io> and sign in with the GitHub account that
   owns `aakrisht-26/docagent`.
2. **Create app** → **Deploy a public app from GitHub**.
3. Fill in:
   - **Repository**: `aakrisht-26/docagent`
   - **Branch**: `main`
   - **Main file path**: `ui/app.py`  ← not `app.py`, and not the repo root
4. Before clicking Deploy, open **Advanced settings** and do step 3.

### 3. Set the secrets

In **Advanced settings → Secrets**, paste TOML. This is the same content a
local `.streamlit/secrets.toml` would hold, and it is *not* a file in the repo
(`.gitignore` excludes it deliberately).

```toml
# Tells the app it is hosted. Drives per-visitor history, the upload cap and
# the OCR page cap. See "What hosted mode changes" below.
DOCAGENT_HOSTED = "true"

# One key, or several comma-separated for round-robin failover.
GROQ_API_KEY = "gsk_your_key_here"
# GROQ_API_KEYS = "gsk_one,gsk_two"
```

> **Use a key that is not in your local `.env`.** The deployment is open, so a
> stranger can burn its daily quota. A separate key means that stops the demo,
> not your own work. See [docs/OPEN-DEPLOYMENT-RISKS.md](docs/OPEN-DEPLOYMENT-RISKS.md).

### 4. Deploy, then watch the first build

The first build takes several minutes — it installs `packages.txt` with apt,
then the whole of `requirements.txt`, which includes torch. Open **Manage app**
in the lower right to watch the log. It is normal for it to sit quietly on the
torch download for a while.

### 5. Check it came up correctly

In the app: upload `tests/e2e/samples/sample_report.pdf`. You should get a
multi-paragraph summary with page citations, and the sidebar should report the
summary method as `llm_single_groq`.

**Check the method, not the confidence.** An earlier version of this step said
to expect "a confidence around 96%". That fixture actually classifies as
`normal_document` at confidence 0.00, and does so with the LLM disabled too, so
the number was never evidence the deploy worked. The summary method is: it is
`extractive` whenever the LLM did not run, which is exactly the failure this
step needs to catch.

In the log (**Manage app**), confirm three lines:

```
Runtime: hosted (Community Cloud)          <- DOCAGENT_HOSTED was read
Loaded N value(s) from Streamlit secrets.  <- secrets reached the app
USAGE | session=... | event=analyze:file   <- usage logging is live
```

If the summary looks thin and generic, the Groq key did not arrive — see
[Troubleshooting](#troubleshooting).

---

## The two files that make it work

### `requirements.txt`

Standard, with one deployment-specific block:

```
--extra-index-url https://download.pytorch.org/whl/cpu
torch==2.7.1+cpu ; sys_platform == "linux"
```

**Why.** The default PyPI `torch` wheel for Linux is a **CUDA** build: 527 MB
itself, plus `nvidia-cudnn`, `nvidia-nccl`, `nvidia-cusparselt`,
`nvidia-nvshmem` and `triton` as hard dependencies — about **1.5 GB downloaded,
3 GB unpacked**, none of it usable on a CPU-only container. Those dependencies
carry a `platform_system == "Linux"` marker, which is exactly why a Windows or
macOS install never sees them and why this is invisible until you deploy.

**Why the marker.** `sys_platform == "linux"` means the line is skipped on your
machine, where torch keeps arriving as a plain transitive dependency of
`sentence-transformers`. Verified by diffing `pip install --dry-run` output
before and after: byte-identical locally.

**Why `+cpu` and not just a version.** `+cpu` is a local version that exists
only on the PyTorch index, so pip is forced to take it from there. An
`--extra-index-url` on its own would leave the choice between the two indexes
ambiguous.

**Why 2.7.1 specifically.** It matches the version used locally. The retrieval
eval scores 33/33 retrieved and 32/33 leading the ranking, and four of its margins sit
between 0.028 and 0.047 — narrow enough that numerical drift across releases
could flip a case silently. Newer wheels exist; parity is worth more than
novelty here. If you ever change this line, re-run the eval (below) — it is
free.

### `packages.txt`

Community Cloud runs `apt-get install` on each line before installing Python
packages. Two system binaries no pip package can supply:

- **`ffmpeg`** — yt-dlp needs it to convert downloaded audio to MP3, and it
  brings `ffprobe` along. Without it the YouTube path fails with a clear
  message; uploads are unaffected.
- **`tesseract-ocr`** — the OCR engine behind `pytesseract`, used for scanned
  PDFs with no text layer. Without it a scanned PDF yields no text.

This file does nothing locally.

---

## What hosted mode changes

All of it keys off `is_hosted()`, which is true when `DOCAGENT_HOSTED` is set,
or (as a backstop) when the app is running from `/mount/src`, where Community
Cloud checks the repo out.

| | Local | Hosted | Why |
|---|---|---|---|
| History database | Shared `~/.docagent/history.db`, persistent | One SQLite file **per browser session**, in the container temp dir | Community Cloud runs **one container for every visitor**. A shared database would show one stranger's document, its text and chat, to the next stranger. |
| Upload cap | 50 MB | 10 MB, enforced in the **browser** | Bounds what a single visitor can push into a ~1 GB container. |
| OCR page cap | 500 pages | 25 pages | This, not the upload cap, is what actually bounds memory — see below. |
| Groq keys | `.env` | `st.secrets` | No `.env` exists on Cloud. |
| YouTube downloads | Usually work | **Frequently blocked** | Community Cloud egress IPs are shared by every app on the platform and are heavily rate-limited by YouTube. See below. |

### YouTube is unreliable when hosted, by design of the platform

**Expect this. It is an external limitation, not a bug, and there is no fix
available from inside this repository.**

YouTube challenges downloads by IP reputation. Streamlit Community Cloud runs
every app from a small pool of **shared** egress addresses, which are therefore
seen by YouTube as high-volume, unattributable traffic and are challenged far
more aggressively than a residential connection. The response looks like this:

```
Sign in to confirm you're not a bot. This helps protect our community.
```

The app surfaces it as a download failure that names the cause, so a hosted
user is told the network was refused rather than being left to think the video
or the app is broken.

**How often** is not something this project can promise a number for — it
depends on the platform's address pool and on YouTube's current thresholds,
both of which change without notice. Treat a hosted YouTube URL as best-effort.
It was already observed failing on a *home* connection minutes after the same
video succeeded, so a shared cloud IP is strictly worse.

**What is unaffected.** Everything else: PDF, Excel, CSV and direct audio
uploads never touch YouTube. Only the URL path is exposed to this.

**What would fix it**, none of which is currently done or recommended lightly:

| approach | why it is not in place |
|---|---|
| Cookies from a signed-in account | Puts a real account's credentials in the deployment's secrets, and YouTube bans accounts used this way. A hosted app shared with strangers makes it worse — a single visitor's URL is downloaded on that account's behalf |
| A proxy with residential IPs | A paid dependency and a new failure mode, for a secondary input path |
| Dropping the YouTube feature when hosted | Reasonable if it proves more confusing than useful; the input path is already isolated enough to gate on `is_hosted()` |

Until one of those is chosen, the honest position is the one taken here:
**let it fail, say clearly why, and make sure the failure cannot be mistaken
for something the user did wrong.**

### The upload cap is layered, not airtight

Streamlit enforces upload size in two places and only one is reachable from
application code:

- **tornado's `max_buffer_size`** is fixed when the process binds, from
  `.streamlit/config.toml`. Community Cloud gives you no way to set an
  environment variable or CLI flag before startup — secrets only become env
  vars once something reads `st.secrets`, long after the server is listening —
  and `config.toml` is one committed file serving both environments. **So the
  hard ceiling stays at 50 MB.**
- **the file-uploader widget's limit** is read from config on *every render* and
  sent to the browser. `ui/app.py` sets it to 10 MB when hosted, so a normal
  browser refuses a larger file and never starts the upload.

Net: 10 MB for real traffic, 50 MB hard ceiling for a handcrafted request,
`validate_file()` rejecting anything past both.

### Memory, and what happens at the limit

Measured, not estimated:

| | RSS |
|---|---|
| App with a document, no embeddings | ~168 MB |
| torch + sentence-transformers + model + 40 encodes | ~528 MB |
| **Together, steady state** | **~643 MB** |
| Peak during OCR of a 25-page scan | ~+250 MB |

The embedding model is a **process-level singleton**, so concurrent visitors
share one copy rather than each paying 475 MB. Extra visitors add only their
own document state.

The important finding: **peak memory tracks page count, not file size.** A
50 MB / 40-page scan peaked at 446 MB and a 10 MB / 40-page scan at 447 MB —
essentially identical, because the cost is rendering each page to a bitmap for
Tesseract (~315 MB fixed, ~2.4 MB per page). A 10 MB PDF can carry hundreds of
pages, so capping bytes alone leaves the spike unbounded. Hence the page cap.

**At the limit, the container is killed.** An out-of-memory kill is a SIGKILL:
no traceback, no fallback, nothing in the log explaining it. Community Cloud
restarts the app and every concurrent visitor loses their session and their
per-session history. The `try/except` around the embedding load cannot catch
this — it is not an exception.

Symptom to recognise: the app becomes unresponsive, then comes back empty, with
the log showing a fresh startup and no error before it.

---

## Redeploying after a push

**Normal changes** — Python, CSS, docs: Community Cloud watches the branch and
redeploys automatically within a minute or two of the push. Nothing to do.

**Changes to `requirements.txt` or `packages.txt`**: these only take effect on
a full rebuild. Use **Manage app → ⋮ → Reboot app**. Expect several minutes.

**Changes to secrets**: edit them in **Manage app → Settings → Secrets**. Saving
restarts the app. Secrets are *not* in the repo, so they survive a rebuild and
are not affected by a push.

**If a deploy seems stuck**, open the log first — the torch download genuinely
takes a while on a cold build and looks like a hang.

---

## Verifying a deployment

Free and worth doing after any dependency change:

```bash
# Retrieval quality — no API calls, prints the headline number.
python tests/e2e/rag_eval/run_eval.py
```

Expect **33/33 retrieved, 32/33 leading the ranking** on the meaningful
fixtures (the worst-rank diagnostic reads 28/33, which is ITS ceiling — see
`docs/retrieval-sub-chunking.md`).
These are LLM-independent — the eval makes no API call, so they do not move when
the Groq model changes, and a drop means the embedding path really did change. If
either has dropped, the torch pin is the first suspect: embedding values can
shift across releases and four margins sit between 0.028 and 0.047. Running it
against whatever Cloud actually installed is the only way to see the real
number.

```bash
# Everything else, locally.
python -m pytest tests/ -q          # 121 tests
python tests/e2e/e2e.py all         # 6 stages, needs a Groq key
```

---

## Reading the usage logs

Every analysis and every chat query emits one line to stdout, which is what
**Manage app** shows:

```
USAGE | session=893baa76 | event=analyze:file | session: 1 op(s), 3 call(s),
2,225 tok | process: 3 call(s), 0 transcription(s), 2,225 tok, 0 cache hit(s)
in 0.01h | rate n/a (<10m uptime)
```

- **`session=`** — random per-browser-session id, for correlating one visitor's
  activity. Not a user identity; nothing personal is logged.
- **`event=`** — `analyze:file`, `analyze:youtube`, or `chat`. Chat is counted
  separately because a visitor can ask one document a hundred questions without
  ever triggering a second analysis.
- **`session:`** — best-effort attribution, by diffing process totals around the
  operation. Overlapping runs can cross-attribute.
- **`process:`** — exact, since the container started. **This is the figure to
  watch against a daily key limit.**
- **rate** — withheld below ten minutes of uptime, because one document in the
  first thirty seconds extrapolates to an absurd figure.

The line is deliberately one physical line with a fixed prefix, so it survives
`grep` and does not interleave when several visitors are active.

**What to watch for.** Free-tier Groq allows **100,000 tokens per day** per key
and a run costs ~2,000–3,000, so **one key funds roughly 30–50 analyses a day**.
If the process total approaches that, the demo is about to start silently
degrading to extractive summaries. Nothing caps it — see
[docs/OPEN-DEPLOYMENT-RISKS.md](docs/OPEN-DEPLOYMENT-RISKS.md) for what a cap
would cost to add.

---

## Troubleshooting

**Summaries are generic and there are no page citations.**
The Groq key did not arrive. Check the sidebar for a configuration warning, and
the log for `Loaded N value(s) from Streamlit secrets.` — if N is 0 or the line
is missing, the secret name is wrong. It must be exactly `GROQ_API_KEY` (or
`GROQ_API_KEYS`) at the **top level** of the TOML, not nested in a section.

**A YouTube URL fails with "Sign in to confirm you're not a bot".**
Expected on Community Cloud — the platform's egress IPs are shared and heavily
rate-limited by YouTube. Nothing is misconfigured, and retrying the same URL
later sometimes works. File uploads are unaffected. See *YouTube is unreliable
when hosted* above for why, and for the options that would change it.

**The e2e suite exits 3.**
At least one stage was blocked externally and did not run — in practice the
`youtube` stage being rate-limited. Nothing failed, but the run is incomplete,
which is why it is neither 0 nor 1. The summary names the blocked stages.

**The sidebar shows other people's documents.**
`DOCAGENT_HOSTED` is not set and the `/mount/src` backstop did not fire. Set it
in secrets. Confirm with `Runtime: hosted (Community Cloud)` in the log.

**Everything is slower than it used to be.**
Expected. `openai/gpt-oss-120b` reasons before it answers and is roughly **4x
slower per call** than the retired `llama-3.3-70b-versatile`. Measured:

| | 70b | gpt-oss-120b |
|---|---|---|
| a typical summary | ~2s | **12-18s** |
| `e2e.py pdf`, end to end | — | 11s |
| a 20-page report, end to end | — | 91s (61s in summarisation) |

Free-tier keys also carry an **8,000 tokens-per-minute** limit alongside the
daily one, and a large document now brushes against it. The client parks a
throttled key and rotates to the next, so this surfaces as `tokens/min headroom
low` warnings rather than failures — but with a single key it would be a stall.
Configure several keys via `GROQ_API_KEYS` for anything beyond casual use.

**A summary falls back to extractive with a "per-minute token limit" warning.**
The tier refused the request size on every key. This is the free tier's rolling
per-minute allowance, not a broken model — the same request often succeeds a
minute later. Pick a shorter summary length, or add more keys via
`GROQ_API_KEYS` so rotation has somewhere to go. The log distinguishes this
from a model failure explicitly, and says so in those words.

**The "Exhaustive" summary length falls back to extractive.**
**Fixed** — Exhaustive is now 6000 rather than 8000, and a 413 rotates to
another key instead of ending the call. Both were needed for different reasons.

The rotation is the substantive fix: a 413 is the tier declining a request
against its ROLLING per-minute allowance, so it describes one key at one moment
and another key will usually serve it. Previously a single refusal returned
None and the summary dropped to extractive silently.

The lower value is cleanup: Exhaustive never used 8000. Measured across three
documents its longest output was 3783 tokens. 6000 keeps it the largest budget
with ~2200 tokens of headroom while shrinking the request by 976.

**Neither makes acceptance certain.** The window is time-varying, so a large
document at a long summary length can still be refused on every key at a busy
moment. It will now say so plainly rather than quietly shortening your summary.

**Summaries are short and flat, and the method reads `extractive`.**
The LLM is not running. Two causes, and they need different fixes. If the log
shows `No API key found`, the secret did not arrive — see above. If it shows
`404 ... model_not_found`, the key is fine and the **model** is gone: Groq
retires models on notice, and it retired the previous default,
`llama-3.3-70b-versatile`, on 17 June 2026. Nothing raises when this happens.
Every stage falls back to extractive and the app keeps serving, which is right
for a visitor and means a dead model can sit in a deployment unnoticed for as
long as nobody reads a summary closely.

Check <https://console.groq.com/docs/deprecations>, then set `groq.model` in
`configs/default.yaml` or add `DOCAGENT_GROQ_MODEL` to the secrets.
`python tests/e2e/e2e.py pdf` refuses to start against an unreachable model, so
run it before redeploying.

**Scanned PDFs produce no text.**
`tesseract-ocr` missing from `packages.txt`, or the file was edited without a
rebuild. Reboot the app.

**YouTube links fail but uploads work.**
`ffmpeg` missing from `packages.txt` — same fix. Note YouTube also rate-limits
datacentre IP addresses, so a download can fail for reasons that are nothing to
do with this app; the log distinguishes the two.

**Chat answers got noticeably worse.**
Check the caption above the chat box. If it says *keyword overlap*, the
embedding model is not loading — either `sentence-transformers` failed to
install, or the container ran out of memory during the load. The app says which
retrieval method is live precisely so this is visible rather than silent.

**The app restarts by itself, losing session history.**
Almost certainly an out-of-memory kill. See
[Memory, and what happens at the limit](#memory-and-what-happens-at-the-limit).
If it is reproducible, the document that triggers it is the thing to look at —
page count first, not file size.

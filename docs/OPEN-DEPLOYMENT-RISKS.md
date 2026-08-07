# What an open deployment exposes

Assessment only. **Nothing here is implemented** — the deployment is
deliberately open, with no password, no gating and no caps. This exists so the
decision to leave it open is an informed one, and so the cheapest fixes are
already written down if that changes.

Figures are measured on this app, not generic advice.

---

## 1. Your Groq quota is the exposed asset

This is the real risk. Everything else is smaller.

**The numbers.** Free-tier Groq allows **100,000 tokens per day (TPD)** per key,
and TPD is invisible in response headers — see the rate-limit section in the
README. One analysis of the small sample report measured **2,225 tokens**; the
README puts a typical run at 2,000–3,000.

> **A single key funds roughly 30–50 document analyses per day.**

**What a stranger can do.** Nothing clever is required. Upload a document,
click Analyze, repeat. There is no account, no rate limit and no cost to them.
A person doing it by hand exhausts a key in a few minutes of clicking; a
trivial script does it faster. With *N* keys in rotation the ceiling is *N* ×
100,000 tokens, and the rotation logic will work through every key before
anything fails visibly.

**The quieter version.** Chat is cheap per call but unbounded in count. A
visitor can ask one document a thousand questions without ever triggering a
second analysis. Per-analysis thinking misses this, which is why the usage
logging counts `chat` events separately.

**What it costs you.** Not money — the free tier does not bill overage, it
refuses. It costs *availability*: once TPD is gone, the demo silently degrades
to extractive summaries and keyword answers for the rest of the day, which is
precisely the impression you do not want a recruiter to get. If the same keys
are in your `.env`, your local work stops too.

**Mitigations, cheapest first**

| Effort | Mitigation | Effect |
|---|---|---|
| 2 min | **Use a separate Groq key for the deployment**, not the ones in your local `.env`. | Exhaustion can no longer stop your own work. Best value here by a wide margin. |
| ~10 lines | **Refuse new analyses past a daily token budget.** `get_process_usage()` already tracks the exact figure; compare it against a threshold before the run and show a "demo quota reached, try tomorrow" state. | Turns silent degradation into an honest message, and preserves the remaining quota. |
| ~10 lines | **Per-session soft cap** (say 5 analyses), held in `session_state`. | Stops casual repetition. Bypassable by opening a new session, so it is a speed bump, not a control. |
| ~5 lines | **Disable the YouTube path when hosted.** It is the most expensive request the app accepts: a download, a Whisper transcription, then the full pipeline. | Removes the costliest single action. |
| config | **Streamlit Community Cloud viewer allowlist** (make the app private, invite by email). | Complete fix, but stops it being a link on a résumé. |

---

## 2. What an upload can do

**Denial of service through memory.** The container is roughly 1 GB and shared
by every visitor. Measured: the OCR path costs about 315 MB fixed plus ~2.4 MB
per page, and peak memory tracks **page count, not file size** — a 50 MB /
40-page scan and a 10 MB / 40-page scan both peaked near 446 MB. An OOM is a
SIGKILL: no traceback, no graceful fallback, the container restarts and every
concurrent visitor loses their session.

Already mitigated in this branch: the browser-side upload cap (10 MB hosted)
and `pdf.max_pages` capped at 25 when hosted, which together put the worst case
near 885 MB. **Residual:** tornado's hard ceiling is still the 50 MB from
`config.toml`, because Community Cloud offers no way to set it lower per
environment, so a handcrafted POST can still push a 50 MB body. The page cap
bounds what happens next.

**Untrusted input reaching parsers.** `pdfplumber`, `PyMuPDF`, `openpyxl` and
Tesseract all parse attacker-supplied bytes. These are widely used and
reasonably hardened, but they are C-backed parsers and this is the classic
place for a memory-safety bug. There is no sandbox: a parser exploit runs with
the app's privileges, which includes reading `st.secrets` — i.e. your API keys.

*Cheapest mitigation:* keep the dependencies current; that is genuinely most of
it. A real sandbox is disproportionate for a portfolio demo.

**Prompt injection.** Document text goes into LLM prompts. A document
containing "ignore previous instructions and…" can steer the summary. The blast
radius is small — the output is shown to the person who uploaded it, and the
app gives the model no tools, no network and no ability to act — so the worst
case is a misleading summary in the attacker's own browser. Worth naming, not
worth engineering against here.

**Content you did not choose to host.** Anyone can upload anything. Nothing is
persisted beyond the container's life, and history is now scoped per session so
one visitor cannot see another's document, but for a period your deployment is
processing and displaying a stranger's content under your name.

---

## 3. Smaller items

**One noisy visitor degrades everyone.** Single container, shared process. A
long OCR run blocks; an OOM restarts the container for all sessions.

**YouTube fetching from your IP.** `yt-dlp` downloads on the server's behalf.
Restricted to YouTube URLs by validation, so it is not a general SSRF, but the
container's address is doing the fetching and YouTube may rate-limit it — one
of the runs during this work already failed that way.

**Log contents.** Usage lines carry a random session id, event name and token
counts — no document text, no filenames, no key material. Key candidates are
logged only as `[redacted, N chars]`. Worth preserving if the logging is
extended.

**Secrets exposure.** Keys live in `st.secrets`, are never rendered, and are
not in the repo. The realistic leak path is a code change that logs a config
dict, not the deployment itself.

---

## If you only do one thing

Use a **separate Groq key for the deployment**. It takes two minutes, costs
nothing, and converts the most likely bad day — quota exhausted by a stranger —
from "my demo and my local work are both broken" into "my demo is degraded
until tomorrow".

The second thing, if the link ever gets real traffic, is the daily token budget
check: the counter it needs is already there.

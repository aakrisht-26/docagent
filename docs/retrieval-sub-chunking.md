# Sub-chunking: what it changed, and what it cost

The claim under test: *splitting into smaller overlapping passages improves
retrieval on real documents, because a page can hold several unrelated facts and
retrieving the whole page dilutes the match.*

**Verdict: keep it, but the win is narrower than the claim.** It improves
*ranking* on documents with long multi-topic pages and does nothing measurable
elsewhere. It does not improve recall, because recall was already perfect. The
costs are small enough not to matter, including for the hosted deployment.

---

## The eval had to be fixed first

The eval scored 18/18 before this work, which is a ceiling in the eval rather
than evidence that retrieval is perfect. Two independent reasons:

**The fixtures were the wrong shape.** `sample_large_report.pdf` averages **46
words per page** with one topic each. At that size a page already *is* a
passage, so splitting it can only be a no-op. Added
`sample_dense_manual.pdf`: 8 pages averaging ~222 words, six unrelated policy
topics each — the condition the claim is actually about.

**Hit/miss is the wrong metric at these sizes.** The selector takes the top 3
plus first and last as anchors, so on an 8-page document it returns 4–5 of 8
pages. Nearly any question "hits" because more than half the document is in the
context. That measures the selection budget, not the ranking. The runner now
also reports the **rank of the answering chunk**, computed from data it already
had.

---

## Results

Same eval set, same questions, only the chunking changed.

**Read the headline as "leads", not "ranked first".** `required sources lead
the ranking` counts a case when its required sources hold the top *k*
positions, *k* being how many the case needs: rank 1 for a single-source
question, first-and-second in either order for a two-source one. Its ceiling is
33/33 and is attainable.

The older `worst required source ranked #1` is kept as a diagnostic and still
printed, but **its ceiling is 28/33, not 33/33.** It returns the WORST rank
across required sources, and five cases are `match: all` needing two pages —
two sources cannot both be rank 1. So 28/33 was a perfect score being read as
five failures, which is the metric reading backwards rather than retrieval
failing. Four of those five rank their pair [1,2] or [2,1]; only `dn-11` at
[1,3] is genuinely short.
 No API call is
involved in any row, so none of these numbers depends on which Groq model is
configured — they moved only with the chunking, which is the point.

| setting | hit | worst-rank #1 (ceiling 28/33) | mean worst rank | index chunks |
|---|---|---|---|---|
| **page-level (baseline)** | 33/33 | 25/33 | 1.270 | 42 |
| passage 60/15 | 33/33 | 26/33 | 1.210 | 84 |
| passage 80/20 | 33/33 | 28/33 | 1.180 | 67 |
| **passage 100/20 (shipped)** | 33/33 | **28/33** | **1.180** | 61 |
| passage 150/30 | 33/33 | 26/33 | 1.240 | 51 |
| passage 200/40 | 33/33 | 26/33 | 1.240 | 47 |

100/20 ties 80/20 on quality with a smaller index, so it is the default.

**Cases that moved — three improved, none regressed:**

| case | category | baseline | 100/20 |
|---|---|---|---|
| `dn-01` | dilution | rank 2 | **rank 1** |
| `dn-12` | synonym | rank 2 | **rank 1** |
| `dn-15` | adversarial_sibling | rank 2 | **rank 1** |

All three are on the dense fixture, and all three are the shape the claim
predicts. Every case on the two original large fixtures was unchanged — which is
the expected result, since their 46-word pages barely split at all.

**Hit/miss did not move, and that is the honest headline.** Page-level chunking
already put the answering chunk in the context on every case in the set. There
was no recall to recover. The gain is entirely in ranking.

### It costs the keyword fallback two hits

Measured separately, because the fallback serves every query whenever the
embedding model cannot load — which is a real state on the hosted deployment:

| method | chunking | hit | ranked | worst-rank #1 *(ceiling 28/33)* | mean worst rank |
|---|---|---|---|---|---|
| embedding | page-level | 33/33 | 33/33 | 25/33 | 1.27 |
| embedding | passage 100/20 | 33/33 | 33/33 | **28/33** | **1.18** |
| keyword | page-level | **29/33** | 27/33 | 16/33 | 3.06 |
| keyword | passage 100/20 | **27/33** | 26/33 | 17/33 | 3.09 |

Sub-chunking helps embeddings and mildly *hurts* keyword recall. The mechanism
is straightforward: keyword overlap counts shared content words, and a short
passage offers fewer of them, so more chunks score zero and ties are broken
arbitrarily.

Not treated as a blocker, for two reasons. The fallback is already a heavily
degraded mode — 27–29/33 against 33/33 — so two hits is a second-order
difference within it. And it is the path taken only when the model is missing,
where the honest fix is to get the model loading, not to tune the chunking of a
mode nobody wants to be in. It is recorded here so the trade is visible rather
than discovered later.

---

> **On "dilution".** The argument below is about the 256-token embedding
> truncation, which is mechanical and holds. A separate claim — that mixing
> topics on a page costs the answering passage its rank — was tested later with
> a purpose-built fixture and **refuted**: heterogeneity correlates with rank at
> −0.084, and both losses landed on the least-mixed page. See
> [dilution-probe.md](dilution-probe.md). Read the truncation argument as the
> reason passages exist; do not read it as evidence for dilution.

## The mechanical reason, which is stronger than the dilution argument

`all-MiniLM-L6-v2` has `max_seq_length = 256` tokens and **silently truncates**
anything longer before embedding — no warning, no error, the tail simply does
not exist as far as retrieval is concerned.

Measured on the dense fixture: **5 of 8 pages exceed the limit** (max 282
tokens). Page-level embedding was therefore ranking most of that document from a
lossy copy of its text. Passages sized well inside the window remove that
failure mode entirely, independently of any argument about dilution.

This is also the reason the effect is invisible on the older fixtures: at 44–110
tokens per page, nothing there was ever truncated.

---

## One regression, found and fixed

Naive sub-chunking **broke** the cross-boundary case `dn-11`. Several passages
from page 3 swept the top three slots, so page 4 never got one, and a hit became
a miss:

```
baseline   3, 8, 4    <- pages 3 and 4 both present
naive      3, 3, 8    <- page 4 displaced by a second passage of page 3
```

`_select_chunks` now fills its three slots with three distinct **sources**,
taking each source's best passage. With page-level chunks that is a no-op and
behaviour is byte-identical, so nothing already stored is affected.

Worth stating plainly: without this fix sub-chunking was a net *loss* — +3
ranks, −1 hit. The measurement is what caught it.

---

## Costs

Across the three large fixtures:

| | page-level | passages | change |
|---|---|---|---|
| Index chunks | 36 | 53 | ×1.47 |
| Stored chunk JSON | 22.1 KB | 25.4 KB | +15% |
| Stored vectors | 72 KB | 106 KB | +47% |

**Embedding time is flat to within noise**, because shorter sequences cost less
per chunk and more-but-smaller roughly cancels out:

| fixture | pages → passages | page | passage | delta |
|---|---|---|---|---|
| dense manual | 8 → 24 | 77 ms | 100 ms | **+23 ms** |
| large report | 20 → 21 | 81 ms | 64 ms | −16 ms |
| large sales | 8 → 8 | 70 ms | 69 ms | −1 ms |

**Memory, including the hosted deployment.** A vector is 384 float32 = 1.5 KB.
The hosted build caps OCR at 25 pages, so a worst-case hosted document holds
roughly 75 passages ≈ **115 KB of vectors**, against the ~643 MB steady state
measured on the deploy branch. This does not move the 1 GB ceiling and is not
worth a second thought — the ceiling is dominated by torch itself, not by the
index.

---

## Migration

Vectors describe specific text, so re-splitting invalidates them exactly as
changing the embedding model does. That was previously undetectable.

`DocumentStore` gains a **`chunk_scheme`** column recording e.g.
`passage:100/20`. Rows written before this have `NULL`, which reads as
page-level.

**What happens to documents already in history:** on load, a scheme mismatch
means the stored vectors are ignored and that row answers through keyword
overlap instead of embeddings, with a line in the log saying so. Re-analysing
the document rewrites the row under the current scheme and restores embedding
retrieval. Nothing is lost and nothing breaks — the text, the chunks and the
summary all still load.

Verified against a copy of the real database: column added, all 8 rows
preserved and still loading their text and chunks, stored vectors correctly
ignored. The real `history.db` was backed up to
`~/.docagent/history.db.bak-subchunking` and SHA-verified identical first.

---

## Reproducing this

```bash
# Current setting
python tests/e2e/rag_eval/run_eval.py

# Page-level, for the before/after comparison
DOCAGENT_PASSAGE_WORDS=0 python tests/e2e/rag_eval/run_eval.py

# Any other setting
DOCAGENT_PASSAGE_WORDS=80 DOCAGENT_PASSAGE_OVERLAP=20 python tests/e2e/rag_eval/run_eval.py
```

On Windows PowerShell, set the variables with `$env:DOCAGENT_PASSAGE_WORDS="80"`
first. No API calls are made, so this costs nothing to run.

---

## Would I revert it?

No, but the case is narrower than "sub-chunking improves retrieval":

- On documents whose pages exceed 256 tokens it fixes a real, silent truncation
  bug and improves ranking. That is worth having.
- On documents with short pages it does nothing at all, and correctly so — the
  splitter leaves anything under 40 words alone.
- It costs ~50% more vector storage and no measurable time.

If the eval had shown no ranking gain, the truncation finding alone would still
have justified splitting anything over ~200 words. The dilution argument turned
out to be real but small; the truncation one was the substantive problem.

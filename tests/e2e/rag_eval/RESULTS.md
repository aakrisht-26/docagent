# RAG retrieval: embeddings vs keyword overlap

Reproduce with:

```bash
python tests/e2e/rag_eval/run_eval.py
```

Retrieval scoring makes no API calls. The baseline figures come from
[`BASELINE.md`](BASELINE.md).

## Headline

| Method | ranked | PDF | Workbook |
|---|---|---|---|
| Keyword overlap (as shipped) | 10/17 | 9/12 | 1/5 |
| Keyword overlap, tokenizer bug fixed | 13/17 | 9/12 | 4/5 |
| **Embeddings (`all-MiniLM-L6-v2`)** | **17/17** | **12/12** | **5/5** |

Embeddings beat both references, and take every point of remaining headroom.

## By category — fractions, ranked hits

| Category | keyword | tokenizer-fixed | embeddings |
|---|---|---|---|
| `direct` | 4/4 | 4/4 | **4/4** |
| `vocab_overlap` | 4/6 | 6/6 | **6/6** |
| `cross_boundary` | 2/3 | 3/3 | **3/3** |
| `synonym` | **0/4** | **0/4** | **4/4** |

## Cases that flipped

**Against the shipped baseline (10/17 → 17/17), 7 flipped to RANKED:**

| Case | Category | Baseline | Embeddings |
|---|---|---|---|
| `lg-pdf-07` | synonym | MISS | RANKED |
| `lg-pdf-08` | synonym | MISS | RANKED |
| `lg-pdf-09` | synonym | MISS | RANKED |
| `lg-xls-01` | vocab_overlap | MISS | RANKED |
| `lg-xls-02` | vocab_overlap | INCIDENTAL | RANKED |
| `lg-xls-03` | cross_boundary | MISS | RANKED |
| `lg-xls-04` | synonym | MISS | RANKED |

**Against the tokenizer-fixed reference (13/17 → 17/17), 4 flipped — all
`synonym`:** `lg-pdf-07`, `lg-pdf-08`, `lg-pdf-09`, `lg-xls-04`.

**Nothing flipped in the other direction.** No case regressed under embeddings.

### The honest attribution

Three of the seven gains — `lg-xls-01`, `lg-xls-02`, `lg-xls-03` — are **not
embeddings beating keyword overlap**. They are embeddings incidentally fixing a
regex bug: `_tokenize` matches `[a-z]{3,}` and so never sees "Q1" or "Q3", which
made all four quarterly sheets score identically. A one-line regex change
recovers those same three. Crediting them to embeddings would overstate the
case.

**The four `synonym` cases are the genuine embedding win.** Neither keyword
variant scores above `0/4`, because the question and the answering chunk share
no content word at all. Example, `lg-pdf-07`:

> *"How much time were lorries unavailable for work?"* → page 17, which says
> "aggregate downtime … 41,300 off-road hours".

Keyword overlap scores that page **0** and never considers it. Embeddings rank
it **first**, at 0.510 against 0.400 for the best excluded chunk.

## Anchors — unchanged, so the comparison is like-for-like

`_select_chunks` still appends the first and last chunk regardless of the
question. **This was deliberately not changed**, so retrieval is the only
variable between the two measurements.

| | keyword | embeddings |
|---|---|---|
| Anchor slots | 34 | **34** (identical) |
| Genuinely chosen slots | 39 | **47** |
| Total selected | 73 | 81 |

Anchor count is identical, as it must be — it is structural. Chosen slots rose
from 39 to 47 because under keyword overlap pages 1 and 20 frequently scored
into the top 3 *and* were anchors, collapsing two slots into one. Embeddings
rarely rank them, so the three ranked slots are spent on three genuinely
different chunks. Same budget, more distinct content.

Per case, real choices remain **1–3 slots**, never the 4–5 the selection size
suggests.

## Caveat: the "ranked" bar is weaker for embeddings

A hit counts as `ranked` only if the correct chunk **strictly outscored every
excluded chunk**. Under keyword overlap that test does real work, because
integer overlap scores tie constantly — it is what demoted `lg-xls-02` to
`incidental`. Under cosine similarity **exact ties are effectively impossible**,
so the tie test can almost never fire. 17/17 is therefore not passing an
identical bar.

Margin is the honest substitute: `score(expected) − best score among excluded`.

- smallest margin **0.0284**
- median margin **0.2227**
- cases below 0.02: **0 of 17**
- exact ties: **0 of 17**

No win was marginal, but the **three narrowest are all the quarterly-sheet
cases** (`lg-xls-01` 0.0318, `lg-xls-02` 0.0284, `lg-xls-03` 0.0367). Sibling
sheets differ only by a quarter label and four numbers, so those wins are real
but thin, and are the first that would flip on a model change.

## Cost

| | |
|---|---|
| Model | `all-MiniLM-L6-v2`, 384 dims |
| First encode, including load and download | **27.0 s** |
| Encoding 20 chunks, warm | **23 ms** |
| Import cost | **0 ms** — model loads lazily on first use |
| Storage | 20 chunks ≈ 30 KB base64 float32 |

## Recommendation

Embeddings are worth the dependency, but the case rests on `synonym` alone
(`0/4 → 4/4`). Everything else is reachable with a regex fix.

**The tokenizer bug should be fixed regardless.** It is one line, it makes the
keyword fallback substantially better (`10/17 → 13/17`), and that fallback is
what serves every query when the model is missing. It is not in the task list,
so it has not been changed here.

# RAG retrieval: embeddings vs keyword overlap

Reproduce with:

```bash
python tests/e2e/rag_eval/run_eval.py
```

Retrieval scoring makes no API calls. The baseline figures come from
[`BASELINE.md`](BASELINE.md).

## Headline

All three measured on the same **18-case** set, so the progression is
like-for-like. (`BASELINE.md` quotes 10/17 and 13/17; that was before the
adversarial case `lg-xls-06` was added — the ratios are unchanged.)

| Method | ranked | PDF | Workbook |
|---|---|---|---|
| Keyword overlap, as originally shipped | 10/18 | 9/12 | 1/6 |
| Keyword overlap, tokenizer bug fixed | 13/18 | 9/12 | 4/6 |
| **Embeddings (`all-MiniLM-L6-v2`)** | **18/18** | **12/12** | **6/6** |

Embeddings beat both references and take every point of remaining headroom.

Measure any of them yourself:

```bash
python tests/e2e/rag_eval/run_eval.py            # embeddings
python tests/e2e/rag_eval/run_eval.py --keyword  # the fallback path
```

## By category — fractions, ranked hits

| Category | keyword (orig) | tokenizer-fixed | embeddings |
|---|---|---|---|
| `direct` | 4/4 | 4/4 | **4/4** |
| `vocab_overlap` | 4/6 | 6/6 | **6/6** |
| `cross_boundary` | 2/3 | 3/3 | **3/3** |
| `synonym` | **0/4** | **0/4** | **4/4** |
| `adversarial_sibling` | 0/1 | **0/1** | **1/1** |

## The adversarial sibling case

`lg-xls-06` was added specifically because the three narrowest embedding margins
were all quarterly-sheet cases (0.028–0.037). It asks:

> *"In the second quarter, how many consignments did the East region deliver?"*

The quarter is written as **"second quarter", never "Q2"**, so the literal sheet
label gives no help and the tokenizer fix cannot rescue it. The four quarterly
sheets are otherwise near-identical.

| Method | Result |
|---|---|
| Keyword, original tokenizer | `INCIDENTAL` — present only via tie-break |
| Keyword, tokenizer fixed | `INCIDENTAL` — the fix cannot help, there is no `q2` token |
| **Embeddings** | **`RANKED`** — `Q2 Revenue` first, margin **0.0470** |

**It passes**, and it is the clearest single case for embeddings over any regex
change: the fixed tokenizer still cannot do it.

The margin, though, sits in the same thin band as the other quarterly cases
(0.028–0.047) rather than the 0.2+ typical elsewhere. **The thin edge on
near-identical sheets is real.** It holds here, but it is the first thing that
would flip on a model change, and it is worth re-running this case after any
model or chunking change.

## Cases that flipped

**Against the shipped baseline (10/18 → 18/18), 8 flipped to RANKED:**

| Case | Category | Baseline | Embeddings |
|---|---|---|---|
| `lg-pdf-07` | synonym | MISS | RANKED |
| `lg-pdf-08` | synonym | MISS | RANKED |
| `lg-pdf-09` | synonym | MISS | RANKED |
| `lg-xls-01` | vocab_overlap | MISS | RANKED |
| `lg-xls-02` | vocab_overlap | INCIDENTAL | RANKED |
| `lg-xls-03` | cross_boundary | MISS | RANKED |
| `lg-xls-04` | synonym | MISS | RANKED |
| `lg-xls-06` | adversarial_sibling | INCIDENTAL | RANKED |

**Against the tokenizer-fixed reference (13/18 → 18/18), 5 flipped:** the four
`synonym` cases `lg-pdf-07`, `lg-pdf-08`, `lg-pdf-09`, `lg-xls-04`, plus the
adversarial `lg-xls-06`.

**Nothing flipped in the other direction.** No case regressed under embeddings.

### The honest attribution

Three of the eight gains — `lg-xls-01`, `lg-xls-02`, `lg-xls-03` — are **not
embeddings beating keyword overlap**. They are embeddings incidentally fixing a
regex bug: `_tokenize` matches `[a-z]{3,}` and so never sees "Q1" or "Q3", which
made all four quarterly sheets score identically. A one-line regex change
recovers those same three. Crediting them to embeddings would overstate the
case.

**The four `synonym` cases plus the adversarial `lg-xls-06` are the genuine
embedding win** — five cases that no regex change reaches. Both keyword variants
score `0/4` on `synonym` and `0/1` on the adversarial case, because the question
and the answering chunk share no usable term at all. Example, `lg-pdf-07`:

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
| Anchor slots | 33 | 36 |
| Genuinely chosen slots | 41 | **50** |
| Total selected | 74 | 86 |

The anchor *rule* is identical — first and last chunk, always, in both. The
counts differ slightly (33 vs 36) for a mechanical reason rather than a
behavioural one: an anchor is only counted when it is not already in the ranked
top 3. Under keyword overlap pages 1 and 20 frequently scored into the top 3
*and* were anchors, collapsing two slots into one; embeddings rarely rank them,
so they show up as anchors instead.

The number that matters is **genuinely chosen slots: 41 → 50**. Same top-3
budget, spent on more distinct content.

Per case, real choices remain **1–3 slots**, never the 4–5 the selection size
suggests.

## Caveat: the "ranked" bar is weaker for embeddings

A hit counts as `ranked` only if the correct chunk **strictly outscored every
excluded chunk**. Under keyword overlap that test does real work, because
integer overlap scores tie constantly — it is what demoted `lg-xls-02` to
`incidental`. Under cosine similarity **exact ties are effectively impossible**,
so the tie test can almost never fire. 18/18 is therefore not passing an
identical bar.

Margin is the honest substitute: `score(expected) − best score among excluded`.

- smallest margin **0.0284**
- median margin **0.2227**
- cases below 0.02: **0 of 18**
- exact ties: **0 of 18**

No win was marginal, but the **four narrowest are all quarterly-sheet cases**
(`lg-xls-02` 0.0284, `lg-xls-01` 0.0318, `lg-xls-03` 0.0367, `lg-xls-06`
0.0470) against a 0.2227 median elsewhere. Sibling sheets differ only by a
quarter label and four numbers, so those wins are real but thin, and are the
first that would flip on a model change.

## Cost

| | |
|---|---|
| Model | `all-MiniLM-L6-v2`, 384 dims |
| First encode, including load and download | **27.0 s** |
| Encoding 20 chunks, warm | **23 ms** |
| Import cost | **0 ms** — model loads lazily on first use |
| Storage | 20 chunks ≈ 30 KB base64 float32 |

## Recommendation

Embeddings are worth the dependency, but the case rests on **five cases**: the
four `synonym` ones (`0/4 → 4/4`) and the adversarial sibling (`0/1 → 1/1`).
Everything else — three of the eight gains — is reachable with a regex fix.

**The tokenizer fix has been shipped separately** (`fix(rag): let the keyword
tokenizer see letter-digit tokens`), because the fallback serves every query
whenever the model is unavailable and should not stay broken. It takes that path
from `10/18` to `13/18` on its own.

Note the limit of that fix: it cannot touch `lg-xls-06`, where the quarter is
written as "second quarter" and there is no `q2` token to find. That case is the
cleanest single argument for embeddings over any amount of regex work.

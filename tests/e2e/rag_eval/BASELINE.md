# RAG retrieval baseline — keyword overlap

Measured **before** any retrieval changes, on `rag-retrieval` branched from
`main` at `66465b5`. Reproduce with:

```bash
python tests/e2e/rag_eval/run_eval.py
```

Retrieval scoring makes no API calls, so this number is free to re-measure and
is deterministic.

## Method

`DocumentChatSkill._select_chunks()` is called directly with each question and
the parsed chunks of its fixture. A case is a **hit** when the chunk that
actually contains the answer is present in the selected set. `match: "all"`
cases require *every* expected chunk, since neither alone answers the question.

Answer correctness is scored separately (`--with-answers`, one LLM call per
case) and is deliberately not blended into this number: a wrong answer with
correct retrieval is a generation problem, whereas a wrong answer with failed
retrieval is not.

## Why two new fixtures were added

The three original fixtures produce **2 chunks each**, and the selector returns
the top 3 plus first/last anchors — so it hands back the entire document for any
query, including nonsense ones. Retrieval accuracy on them is 100% by
construction and cannot move. They are retained and reported separately as a
sanity check on the harness, not as signal.

`sample_large_report.pdf` (20 pages/chunks) and `sample_large_sales.xlsx`
(8 sheets/chunks) were added so the selector has to choose. Their content is
built to be genuinely hard rather than 20 unrelated topics — see
`fixture_content.py`.

## Baseline: keyword overlap

**Headline, meaningful fixtures only: 11/17 = 64.7%**

| Fixture | Retrieval | |
|---|---|---|
| `sample_large_report.pdf` | 9/12 | **75.0%** |
| `sample_large_sales.xlsx` | 2/5 | **40.0%** |
| `sample_report.pdf` | 2/2 | 100% *(trivial, no signal)* |
| `sample_scanned.pdf` | 2/2 | 100% *(trivial, no signal)* |
| `sample_sales.xlsx` | 2/2 | 100% *(trivial, no signal)* |

### By category (meaningful fixtures only)

| Category | Retrieval | |
|---|---|---|
| `direct` | 4/4 | **100.0%** |
| `vocab_overlap` | 5/6 | **83.3%** |
| `cross_boundary` | 2/3 | **66.7%** |
| `synonym` | 0/4 | **0.0%** |

### What the numbers say

- **Synonym phrasing fails completely — 0 of 4.** This is the expected weakness
  and the clearest target for embeddings. Asking "How much time were lorries
  unavailable for work?" selects pages 9, 13, 18, 1 and 20; the answering page
  17 ("off-road hours") is never considered, because the question and the page
  share no content word at all.
- **The Excel workbook is much worse than the PDF (40% vs 75%)**, and the reason
  is instructive: the `Cost Definitions` sheet repeats the phrase
  "revenue per consignment" for every quarter and region while holding no
  figures, so it outranks the sheet that actually answers. It appears in the
  selected set for **every single** workbook question. Surface-word counting
  rewards a glossary over data.
- **Direct questions are already perfect.** Embeddings have nothing to gain
  here, so an improvement in the headline must come from the other three
  categories.
- **`cross_boundary` at 66.7%** is mostly luck: adjacent pages share vocabulary,
  so selecting one tends to drag in its neighbour.

Any replacement must beat **64.7%** headline, and specifically must move
`synonym` off zero, to be worth the added dependency.

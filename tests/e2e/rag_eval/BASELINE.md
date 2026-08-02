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

## Two things that would otherwise flatter the numbers

### Anchors are not retrieval decisions

`_select_chunks` appends the **first and last chunk regardless of the question**.
On the 20-page PDF that means pages 1 and 20 are in almost every selection, so a
"5 chunk" selection is really **3 ranked choices plus 2 freebies**.

Across the meaningful fixtures: **39 chosen slots + 34 anchor slots = 73
selected**. Roughly *half* of everything handed to the model was structural
padding, not retrieval. Any comparison must count chosen slots, not selected
chunks.

### "Hit" is not the same as "retrieved"

A hit only counts as **ranked** when the correct chunk *strictly outscored every
chunk that was left out*. Anything weaker is **incidental** — present, but not
because retrieval found it.

`lg-xls-02` is the worked example, and it is worse than it looks. `_tokenize`
matches `[a-z]{3,}`, so it **never sees "Q1" or "Q3" at all**. Every quarterly
sheet therefore ties on exactly the same score:

```
lg-xls-01 (asks about Q3):  Q4=4  Q3=4  Q2=4  Q1=4  CostDefs=4  Headcount=1 …
lg-xls-02 (asks about Q1):  Q4=4  Q3=4  Q2=4  Q1=4  CostDefs=4  Headcount=1 …
selection, both questions:  ['Cost Definitions', 'Q1 Revenue', 'Q2 Revenue', 'Incident Log']
```

The two questions produce a **byte-identical selection**. `lg-xls-02` is
"correct" only because `Q1 Revenue` happens to sort first among the ties. The
quarter — the entire discriminating term — is invisible to the retriever.

## Baseline: keyword overlap

**Headline, meaningful fixtures: `any` 11/17, `ranked` 10/17.**
Use **10/17** as the number to beat.

| Fixture | any | ranked |
|---|---|---|
| `sample_large_report.pdf` | 9/12 | **9/12** |
| `sample_large_sales.xlsx` | 2/5 | **1/5** |
| `sample_report.pdf` | 2/2 | 2/2 *(trivial, no signal)* |
| `sample_scanned.pdf` | 2/2 | 2/2 *(trivial, no signal)* |
| `sample_sales.xlsx` | 2/2 | 2/2 *(trivial, no signal)* |

### By category (meaningful fixtures only)

| Category | any | ranked |
|---|---|---|
| `direct` | 4/4 | **4/4** |
| `vocab_overlap` | 5/6 | **4/6** |
| `cross_boundary` | 2/3 | **2/3** |
| `synonym` | 0/4 | **0/4** |

**Incidental hits: `lg-xls-02`** (the only one).

### What the numbers say

- **Synonym phrasing fails completely — 0/4.** Asking "How much time were
  lorries unavailable for work?" selects pages 9, 13, 18, 1, 20; page 17
  ("off-road hours") is never considered, because question and page share no
  content word. This is the clearest target for embeddings.
- **The workbook is far worse than the PDF — 1/5 ranked against 9/12** — and for
  two compounding reasons: the `Cost Definitions` glossary appears in the
  selection for *every* workbook question, and the quarter identifiers are
  invisible to the tokenizer, so the four revenue sheets are indistinguishable.
- **`direct` is already 4/4.** Embeddings have nothing to gain there, so any
  headline improvement must come from the other three categories.
- **`cross_boundary` at 2/3 is partly luck**: adjacent pages share vocabulary, so
  selecting one tends to drag in its neighbour.

To justify the added dependency, a replacement must beat **10/17 ranked** and
specifically move `synonym` off `0/4`.

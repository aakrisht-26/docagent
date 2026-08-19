# Chat across a corpus

How asking one question of every document in history works, what it measurably
does, and — at more length — what it does not.

Scored with:

```bash
python tests/e2e/rag_eval/run_eval.py --multi                 # free, no API calls
python tests/e2e/rag_eval/run_eval.py --multi --with-answers  # 13 LLM calls
```

---

## What changed

Chat was scoped to one document. It can now search everything in history, and
cite the document as well as the page.

Retrieval was never the weak part. Handed all six eval fixtures at once, the
**old single-document selector already ranked the answering passage for 11 of
13** cross-document questions. Embedding similarity separates these documents
well; both deliberately planted wrong-document traps were ranked correctly.

What it could not do was say **where** an answer came from.

| | before | after |
|---|---|---|
| retrieval, ranked slots | 11/13 | **12/13** |
| present only as a structural anchor | 1/13 | **0/13** |
| citations naming their document | 0/13 | **13/13** |
| citations that cannot be resolved to a file | 11/13 | **0/13** |
| replies naming a document in their prose | 0/13 | **13/13** |
| documents sent to the model with no ranked slot | 14 | **0** |

Two defects, both measured before being fixed.

**The dedupe key.** Slots were deduplicated by page label, so page 3 of the
operations review and page 3 of the staff manual were one source and the loser
was unreachable *at any rank*. On `md-13` that discarded the third-highest
scoring passage in the corpus (0.4495) in favour of the fourth (0.3701). The key
is now `(document, page)`.

**The citation.** Every citation was a bare number. 11 of 13 citation lists
named a page that existed in more than one file; pages 1 and 2 each named four
of the six documents. That is not a weaker citation, it is a wrong one.

Labels now read `report.pdf, Page 3`, and `used_sources` carries the document
and the page as separate fields so the UI, the model's prose and the eval all
describe a source the same way.

---

## Is it worse than single-document chat?

**Yes, measurably.** Cross-corpus retrieval is harder and the numbers say so.

| | single-document | cross-document |
|---|---|---|
| required sources lead the ranking | 32/33 (97%) | **9/13 (69%)** |
| mean rank | 1.18 | **2.15** |
| worst rank | 3 | **10** |

Mean rank nearly doubles and the rank-1 rate falls by thirty points. The worst
case moves from rank 3 — comfortably inside any slot budget — to rank 10, which
is outside it. (That worst case is `md-10`, whose answering passage ranks 4th
even for its own sub-question in isolation; the pooled rank of 10 is the two
question parts competing on top of that.)

**Read that with one caveat.** These are different question sets, and the
cross-document cases were written to be hard: topical siblings where two
documents are plausible, and traps where the best-scoring passage is in the
wrong file. A like-for-like comparison would need the same questions asked both
ways, which the fixtures do not support. The direction and rough magnitude are
trustworthy; the exact ratio is not.

---

## `md-10`, and what declining is worth

This case illustrates what the system does and does not guarantee better than
any passing number, so it is documented rather than carried quietly.

**Question.** "How many people does the company employ, and what is the standard
annual leave entitlement?"

The headcount — 1,840 — is on page 1 of the operations review, which does not
reach the six slots. Originally the model answered anyway, summing a two-row
sheet in the trivial sales workbook:

> Adding these gives **96 employees** in the company as of Q3
> [**sample_sales.xlsx, Sheet: Headcount**]

The citation is **honest**: that number really does come from that sheet. The
answer is **wrong**: 96 is a fragment of an unrelated fixture, not the company
headcount. Reproducible **5/5**.

### The cause is COMPETITION, not dilution and not the slot budget

This section previously said dilution: that page 1 is an executive summary, so
the answering clause is drowned out by revenue, depots and fleet, and the page
embeds as something else. **That was measured and refuted.** See
[docs/dilution-probe.md](dilution-probe.md).

Two readings were tested and both failed.

**Not the slot budget.** The obvious reading is that the second question part
steals slots. Asked *on its own*, "how many people does the company employ" is
answered correctly — but page 1 arrives only at rank 4:

| rank | source | score |
|---|---|---|
| 1 | review page 2 (Corporate Structure) | 0.4501 |
| 2 | review page 14 (Warehouse Riverside) | 0.4046 |
| 3 | **sales.xlsx, Headcount** (wrong company) | 0.4023 |
| 4 | **review page 1 — the answer** | 0.3726 |

Per-part retrieval was implemented on that reading and does not recover the
case: it gives the headcount part three slots and the answer is fourth.

**Not dilution either.** A purpose-built fixture varied topic count (1, 2, 6)
against the depth of the answering fact, with single-topic controls at matched
length. Twelve cases, 10 ranked first, and **both losses were on the page with
the FEWEST topics**. The six-topic executive summary beat its dedicated
competitor at 0.7784. Measured heterogeneity — mean cosine distance of each
sentence from its page centroid — correlates with rank at **−0.084**, no
relationship and the sign runs the wrong way.

**What is left is competition.** Page 2 is *entirely about the organisation* and
states no headcount. Page 1 states the headcount inside a paragraph that is
mostly about other things. The question "how many people does the company
employ" is a better match for a page *about the organisation* than for a page
that happens to *contain* the number. In the probe, `dp-01` ranked first among
nine pages and dropped to rank 2 the moment a plant-room page existed — its own
text unchanged.

**This implies re-chunking cannot fix it, and nobody should try.** Splitting
pages into smaller passages changes what each passage contains; it does not
change the fact that a different page is a better match. Both pages get split by
the same rule and the competitor keeps its advantage. The passage-size sweep
confirms it: `md-10`'s rank wanders between 2 and 5 across settings with no
monotone trend, and the shipped 100/20 is already the best setting on the
single-document set.

Addressing competition needs a different class of change — reranking candidates
against the question, or a signal separating "this page is about X" from "this
page states a fact about X". Both are much larger than a chunking parameter and
neither is justified by one case, so this is recorded as understood and open
rather than fixed.

### No similarity threshold can separate this

Measured across both eval sets:

| population | n | min | max |
|---|---|---|---|
| unanswerable questions | 16 | 0.1973 | **0.4663** |
| multi-doc answerable | 12 | **0.4944** | 0.8433 |
| single-doc answerable | 33 | **0.3583** | 0.8504 |
| `md-10` | — | — | **0.5042** |

Unanswerable questions do separate from *multi-document* answerable ones. They
do not separate from **single-document** answerable ones: six of 33 correct
single-doc cases score below the highest unanswerable question, because a
synonym or hard-vocabulary question is a weak match and looks exactly like an
absent one. And `md-10` scores **above** every answerable minimum but its own. A cut
that catches it costs **14 of 33** correct single-document answers.

`nm-01` settles it on its own: "the average maintenance cost per vehicle at the
Western depot", which the corpus cannot answer because no Western maintenance
page exists, scores **0.7784** — higher than 11 of the 12 answerable
cross-document cases. A confident wrong answer is not a low-scoring one.

### What was changed, and what it bought

Refusal was never the missing capability. On the thirteen no-answer cases the
model declined **12/12** of those requiring it, unprompted, before anything was
added — in its own words, "the provided excerpts do not contain any information
about…". Granting permission to decline would have changed nothing.

So the corpus prompt gained a **prohibition** on what was actually happening: no
computing a figure from numbers not presented as a total, no offering a similar
fact about a different depot, tier, period or organisation, and explicit
permission to answer part of a question and decline the rest.

| | before | after |
|---|---|---|
| no-answer cases handled correctly | 12/13 | **13/13** (39/39 over three runs) |
| `md-10` fabricates "96" | 5/5 | **2 in 25 trials (~8%)** |
| `md-10` leave half still correct | 5/5 | **22/22** |
| multi-doc answerable | 12/13 | **12/13**, no regressions |
| single-doc answers / citations | 33/33, 27/27 | **33/33, 27/27** |

The reply now reads:

> The excerpts do not give a single figure for the total number of people
> employed by the company, so that information is not available in the provided
> documents.
>
> The standard annual-leave entitlement is **25 days per year plus public
> holidays** 【sample_dense_manual.pdf, Page 5】.

### The residual ~8%, characterised

It is a **different failure** from the one that was fixed. The surviving case
declines correctly and then shows its working anyway:

> Q2: Engineering 42, Sales 18, Support 12, Operations 9 → **81 employees**
> Q3: Engineering 51, Sales 21, Support 15, Operations 9 → **96 employees**
> … so a single overall current head-count **is not given**; the available data
> show 81 employees in Q2 and 96 employees in Q3.

The substitution prohibition is **holding** — 96 is no longer offered as the
company headcount, and the absence is stated outright. The arithmetic
prohibition is the one being violated: 81 and 96 are computed and appear in no
document.

The eval scores this as a failure because an invented number is on screen, which
is the strict reading and the deliberate one. Severity is genuinely lower — a
careful reader is told the answer is absent — but it is not zero, because a
skimming reader can still take "96 employees in Q3" as the answer. It is also
the mention-versus-assertion problem again, in a form no regex can settle: only
a human reading the reply can tell working-out from a claim.

### The guarantee, stated exactly

- **Guaranteed:** a citation names the document and page the text actually came
  from. All 13 cross-document cases resolve to exactly one file. No fabricated
  citations, no ambiguous ones.
- **Guaranteed:** when nothing in the corpus is on topic, the model says so.
  39/39 across three runs.
- **NOT guaranteed:** that the cited passage answers the question. A citation
  attests to *provenance*, not to *correctness*.
- **NOT guaranteed:** that no invented figure appears. Roughly one reply in
  twelve on the hardest case still computes one, now alongside a correct
  statement that the answer is missing.

A confidently cited wrong *document* is prevented. A correctly cited wrong
*answer* is rarer than it was and is still reachable.

---

## Slots, and what they are actually worth

Six slots instead of three, and no anchors. The honest accounting:

| slots | ranked |
|---|---|
| 3 | 12/13 |
| 4 | 12/13 |
| 5 | 12/13 |
| 6 *(shipped)* | 12/13 |
| 8 | 12/13 |
| 10 | 13/13 |
| 12 | 13/13 |

**The extra slots buy nothing measurable on this eval set.** The whole
11/13 → 12/13 improvement comes from the `(document, page)` key, which rescues
`md-13` at any slot count. Three slots would score the same.

They are kept because dropping anchors freed the budget — anchors were measured
consuming 14% of the context on every query, on documents nobody asked about —
and because a corpus answer should be able to draw on more than three
documents. That is a design judgement, not a measured gain, and it is recorded
as one.

Ten slots would put `md-10`'s answer in the context. It is not the default
because that is tuning to a single case, and because more slots treat the
symptom: the passage sits at rank 10 in the pooled ranking, and at rank 4 even
for its own sub-question asked alone, because the page it lives on is diluted.
See [the cause](#the-cause-is-competition-not-dilution-and-not-the-slot-budget) — per-part slot
allocation was measured on exactly this reasoning and does not recover it.

---

## Latency and memory against document count

Measured on the real fixtures replicated under distinct names, warm (vectors
already stored), three questions × three runs each.

| documents | passages | vectors | RSS | select, mean | p95 |
|---|---|---|---|---|---|
| 1 | 53 | 0.1 MB | 932 MB | 22 ms | 23 ms |
| 3 | 159 | 0.2 MB | 938 MB | 23 ms | 23 ms |
| 10 | 530 | 0.8 MB | 938 MB | 32 ms | 33 ms |
| **25** *(cap)* | **1325** | **2.0 MB** | **939 MB** | **51 ms** | **52 ms** |
| 50 | 2650 | 4.1 MB | 939 MB | 97 ms | 113 ms |
| 100 | 5300 | 8.1 MB | 940 MB | 177 ms | 198 ms |
| 200 | 10600 | 16.3 MB | 926 MB | 334 ms | 362 ms |

Latency is **linear at roughly 1.6 ms per document** and memory is **flat**.
Vectors are 2 MB at the cap and 16 MB at 200 documents, against a ~643 MB
steady state — they are not the constraint and were never going to be. RSS here
includes the eval harness; the figure to read is that it does not move with
document count.

Cold path, where a document's stored vectors are unusable and its passages must
be embedded on demand:

| documents | passages | first query |
|---|---|---|
| 1 | 53 | 290 ms |
| 5 | 265 | 396 ms |
| 10 | 530 | 664 ms |
| 25 | 1325 | 1594 ms |

That is a once-per-document cost, and only for documents stored under a
different embedding model or chunk scheme. The UI names which documents those
are rather than leaving the slowdown unexplained.

### Where it stops being usable

**Not on latency and not on memory.** At 200 documents a query still selects in
a third of a second, next to an LLM call that takes several seconds. Vectors at
that size are 16 MB.

**It stops on answer quality, and it does so long before either.** Six slots is
already only 10% of a 6-document, 61-passage corpus, and `md-10` fails *there* —
at six documents, not twenty-five. Every document added pushes a correct
passage further down a list that is still read six deep. The failure is silent:
the answer arrives fluent and correctly cited.

The cap is **25 documents**, and it is a limit on answer quality, not on
resources. It is set where a corpus is still small enough that six slots can
plausibly represent it, and it is documented so it can be raised deliberately
rather than drifted past. Raise it and the `md-10` failure mode gets more
common, not less.

---

## The eval matcher: an audit

`answer_hit` compares expected fragments against the model's reply. It was
corrected three times while measuring this work, and **each correction raised a
number**. That pattern is worth auditing rather than trusting, so here it is in
full.

| # | correction | cases affected | before → after |
|---|---|---|---|
| 1 | strip commas inside numbers | `lg-xls-03` | single-doc answers 32/33 → **33/33** |
| 2 | fold Unicode lookalikes (U+202F, U+2011, …) | `md-01`, `md-04`, `md-11` | multi-doc answers 9/13 → **12/13** |
| 3 | treat `four-year` and `four year` as equal | `md-13` | multi-doc answers 11/13 → **12/13** |

In every case the reply was inspected before the matcher was touched, and in
every case the reply was **verifiably correct**:

- `lg-xls-03` answered `$21,500,000`; the fixture expected `21,500,000` and the
  model had written `21500000`.
- `md-01` answered "45 pence per mile" with a **narrow no-break space** (U+202F)
  between "45" and "pence".
- `md-13` answered "a four‑year cycle" with a **non-breaking hyphen** (U+2011).

The fixtures were left alone and the matcher was changed, on the grounds that
how a model punctuates a correct figure is not the thing under test.

### The asymmetry, stated plainly

**Every one of those three corrections is monotonic in the same direction.**
Each collapses a distinction, so each can only turn a non-match into a match.
None can turn a match into a non-match. A matcher built only from such rules
**can only ever move scores up**, and no amount of care in applying them changes
that. This was verified rather than assumed.

That is a structural property, not a caveat, and it needed a counterweight.

### The fourth correction, which moves numbers down

Auditing for the opposite failure found a real one. The comparison was a plain
substring test, which has no boundaries:

- expecting `19` matched **`1987`** — the incorporation year, on page 2 of the
  same fixture
- expecting `94` matched **`1.94`** — the maintenance spend, on page 5
- expecting `51` matched **`151`**

Across both eval sets: **212 constructible false positives, of which 127 were
reachable from figures that genuinely appear in the corpus.** A wrong answer
containing the right digits would have scored as correct.

`_contains` now requires a numeric fragment not to be flanked by digits, and a
word fragment not to be flanked by word characters — with the period rule
narrowed to decimal points only, so `19. Next` still matches while `19.4` does
not. Reachable false positives: **127 → 0**.

**This is the test of whether the first three corrections were self-serving.**
Tightening the matcher in the opposite direction moved **no headline number**:

| | before tightening | after tightening |
|---|---|---|
| single-doc answers | 33/33 | **33/33** |
| single-doc citations | 27/27 correct, 0 wrong | **27/27 correct, 0 wrong** |
| multi-doc answers | 12/13 | **12/13** |

### Can remaining upward-only asymmetry be ruled out?

**No, and it should not be claimed.**

The normalisation layer is still monotonic by construction: comma stripping,
Unicode folding and hyphen/space equivalence each only ever collapse
distinctions. Boundary checking constrains *where* a match may occur, not how
permissively the two strings are folded before comparing. So a future
normalisation would again be able to raise scores and not lower them.

What can be said is narrower and worth stating exactly:

- The known unbounded-substring hazard is closed, measured 127 → 0 on the
  current fixtures.
- Closing it changed no headline number, which is evidence the earlier
  corrections were not inflating results.
- The audit is reproducible, and every clause of the matcher is covered by
  tests in `tests/test_rag_eval.py` — including six that assert the matcher
  *rejects* things.

None of that amounts to a guarantee. A substring-based matcher with a
permissive normalisation layer is a blunt instrument, and its bias has a
direction. Treat `33/33` as "no case is obviously wrong", not as proof that
every answer is right.

---

## Design notes

**Mode comes from the chunks, not a caller flag.** Single-document chunks are
`{"text", "page_or_sheet"}` and carry no `document` key, so a single-document
conversation cannot take the corpus path by accident. Single-document behaviour
is unchanged and re-verified: 33/33 retrieved, 32/33 leading the ranking, 33/33
answers, 27/27 correct citations.

**No vector database.** Numpy over the stored vectors, exactly as before. Gaps
are embedded on demand, per chunk rather than per document.

**Summarisation is untouched.**

**Duplicate file names are disambiguated by entry id.** Two history rows can
carry the same name, which would recreate the ambiguity the document tag exists
to remove, one level up.

**Which documents are searchable is surfaced.** A document stored under an
older embedding model or a different chunk scheme has its vectors ignored rather
than compared, and its passages embedded on demand. The UI says which documents
those are instead of quietly answering worse.

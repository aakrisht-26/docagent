# Structured extraction — baseline

Run with:

```bash
python tests/e2e/extraction_eval/run_eval.py            # LLM path
python tests/e2e/extraction_eval/run_eval.py --regex    # fallback path
python tests/e2e/extraction_eval/run_eval.py --verbose  # per-field detail
```

28 scored fields across five documents, one per schema (Financial, Legal,
Research, Healthcare, General). Every expected value is present in its
document, so the ceiling of 28/28 is reachable.

## Headline — restated, because the first version measured prose

| input kind | correct | wrong | missing | |
|---|---|---|---|---|
| **prose** (5 documents, 28 fields) | **28 / 28** | 0 | 0 | **100%** |
| **spreadsheet** (3 documents, 12 fields) | **11 / 12** | 0 | 1 | **92%** |
| **regex fallback** (all) | 0 / 40 | 0 | 40 | 0% |

Three runs, identical, after the Healthcare fixes on 2026-09-14. Before them the
same eval read 34, 34 and 36 of 40: prose 27/28 each time, spreadsheet 7, 7 and
9 of 12, with 3 WRONG every run. **Not all of that gain is the fix.** Three
fields a run are: `health-01` `patient_id`, and `health-sheet-01` `medications`
and `patient_id`. The other two are `legal-sheet-01`'s dates, MISSING in two of
the three earlier runs and untouched by the change, which is run-to-run
variance. The remaining miss, `fin-sheet-01` `fiscal_period`, was MISSING in all
six runs.

**The original headline of 27–28/28 was measured entirely on prose, and prose
was easy to author.** The app does not only receive prose: `ExcelReaderSkill`
emits a tabular dump, and the classifier routes those dumps to typed schemas —
a sales sheet reaches Financial, a ward census reaches Healthcare, a contract
register reaches Legal. Three of the four typed schemas are reachable from
tabular input, and none of them had a tabular fixture.

That gap was not hypothetical. `sample_sales.xlsx`, a real fixture in the e2e
harness, returned **zero** of the seven Financial fields while the prose
Financial fixture returned all seven.

**The prose number still saturates; the spreadsheet number does not**, and the
spreadsheet cases are where the eval now earns its keep.

## What the spreadsheet fixtures found

Three failures, all real, none of them a matcher artefact:

| case | verdict | what happened |
|---|---|---|
| `fin-sheet-01` `revenue` | **WRONG** | emitted **2,379,900** — *exactly the sum of the Revenue column*, a figure in no document. The sheet has a Revenue column and no totals row, so no total is stated. The model did arithmetic and presented it as an extracted fact. |
| `health-sheet-01` `medications` | **WRONG, fixed** | included **Ibuprofen**, whose Status column reads `Stopped`, in 10 of 10 draws, always annotated `(Stopped)` rather than presented as current. The field asked for *prescribed* medications, and a stopped drug was prescribed. See [the Healthcare fixes](#the-two-healthcare-failures-diagnosed-and-fixed). |
| `health-sheet-01` `patient_id` | **WRONG, fixed** | was filled in 10 of 10 draws: the raw MRNs in 9, placeholders in the tenth, and in 5 draws an MRN reached nearly every other field too. The schema said *"anonymise if present"*. It did not hold on prose either: 6 of 10 prose draws emitted the raw MRN. See [the Healthcare fixes](#the-two-healthcare-failures-diagnosed-and-fixed). |

The first and third are the two categories the eval now distinguishes:

- **`must_be_absent` — fabrication.** The value is NOT in the document and must
  not be invented.
- **`must_be_withheld` — policy.** The value IS in the document and must not be
  emitted anyway.

Both make silence correct and a value wrong, but the fixture invariants are
opposite, and the harness asserts both: a fabrication marker must be absent from
the document, a withheld value must be present in it. Conflating them was caught
by the eval's own harness test, not by review.

## The revenue fabrication is deterministic, and the refusal is an accident

The first investigation could not reproduce it and encoded it as a scored
expectation instead. Measured properly, it is **not nondeterministic at all.**

**20 trials per document, interleaved so timing cannot confound:**

| document | fabricated | rate | 95% CI (Wilson) |
|---|---|---|---|
| `sample_sales.xlsx` (app fixture) | 0 / 20 | **0%** | [0%, 16%] |
| `fin_sales_sheet` (eval fixture) | 20 / 20 | **100%** | [84%, 100%] |

Perfectly separated, non-overlapping intervals. The earlier contradictory
readings were 3–5 trials each — too few to see a split this sharp.

**The two documents differ by exactly one line.** A unified diff of the full
texts returns a single hunk:

```
- ────────────────────────────────────────────────────────────[Sheet: Q3 Sales]
+ [Sheet: Q3 FY26 Sales]
```

So there were two candidate variables. An A/B at 6 trials each isolates them:

| variant | fabricated |
|---|---|
| **separator + `Q3 Sales`** (the app fixture) | **0 / 6** |
| no separator + `Q3 Sales` | 6 / 6 |
| separator + `Q3 FY26 Sales` | 6 / 6 |
| no separator + `Q3 FY26 Sales` | 6 / 6 |

**Both perturbations independently cause it.** Only the exact original refuses
— 0 across 26 observations. Removing a 60-character separator line, or adding
two characters to a sheet name, is enough to flip the model from declining to
computing a total and presenting it as extracted.

**That is the finding, and it is worse than nondeterminism would have been.**
The refusal is not the model applying "extract only what is explicitly stated";
it is one input landing on the right side of a boundary nobody controls. The
instruction is in the prompt and it is not being followed. So the correct
reading of `sample_sales.xlsx` returning null is luck, not a safeguard, and no
document we have not already tested can be assumed safe.

## The fabrication check

A prompt instruction cannot be relied on here, but **a fabricated figure is
detectable after the fact**: a number presented as extracted should be findable
in the text it was extracted from. Prose can be legitimately paraphrased; a
figure cannot. `1.94` is either in the source or it is not.

`unverified_numbers()` extracts numerals from each value and checks them
against the source, ignoring tokens under four digits (row counts, small
integers and column indices are everywhere and would drown the signal).

**Measured before switching it on: across 45 fields extracted from the eval's
8 documents it flagged 2, and both were genuine fabrications.**

| flagged | verdict |
|---|---|
| `revenue: 2,379,900` | the column sum, in no document |
| `key_facts: …2011…` | an invented year; that document's only 4-digit number is 2026 |

**Zero false positives.** That is why the check **drops** rather than flags: a
wrong figure is worse than a missing one, and dropping is only defensible
because the false-positive rate was measured first rather than assumed.

List items are dropped individually — `key_facts` held four sound facts and one
invented one, and discarding the field would have lost the four to punish the
one. The caller is told what was removed and which figure triggered it, because
removing a value silently would be its own failure.

`DOCAGENT_EXTRACTION_VERIFY=false` disables it.

**What it does not address.** It is numbers only. The other two spreadsheet
failures, `medications` including a drug whose Status column reads `Stopped` and
`patient_id` leaking MRNs, were selection and policy failures rather than
arithmetic, and are handled in the next section.

## The two Healthcare failures, diagnosed and fixed

Both were found by the spreadsheet fixture, and the record of both was partly
wrong. Every rate below comes from independent draws: temperature 0.0, the
in-process LLM cache disabled, sheet and prose interleaved, the raw reply kept.
Intervals are Wilson 95%.

### What was actually happening

| | ward census sheet | prose discharge note |
|---|---|---|
| `medications` includes ibuprofen | **10/10**, always annotated `(Stopped)` | 0/10 |
| `patient_id` not empty | **10/10**: 9 raw MRN lists, 1 `ANON1; ANON2; ANON3` | **10/10**: 6 `MRN 55-40182`, 2 `MRN`, 1 `ANON`, 1 `REDACTED` |
| an MRN in any field | **9/10**, 5 of them in nearly every field (`55-40182: Amoxicillin 500 mg TDS`) | 6/10 |

Two corrections to how the failures had been recorded:

- **The stopped drug was not listed as current.** It was listed with its
  status. The field asked for "Prescribed medications with dosages", and a
  stopped drug was prescribed, while the prose note lists medications "on
  discharge". The model was answering the field as written: a specification
  defect, of the same kind as the five expectations further down.
- **The identifier leak was not spreadsheet-specific.** The raw MRN came back
  from the prose note in 6 of 10 draws. The prose case looked healthier only
  because it scored a placeholder as correct.

### Prompt work

| field | was | now |
|---|---|---|
| `medications` | Prescribed medications with dosages | Current medications with dosages; omit any whose status is stopped, discontinued or held |
| `patient_id` | Patient identifier or MRN (anonymise if present) | Patient identifier or MRN. WITHHELD: always null, and never copy an identifier into this or any other field |

| after the wording change, 50 draws each | sheet | prose |
|---|---|---|
| ibuprofen included | 0/50 [0%, 7%] | 0/50 [0%, 7%] |
| `patient_id` not empty | 0/50 [0%, 7%] | 0/50 [0%, 7%] |
| an MRN in any field of the reply | 0/50 [0%, 7%] | 0/50 [0%, 7%] |

On these two documents the wording holds. **On documents it was not written
against, it does not quite.** A cosmetic change flipped the model in the
fabrication finding, so four rewordings were run that keep the facts and change
how the identifier is presented, 16 draws each:

| rewording | identifier in any field of the reply | ibuprofen included |
|---|---|---|
| sheet, column headed `Patient ID` | **1/16** [1%, 28%] | 0/16 |
| sheet, `Hospital No` column moved to the end | 0/16 | 0/16 |
| prose, spaced NHS number | 0/16 | 0/16 |
| prose, MRN inline with no label line | 0/16 | 0/16 |

The one leak returned `patient_id` null and wrote `(patient 55-40182)` into
`dates` for all three patients. The instruction held the field and not the
value. For a health identifier that is not good enough, so the value is also
enforced after extraction.

### The withholding check

`_withhold()` runs on every Healthcare extraction, before the fabrication check:

1. Fields the schema withholds (`patient_id`) are removed.
2. Identifiers are read **from the document**: a label with a value beside it
   (`Patient identifier: MRN 55-40182`, `(MRN 55-40182)`, `NHS number: 485 777
   3456`), or a label heading a column in the reader's table dump. A value the
   model put in a withheld field is added when it also appears in the document.
3. Every occurrence in every other field, however re-punctuated (`55-40182`,
   `5540182`, `55 40182`), becomes `[withheld]`. A list item left holding
   nothing else is dropped.
4. A warning names the fields touched and never the identifier.

It runs first because the fabrication check prints the figures it drops, and
`5540182` is a figure the document never writes.

**Measured before it was wired in, and after:**

- Replayed over the 112 replies recorded before it existed (20 from before the
  wording change, 60 after, 32 on rewordings): no identifier left in any field,
  none in any warning, and no field changed that carried none. Live over 72 more
  replies after it was wired in: no identifier in any returned field.
- False positives: no identifier found in the six other extraction fixtures, the
  43 retrieval-eval texts, or the nine e2e sample files as the real readers
  parse them. The check runs only for the Healthcare schema, so the other four
  cannot be affected.
- 15 mutations of the check, the wording and the eval rules each turn a named
  test red, in `tests/test_extraction_withholding.py` and
  `tests/test_extraction_eval.py`.

### What it guarantees, and what it does not

- **Guaranteed:** a Healthcare extraction never returns `patient_id`, and never
  returns an identifier the document labels in one of the recognised ways, in
  any field and in any punctuation.
- **Not guaranteed:** an identifier with no label near it; a label outside the
  list (`URN`, `Case ID`) unless the model itself put the value in
  `patient_id`; identifiers inside tables that structure recognition embeds as
  HTML. Those rest on the wording, which leaked once in 164 replies.
- **`medications` has no check.** The failure was the field's wording, the new
  wording measured 0/50 on the sheet and 0/32 on its rewordings, and a string
  check could only catch a drug the model itself annotated as stopped, which is
  the case a reader can already see. A stopped drug listed without its status
  would pass.

### What changed in the eval, and why it is not a loosening

Both changes score the Healthcare cases more strictly.

1. **A withheld value is WRONG in every field of its case**, not only in
   `patient_id`. Before, `55-40182: Amoxicillin 500 mg` scored `medications`
   CORRECT. Re-judging the 10 recorded pre-change sheet draws, every draw scores
   4/6 under the old rules; under the new, five of them score 0 or 1 of 6.
   `health-sheet-01` now lists all three MRNs.
2. **`health-01` `patient_id` is `must_be_withheld`**, like the sheet case. It
   was `must_not_contain` alone, which scored a placeholder (`ANON`, `REDACTED`,
   the bare word `MRN`) CORRECT and an empty field MISSING, while the sheet case
   scored `ANON1; ANON2; ANON3` WRONG. Re-judging the 10 recorded pre-change
   prose draws: old rule 4 CORRECT and 6 WRONG, new rule 0 CORRECT and 10 WRONG.
   The one outcome the new rule accepts and the old did not is an empty field,
   which is what the schema now asks for. Under the old rule the fixed extractor
   would read prose 27/28, with `patient_id` MISSING.


## Schema coverage: what a document can support at all

| case | input | assertable fields |
|---|---|---|
| `fin-01` | prose | 6 / 7 |
| `fin-sheet-01` | **spreadsheet** | **2 / 7** |
| `legal-01` | prose | 5 / 7 |
| `legal-sheet-01` | **spreadsheet** | 4 / 7 |
| `health-01` | prose | 6 / 7 |
| `health-sheet-01` | **spreadsheet** | 6 / 7 |

This separates "the extractor missed it" from "the document does not contain
it". A sales spreadsheet supports **2 of the 7 Financial fields** — it states no
net income, no EPS, no guidance, no risks and no total revenue. That is a
schema-fit problem, not an extraction problem, and no amount of prompt work
fixes it. A ward census, by contrast, supports 6 of 7 Healthcare fields: tabular
input is not uniformly worse, it is worse where the schema asks for figures a
narrative states and a table does not.

## The prose metric saturates, and that is the finding

**It should be read as a regression guard, not as a score with headroom.** The
extractor sits at the ceiling on these fixtures, so the eval cannot rank an
improvement — any future change can only move it down. That was the thing to
avoid when building it, and it was not avoidable here: the fixtures carry
deliberate near misses and the extractor handles almost all of them.

The distractors were not token. Each was a wrong answer of the right shape,
sitting near the right words, and the LLM path excluded them:

| field | distractor it correctly rejected |
|---|---|
| `revenue` | prior-year 388.1, Q2 401.2, guidance 430–445, segments 96.4 / 214.5 |
| `net_income` | operating income 61.2 and gross profit 180.3, both larger and both nearer the word "income" |
| `parties` | a **guarantor** and two **law firms** named in the same paragraph |
| `effective_date` | the **signature** date and an **amendment** date, identically formatted |
| `governing_law` | New York, named two sentences later as the **arbitration seat** |
| `authors` | four authors of **cited** work |
| `datasets` | two corpora named only as **related work** |
| `diagnoses` | a **ruled-out** differential and two **family-history** conditions |
| `medications` | a **discontinued** drug |
| `physician` | the **referring** physician, explicitly not responsible for inpatient care |

That is a real result: on documents built to trap it, extraction is accurate.

**What still discriminates is the path, not the accuracy.** 28 versus 0 is the
number to watch, and it is what the fallback costs.

## The regex fallback contributes nothing, structurally

It can emit only four keys — `dates`, `monetary_values`, `percentages`,
`emails` — and they are cross-referenced against the schema before reaching the
caller:

| schema | regex keys that are valid fields |
|---|---|
| Financial | **0 / 4** |
| Legal | **0 / 4** |
| Research | **0 / 4** |
| Healthcare | 1 / 4 (`dates`) |
| General | 2 / 4 (`dates`, `monetary_values`) |

`percentages` and `emails` are fields of no schema and are always discarded.
Measured: **0 schema-valid fields on 5/5 eval documents and on 6/6 of the
existing e2e fixtures.** For Financial, Legal and Research a zero result is
guaranteed by construction, not by the document.

It is not a degraded mode. It is an elaborate way of returning `{}` while
reporting `success=True`.

### What was done about it

**It is no longer a fallback.** It is a supplement, and the stage now says when
extraction did not run.

- A failure of the LLM path no longer substitutes regex output silently. The
  method names the cause — `unavailable_truncated`, `unavailable_rate_limited`,
  `unavailable_llm_failed`, `unavailable_unparseable`, `unavailable_no_llm` —
  and `success` is **False**, because a stage that produced nothing must not
  look like it worked.
- The regex path is kept and demoted. On a General-schema document with ISO
  dates or currency-marked amounts it does recover real fields, and one field
  beats none; that outcome is `regex_partial` with `success=True` and a warning
  saying what was lost. It is never allowed to stand in for the LLM path on a
  typed schema, where it cannot produce a field at all.
- The warning travels with the result rather than stopping at the log, so the
  reader learns whether to raise a budget or wait a minute.

### The budget that caused most of it

`max_tokens` was **1500**, sized against the expected reply. Measured across
eight documents at a 6000-token budget so nothing truncated:

| document | prompt | reasoning | content | total out |
|---|---|---|---|---|
| health_note | 520 | 326 | 166 | 492 |
| research_paper | 578 | 231 | 244 | 475 |
| legal_msa | 635 | 314 | 207 | 521 |
| fin_quarterly | 660 | 400 | 214 | 614 |
| general_ops | 471 | 524 | 200 | 724 |
| sample_large_report | 1710 | 710 | 241 | 951 |
| sample_mixed_topics | 1825 | 930 | 221 | 1151 |
| **sample_dense_manual** | 1905 | **1278** | 261 | **1539** |

**Content is flat at 166–261 regardless of document size; reasoning is what
scales.** `sample_dense_manual` needed 1539 and had 1500 — it truncated by 39
tokens, which is the whole of the silent fallback this eval was built to find.

The budget is now `_CONTENT_TOKENS` 400 + `_REASONING_ALLOWANCE` 2048 =
**2448**, each sized from its own measurement with margin (+53% on content,
+60% on reasoning). Summarisation's allowance of 1024 was sized against its own
worst reasoning of 902 and would not have been enough.

**Measured effect:** `sample_dense_manual.pdf` went from **0 fields to 5 of 6**.

The cost is stated rather than hidden: Groq counts prompt + `max_tokens` against
the per-minute window, so a larger budget makes a 413 refusal likelier. That is
the right trade only because a refusal is survivable — keys rotate, and the
caller now says which failure happened — whereas truncation produced a
guaranteed zero that reported success.

## One genuine defect the eval found

`patient_id` was specified as *"Patient identifier or MRN (anonymise if
present)"*. This section said the instruction was honoured inconsistently and
at a low rate: two leaks in roughly thirteen attempts, and 0 in 8 identical
isolated calls at temperature 0.0.

> **Corrected.** With the in-process LLM cache off, 10 independent draws on the
> prose fixture emitted the raw MRN 6 times and never returned the field empty.
> Identical calls in one process are served from `LLMCache`, so "0 in 8
> identical calls" may have counted one reply eight times; that was not
> re-checked. The failure and its fix are in
> [the Healthcare fixes](#the-two-healthcare-failures-diagnosed-and-fixed).

## Five expectations I wrote were wrong, and all five flattered the eval

Every one was caught by reading the failures rather than by trusting the first
number, and **every correction moved the score up** — which is the pattern this
project has hit four times before, and the reason to distrust a matcher that
has never been argued with.

| case | what I asserted | why it was wrong |
|---|---|---|
| `eps` | `2.21` is a distractor | The schema says only "Earnings per share" and names no basis. `Basic: $2.21, Diluted: $2.14` is a correct, labelled answer. I was scoring a preference the system was never told. |
| `fiscal_period` | must contain `"q3"` | The model answered `Third Quarter, Fiscal Year 2025`. Correct, and I was scoring format. Fixed by adding `must_contain_any`. |
| `organisations` | `Brandt and Fielding` is a distractor | The schema asks for "Named organisations or companies". It **is** one. |
| `locations` | `Porto`, `Rotterdam` are distractors | The schema asks for "Named locations, cities, countries". They **are** named locations. |
| `patient_id` | must contain the MRN | The schema says *anonymise*. I was asserting a violation of the schema and marking the model wrong for obeying it. |

The four General-schema entries share a cause worth stating on its own: **the
General schema's fields are enumerative** ("all important dates", "named
organisations"), so they cannot be scored for exclusion. Anything named in the
document satisfies them. Only the four typed schemas can be scored for
correctness at all.

## What this does not measure

The domain is taken from the eval set, not from the classifier, so a
misclassification cannot move the score. Domain routing is a real failure path
— `_DOMAIN_ALIASES` has no entry for `Generic`, which the classifier does emit,
and it falls through to `General` — and it is deliberately kept out of the
headline so that two different failures do not share one number.

---

## Is the stage worth what it costs?

**The extraction is good. The stage, as currently wired, is not paying for
itself.** Those are separate judgements and the numbers separate them cleanly.

### What it costs

| | |
|---|---|
| tokens reserved per document | 1905 prompt + 2448 budget = **4353** |
| free-tier window | 8000 tokens/minute |
| **share of one minute, per document** | **54%** |
| measured latency | 2.3s (small) to 25.6s (dense) |

It competes for that window with summarisation, which is the stage users
actually read. On a free-tier key, running both on a dense document is most of
a minute's allowance.

### What it returns

Accurate output: **27–28/28** against documents built to trap it.

### Where that output goes

| destination | present? |
|---|---|
| the UI | **no — never rendered anywhere** |
| markdown export | **no** |
| JSON export | yes |
| `history.db` | yes |
| e2e harness stdout | yes (printed, asserted on by nothing) |

`grep -rn extracted_entities ui/` returns two lines: one restoring it from
history, one storing it. **No component displays it.** A user meets this
stage's output only by downloading the JSON.

### And roughly half of documents get the schema that says least

`_DOMAIN_ALIASES` routes `Technical`, `Educational`, `Environmental` and `HR`
to **General**, whose fields are enumerative — "all important dates", "named
organisations", "up to 5 other important facts". Those largely restate what the
summary already says in prose, and as the eval found, they cannot be scored for
correctness at all because anything named in the document satisfies them. The
four typed schemas are where the value is, and they are a minority of runs.

### The verdict

**Keep the capability; fix the wiring.** The waste is not extraction quality —
it is spending the scarcest resource in the system on a result nobody is shown.
Two changes would settle it, and the first is the minimum:

1. **Display it.** The result is accurate and already paid for. A field table
   under the results tabs would make the stage's cost defensible immediately.
2. **Gate it to typed schemas.** Skipping `structured_extraction` when the
   schema resolves to General would remove roughly half the calls and drop the
   half whose output duplicates the summary. That is a planner change and a
   product decision, so it is recommended here rather than made.

If neither is done, the honest position is that this stage should be removed:
an accurate answer that no one can see is not worth 54% of a minute.

### Both changes were subsequently made

**Displayed.** A compact "Key fields" table above the summary prose, and a table
in the markdown export. Above the prose because these are lookup values and the
summary is narrative.

**General gated out.** Checked before acting rather than argued from the schema
wording: read against a real General summary, the extraction's values were
already in the prose with comparisons the field list does not carry.

**Measured: 4 of 11 documents resolve to General — a 36% reduction in extraction
calls.** I had estimated "roughly half"; measurement corrected that downward.

| document | classifier domain | schema | runs? |
|---|---|---|---|
| sample_report.pdf | Technical | General | skipped |
| sample_large_report.pdf | Financial | Financial | runs |
| sample_dense_manual.pdf | Generic | General | skipped |
| sample_mixed_topics.pdf | Technical | General | skipped |
| sample_sales.xlsx | Financial | Financial | runs |
| sample_large_sales.xlsx | Financial | Financial | runs |
| fin_quarterly | Financial | Financial | runs |
| legal_msa | Legal | Legal | runs |
| research_paper | **Technical** | **General** | **skipped** |
| health_note | Healthcare | Healthcare | runs |
| general_ops | Financial | Financial | runs |

**What it costs.** The `research_paper` row is the cost in one line: its true
domain is Research, the classifier says Technical, and it is now skipped
entirely rather than getting a General field list. Domain routing was always
imperfect; the gate makes that imperfection consequential.
`DOCAGENT_EXTRACT_GENERAL=true` restores the old behaviour without a code
change.


# Summary figures: how often a summary states a number the document does not

Run with:

```bash
python tests/e2e/summary_eval/adjudicate.py            # the rates below, from the recorded summaries
python tests/e2e/summary_eval/analyse.py --calibrate   # classifier counts, and how far to trust them
python tests/e2e/summary_eval/run_trials.py --rounds 5 --out new_trials.jsonl   # record a new run (API)
```

Reproducing any number on this page needs no API call. The 85 summaries are
recorded in `trials.jsonl`, with the exact text each was given in
`trials.sources.json`. `tests/test_summary_eval.py` re-derives every number
below from those files.

## What was measured

There are 17 fixtures, summarised 5 rounds each: 85 summaries.

- **Model and settings:** `openai/gpt-oss-120b` via Groq. Standard length and Professional tone, as in the e2e harness. In-process LLM cache off.
- **When:** recorded 15 September 2026, 18:45–18:53 UTC.
- **Fixtures:** the nine e2e samples that produce a summary, where **no source states a year**. Plus the eight extraction-eval documents, where **every source is dated**.
- **Call path:** 70 summaries used one call and 15 used map-reduce.

`figures.py` sorts every figure in a summary against the text the summariser
was given:

- **3,134 stated:** as written, rescaled, in words, in pence, or OCR-garbled in the source.
- **23 rounded.**
- **41 small integers:** not scored.
- **821 not in the source.** Each of these got a verdict. The classifier settles
  the ones it can be trusted on; a person decided the rest, and `adjudicate.py`
  checks that decision against its own arithmetic.

## Headline

| summaries containing | all | e2e: no source states a year | eval: every source dated |
|---|---|---|---|
| invented year | 21/85 (25%, 17–35%) | 21/45 (47%, 33–61%) | 0/40 (0%, 0–9%) |
| computed total, correct | 19/85 (22%, 15–32%) | 11/45 (24%, 14–39%) | 8/40 (20%, 10–35%) |
| computed total, wrong | 8/85 (9%, 5–17%) | 8/45 (18%, 9–31%) | 0/40 (0%, 0–9%) |
| derived figure, correct | 61/85 (72%, 61–80%) | 33/45 (73%, 59–84%) | 28/40 (70%, 55–82%) |
| wrong figure | 27/85 (32%, 23–42%) | 15/45 (33%, 21–48%) | 12/40 (30%, 18–45%) |
| gross error | 16/85 (19%, 12–28%) | 12/45 (27%, 16–41%) | 4/40 (10%, 4–23%) |
| slips only | 11/85 (13%, 7–22%) | 3/45 (7%, 2–18%) | 8/40 (20%, 10–35%) |
| invented figure | 8/85 (9%, 5–17%) | 3/45 (7%, 2–18%) | 5/40 (12%, 5–26%) |
| outside figure | 24/85 (28%, 20–39%) | 13/45 (29%, 18–43%) | 11/40 (28%, 16–43%) |
| restatement | 7/85 (8%, 4–16%) | 5/45 (11%, 5–23%) | 2/40 (5%, 1–17%) |
| **year, wrong or invented** | **42/85 (49%, 39–60%)** | **26/45 (58%, 43–71%)** | **16/40 (40%, 26–55%)** |
| any figure not in source | 74/85 (87%, 78–93%) | 39/45 (87%, 74–94%) | 35/40 (88%, 74–95%) |

Every cell counts summaries, with Wilson 95% intervals.

**Half the summaries state at least one false figure about their document.**
That means a year the document never gives, arithmetic that is wrong, or a
number with no basis in the document. Both prompts already say "Never invent
information — only use what is provided."

## The computed total and the invented year are different failures

"$2,379,900 in total revenue for Q3 2024" was both at once: a right total and
an invented year. Measured separately, they behave differently.

### Invented years happen only when the document gives no year

- **Rate:** 21 of 45 summaries of undated sources contain one, against 0 of 40
  summaries of dated sources. The intervals do not overlap.
- **Where they appear:** the model supplies a period the document leaves out.
  - Titles: "Q3 2024 Operations…" on the audio briefing (5/5). "FY 2024" or "FY 2023" on the large sales workbook (5/5), where the year changes between runs.
  - "Meridian Freight Systems FY 2023…" (4/5).
  - Deadlines: "full rollout by Q1 2027".
  - Two sign-offs reading "Date: 15 September 2026", the UTC date the trials ran. No source contains a date at all.
- **Values:** 2024 ×15, 2023 ×11, 2025 ×4, 2026 ×4, 2027 ×3, and the placeholder "202X" ×2.
- **Accuracy:** all 39 are false. Not one is a restatement.

### Computed totals are mostly right, and the wrong ones are all on undated spreadsheets and reports

- **Rate:** 27 summaries contain a computed total. 19 of those are right and 8 are wrong.
- **Right totals** are sums of columns or groups that the source never totals:
  - $2,379,900 on both sales workbooks (5/5 each)
  - the large sales workbook's 111,120,000
  - £8,200,000 on the contract register (3/5)
- **Wrong totals:**
  - A full-year regional table on the large sales workbook (5/5), with North at $38.5M, $41.1M or 38,530,000 against a true $40.55M.
  - $1,247,300 for Orion revenue of $1,329,700, and a Q2 headcount of 84 for 81 (sample_sales, 2/5).
  - $5.73M of maintenance for $4.77M (1/5).

## Genuine fabrication vs legitimate restatement

Each of the 821 figures not in its source falls into exactly one verdict:

| verdict | figures | meaning | example |
|---|---|---|---|
| correct, derived | 340 | a margin, share, growth rate or difference, arithmetically right | gross margin 43.7% = 180.3 / 412.7 |
| correct, total | 228 | a sum the source does not state, arithmetically right | $2,379,900 |
| outside | 74 | an assumption, benchmark, example or target the model introduces, usually labelled | "$120,000 per vehicle (industry benchmark, not disclosed)"; "e.g., 30 days" |
| wrong, gross | 71 | about the document and wrong | "an additional $4.6 million" for about $1.11M; North "≈ 38 %" of revenue for 36.5% |
| wrong, slip | 45 | wrong by at most one unit of the last digit shown, or 1% | "$1,231.57" for 1,231.20; "23.3 %" for 23.36% |
| year | 39 | a year the source never states | "Q3 2024" |
| invented | 16 | presented as fact with no derivation from the document | "below the 10 milli-g threshold" (the paper states none); "a 28% increase in total revenue" from a workbook holding one quarter |
| restated | 8 | legitimate, and missed by the classifier | "over 70 %" for 71 percent; "31 March" for a leave year that "runs from the first of April"; "100 % compliance" for "met on every occasion" |

Some restatements never reach adjudication because the classifier resolves them
itself:

- unit scaling ("704.8 k")
- number words ("twelve")
- "FY26" as 2026
- "£0.19" for "19 pence"
- "time and a half" as 1.5, and "per thousand" as 1,000
- OCR-garbled source numerals ("to.51")
- rounding (23 figures)

**What this means for a check.** Suppose a check flagged every figure absent
from the source. 813 of its 821 flags would be accurate: the figure really is
not in the document (99%). But only 171 (21%) would mark a false statement. The
rest are right arithmetic or labelled assumptions.

## Two findings that bear on any fix

- **Most failures repeat on the same document.**
  - Six fixtures had a false figure in all five runs: fin_quarterly, fin_sales_sheet, sample_sales, sample_large_report, sample_large_sales and sample_audio. The sales workbooks gave the Orion average price as 1,231.6 or 1,231.57 in every one of their ten runs.
  - Across the 42 summaries with a false figure, a new draw at that document's own rate would contain one again 83% of the time.
- **Right arithmetic outnumbers wrong about four to one.** There are 568 correct computed figures against 116 wrong ones. 72% of summaries contain a correct derived figure: the margins, shares and growth rates the prompt asks for when it says "identifying overall trends and outliers".

## Method

### The classifier

`figures.py` tries, in order: stated, small, year, rounded. It then tries to
reproduce the figure by arithmetic on source figures, to the precision the
summary shows. The first basis that fits wins:

| basis | arithmetic tried | trusted? |
|---|---|---|
| total | column, group or row sums and means in a source table, and a column or group summed across sheets | yes |
| sentence | sum, difference, ratio, product, share or change of figures in the same summary sentence or table row | yes, if every operand is a source figure |
| row | the same on one source table row | no, needs a verdict |
| passage | the same on one source prose passage | no, needs a verdict |
| between | the same between two source table totals | no, needs a verdict |

### Calibration: how far each basis can be trusted

Each figure computed on a basis is moved 3–15% in the same format, put back in
its own sentence, and classified again. The table gives the share of these
decoys that still matched, on that basis or on an earlier one:

| basis | decimal | integer <1000 | integer ≥1000 | percentage |
|---|---|---|---|---|
| total | 0/55 | 12/455 (2.6%) | 1/565 (0.2%) | 0/30 |
| sentence | 4/110 (3.6%) | 4/85 (4.7%) | 1/60 (1.7%) | 50/555 (9.0%) |
| row | – | 4/10 (40%) | – | 59/155 (38%) |
| passage | 78/135 (58%) | 42/60 (70%) | 7/65 (11%) | 186/355 (52%) |
| between | 51/75 (68%) | 9/10 (90%) | 17/45 (38%) | 380/420 (90%) |

On the wide bases, a figure 3–15% wrong still matches most of the time. So a
match there says nothing, and every such figure was adjudicated by hand.

### Adjudication

`adjudication.json` holds one rule per distinct figure: fixture, token, an
optional context and a verdict. A `correct` or `wrong` verdict carries the right
derivation as an expression. `adjudicate.py` refuses to report if:

- any figure has no rule, or any rule matches nothing
- a `correct` figure does not equal its expression at the precision shown
- a `wrong` figure does

A same-sentence derivation whose operands are not source figures also needs a
rule. "≈ 45 %" computed from "$41.1 M of $91.5 M" is right arithmetic on two
wrong numbers.

### Judgement calls, stated so they can be disputed

- **Hedges.** "≈", "~" and "roughly" buy one unit of the last digit shown, or 1%
  off a figure that is not a percentage.
- **general_ops, 39% and 77%.** The note says "a further 6.1 million pounds is the
  approved budget for the full year and has not yet been spent". I read that as
  6.1 on top of the 2.4 already spent, making 39% wrong. Under the other
  reading, 39% is right. That changes one summary at most.
- **"44 extra leavers".** This applies 2.4 points of driver attrition to all
  1,840 staff. The arithmetic is right but the base is wrong, so it is marked
  wrong.
- **"≈4–5 % YoY".** Accepted as correct, because it spans growth rates of 3.9%,
  4.4% and 5.7%.
- **Outside figures are not counted as false.** Most are labelled ("assuming",
  "industry benchmark", "e.g."). But the costs derived from them ("$10.9 M
  capital outlay") are set in the same bold as document figures.

## What this does not measure

- **Attribution.** A figure that IS in the source but is attached to the wrong
  thing counts as stated, so the false rate is a lower bound. An example would
  be the stated 18% quarter-on-quarter growth reported as year-on-year.
- **Anything without digits:** a name, a period in words, or a claim.
- **A computed figure that equals an unrelated source figure** counts as stated.
- **Scope:** one model, one length and tone, and 5 draws per fixture, over 17
  synthetic fixtures. Treat a per-fixture rate as 5 draws, not a probability.

## Corrections on the record

1. **The first classifier found arithmetic everywhere.** "21.4% = 4500/210" and
   "81 = 210/260×100" were coincidences in a pool of about 38,400 pairwise
   results taken across whole sheets. Arithmetic is now tried on narrow bases
   first. Every basis was calibrated, and the wide ones are not trusted.
2. **Hand review found classifier artefacts that would have been reported as
   fabrications:**
   - section numbers followed by U+202F ("### 4.1 Title")
   - "£0.19" for "19 pence", and "1,000" for "per thousand"
   - "time and a quarter" broken across a line
   - the "19" of COVID‑19, the "25" of "2024‑25", and the "20" of "202X"
   - OCR-garbled source numerals
   - falls written as positive percentages
   - totals across quarterly sheets, and row totals

   With these fixed, the classifier's unexplained figures fell from 180 to 130
   before any adjudication.
3. **The hedge first allowed 1% on percentages.** That excused "≈ 99.5 %" for
   99.23%. The arithmetic check caught it. Tightening the rule also moved three
   of my own `wrong` verdicts to `correct`: "≈ 12 %", "≈ 20 %" and "≈ 27 %" on
   the large sales workbook.
4. **Two bugs in the adjudication script, both caught by its own checks.**
   - Figures the model wrote with U+202F before "%" matched no rule.
   - The operand check read "(51-42)" as −42, which sent 142 correct
     same-sentence derivations to review.
5. **"Computed total" was first counted by classifier basis alone.** That missed
   totals only a person could confirm (£8,200,000, 1,840) and every wrong total.
   Rules now tag totals. The right-total rate moved from 15/85 to 19/85, and
   the 8/85 summaries with a wrong total became visible.

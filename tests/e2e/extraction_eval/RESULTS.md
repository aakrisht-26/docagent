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

## Headline

| path | correct | wrong | missing | completeness | correctness |
|---|---|---|---|---|---|
| **LLM** (`openai/gpt-oss-120b`) | **27–28 / 28** | 0–1 | 0 | 100% | 96–100% |
| **regex fallback** | **0 / 28** | 0 | 28 | **0%** | n/a |

Five consecutive LLM runs scored **28, 27, 27, 28, 28**. The only field that
moves is `patient_id`; every other field was correct in every run.

The runner prints a warning when it scores full marks, because a metric at its
ceiling cannot discriminate and saying so is more useful than reporting a pass.

## The correctness metric saturates, and that is the finding

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

## One genuine defect the eval found

`patient_id` is specified as *"Patient identifier or MRN (anonymise if
present)"*. The instruction is honoured **inconsistently**: across runs the
field came back as `ANONYMIZED` and, twice, as the raw `MRN 55-40182`. Isolated
repetition scored 0 leaks in 8 identical calls at temperature 0.0, so the rate
is low — observed twice in roughly thirteen attempts — but it is not zero, and
the failure direction is a healthcare identifier being emitted when the schema
said to withhold it.

The eval case is inverted to test this: emitting the MRN is the failure.

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

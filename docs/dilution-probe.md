# Dilution is not the mechanism

**A fact buried among unrelated ones in a mixed-topic page does not lose rank
because of the mixing.** This branch set out to fix that effect and established,
with a purpose-built fixture, that the effect is not there. What actually costs
a page its rank is **competition**: another page being a better match for the
question, independent of how either page is composed.

Re-run it yourself, free and without API calls:

```bash
python tests/e2e/rag_eval/run_eval.py --probe
```

---

## The hypothesis

From `docs/multi-document-chat.md`, on `md-10`: the answer to "how many people
does the company employ" is 1,840, stated on page 1 of an operations review.
That page is an executive summary — revenue, four depots, 612 vehicles, 1,840
people, scope of review. The reading was that the page therefore *embeds as
something else*, and that the answering clause is drowned out by its neighbours.

That reading predicted a general effect: heterogeneous pages should lose rank,
and more heterogeneous ones should lose more.

## The probe

`sample_mixed_topics.pdf`, a facilities annual report. Twelve cases, built
**before any fix existed and without knowing what fix might apply**, so it could
not be steered toward a conclusion.

A 3×3 factorial:

| | fact **leads** | fact **mid-page** | fact in **final clause** |
|---|---|---|---|
| **1 topic** *(control)* | dp-01 | dp-02 | dp-03 |
| **2 topics** | dp-04 | dp-05 | dp-06 |
| **6 topics** | dp-07 | dp-08 | dp-09 |

Every page is 95–112 words and holds exactly one queryable fact. Pages are
written to read like real estates reporting rather than as traps — a real annual
report does put six subjects in an overview, and small teams really do write a
combined "Grounds and Security" page.

**The single-topic controls are the load-bearing part.** A mixed page that ranks
badly proves nothing on its own, because topic count and fact depth both moved.
If a single-topic page of the same length also loses rank when its fact sits in
the final clause, the cause is depth or length and heterogeneity is a red
herring.

Three **competitor** pages (10–12) were added after the first nine, each
topically dedicated to a probe question while holding none of its answer, with
depth held at `lead` so only the answer page's composition varies. That is
`md-10`'s actual shape and the first nine were missing it.

## Result

**10/12 rank first, and the two losses are in the wrong place.**

| declared topic count | rank-1 | mean score |
|---|---|---|
| **1 topic** | **2/4** | 0.5037 |
| 2 topics | 4/4 | 0.5476 |
| **6 topics** | **4/4** | **0.7034** |

| depth of the fact | rank-1 | mean score |
|---|---|---|
| lead | 4/6 | 0.5719 |
| middle | 3/3 | 0.5490 |
| final | 3/3 | 0.6468 |

Both losses — `dp-01` and `dp-10` — are the *same single-topic page*, a boiler
replacement page beaten by a plant-room page. The six-topic executive summary
beats its dedicated competitor at **0.7784**, the highest score in the probe.

**Topic count runs opposite to the prediction, and depth shows nothing.**

## Heterogeneity, measured rather than gestured at

Operational definition: split a page into sentences, embed each, take the mean
cosine distance from the page centroid. Homogeneous text clusters and scores
low.

```
correlation(heterogeneity, rank) = -0.084   (n = 12)

mean heterogeneity, pages that LOST rank 1 : 0.4475
mean heterogeneity, pages that KEPT rank 1 : 0.4568
```

No relationship, and the sign runs the wrong way — the pages that lost are
marginally *less* heterogeneous than the ones that kept first place.

The metric itself is sound. It tracks declared topic count as it should:
0.42–0.45 for one-topic pages against 0.47–0.53 for six-topic ones. It simply
does not predict rank.

## What does predict rank: competition

`dp-01` ranked **first** among nine pages. Adding a plant-room page dropped it
to **rank 2** — its own text unchanged, its own heterogeneity unchanged. `dp-10`
asks the same question in different words and lands at rank 3.

The plant-room page never states the boiler cost. It wins because it is *more
about plant rooms and boiler housings* than the boiler-replacement page is about
"how much was spent" — that page spends most of its words on flue routing,
scaffold licences, commissioning, recycling and warranty.

Which is the same mechanism as `md-10`: page 2 (Corporate Structure) is entirely
about the organisation and states no headcount, and it outranks the page that
holds the number.

## What this implies

**Re-chunking cannot address it.** Splitting a page into smaller passages
changes what each passage contains; it does not change the fact that a
*different page* is a better match for the question. Both pages get re-split by
the same rule, and the competitor's advantage survives. The passage-size sweep
bears this out — `md-10`'s rank moves between 2 and 5 across settings with no
monotone trend, which is noise around one case rather than a size effect:

| passage | single-doc leads | md-10 rank | index |
|---|---|---|---|
| page-level | — | 4 | 36 |
| 40/10 | — | 2 | 106 |
| 60/15 | — | 5 | 72 |
| **100/20 (shipped)** | **32/33** | 4 | 53 |
| 150/30 | — | 4 | 44 |

**The shipped setting is already the best of them** on the single-document set,
and it reaches that set's ceiling.

What *would* address competition is a different class of change — reranking
retrieved candidates against the question, or a signal that distinguishes "this
page is about X" from "this page states a fact about X". Both are considerably
larger than a chunking parameter, and neither is justified by one case.

## Two corrections to my own work, recorded

**The first nine cases scored 9/9 and I nearly stopped there.** They were
missing the variable `md-10` actually has: a dedicated competitor. The
competitor pages were added because of that gap, not because the first result
was inconvenient, and the 9/9 stands in the record above.

**"1 topic" was a label, not a property of the text.** The boiler page covers
flues, scaffolding, commissioning, recycling and warranty inside its one
project. Author-declared topic count is a coarser instrument than it looks,
which is why the measured definition matters more than the design cell — and
why the measured definition finding no effect is the stronger of the two
results.

## Why the fixture stays

A negative result nobody can re-run is a claim, not a finding. The fixture, its
twelve cases and the `--probe` runner are committed so the refutation can be
checked, and so the next person who suspects dilution can test it in a minute
rather than rediscovering it over a session.

It lives in its own eval block and cannot move the headline sets, which are
re-verified unchanged: 33/33 retrieved with 32/33 leading the ranking on the
single-document set, 12/13 on the cross-document set.

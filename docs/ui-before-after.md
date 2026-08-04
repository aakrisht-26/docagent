# UI: before and after

Screenshots from the interface polish pass. Every surface appears four times —
before and after, in dark and light — so the comparison is like for like.

**How these were captured.** Both sides were driven through headless Chrome at
an identical **1600×1000** viewport at 2× scale, analysing the **same document**
(`tests/e2e/samples/sample_report.pdf`). "Before" is commit
[`f2efea5`](https://github.com/aakrisht-26/docagent/commit/f2efea5), the state of
`main` before this pass; "after" is the tip of the branch. Both ran against the
same history database and the same `.env`, so nothing differs except the code.

Two shots — the mid-run status panel and the crash state — needed a run frozen
in place. A temporary per-stage pause and a temporary `raise` were injected into
both checkouts to hold those states still, then removed. Neither is in the
committed code.

---

## 1. First screen

The old first screen restated the control directly above it and listed file
extensions. It never said what the tool does, and on a 1920-wide window 46% of
the viewport was empty.

| | Dark | Light |
|---|---|---|
| Before | ![](screenshots/before-dark-01-empty-state.png) | ![](screenshots/before-light-01-empty-state.png) |
| After | ![](screenshots/after-dark-01-empty-state.png) | ![](screenshots/after-light-01-empty-state.png) |

In the light "before" shot, note the sidebar history: those black slabs are
buttons whose filenames are invisible, and the unselected radios render as
filled black dots — so *every* option looks selected.

## 2. Upload, ready to analyse

| | Dark | Light |
|---|---|---|
| Before | ![](screenshots/before-dark-02-upload-ready.png) | ![](screenshots/before-light-02-upload-ready.png) |
| After | ![](screenshots/after-dark-02-upload-ready.png) | ![](screenshots/after-light-02-upload-ready.png) |

## 3. A run in progress

Before: a bare progress bar with the finished stages joined into a single
truncated caption. After: a status panel with a live stage checklist and
per-stage timings, which collapses to one summary line when the run finishes
and stays open if it fails.

| | Dark | Light |
|---|---|---|
| Before | ![](screenshots/before-dark-03-status-midrun.png) | ![](screenshots/before-light-03-status-midrun.png) |
| After | ![](screenshots/after-dark-03-status-midrun.png) | ![](screenshots/after-light-03-status-midrun.png) |

## 4. Results

The clearest single difference: confidence read **3%** before and **96%** after,
on the same document. The number was being displayed inverted — it showed
*questionnaire likelihood*, not confidence in the answer given. Exports also
moved below the content, so the page no longer offers a download of something
you have not been shown yet.

| | Dark | Light |
|---|---|---|
| Before | ![](screenshots/before-dark-04-results-summary.png) | ![](screenshots/before-light-04-results-summary.png) |
| After | ![](screenshots/after-dark-04-results-summary.png) | ![](screenshots/after-light-04-results-summary.png) |

## 5. Document tab

**No "before" shot exists for this one.** The renderers for the extracted text,
the tables and the run detail were all present in the code but had no caller —
nothing in the UI reached them. They are reachable again behind this tab.

| | Dark | Light |
|---|---|---|
| After | ![](screenshots/after-dark-05-document-tab.png) | ![](screenshots/after-light-05-document-tab.png) |

## 6. Chat

| | Dark | Light |
|---|---|---|
| Before | ![](screenshots/before-dark-06-chat.png) | ![](screenshots/before-light-06-chat.png) |
| After | ![](screenshots/after-dark-06-chat.png) | ![](screenshots/after-light-06-chat.png) |

In light mode the chat input was a black bar on a white page — see
[DEPENDENCIES.md §6](../DEPENDENCIES.md) for why that kept happening.

## 7. Something went wrong

A crash and a rejected input used to look identical. They now differ on purpose:
a crash leads with the exception type, says in words that it is a fault in
DocAgent rather than a problem with your file, and keeps the traceback behind a
disclosure. Rejected input gets an icon, no exception type and no traceback.

| | Dark | Light |
|---|---|---|
| Before | ![](screenshots/before-dark-07-error-state.png) | ![](screenshots/before-light-07-error-state.png) |
| After | ![](screenshots/after-dark-07-error-state.png) | ![](screenshots/after-light-07-error-state.png) |

---

## What changed underneath

| Measure | Before | After |
|---|---|---|
| Classification confidence shown | 3% | 96% |
| Content width in a 1584px main area | 1040px | 1360px |
| Sidebar gap above Recent Documents | 146px | 81px |
| Light-mode override rules | 51 `!important` across 42 selectors | 0 — one token block |
| Design tokens | — | 39, same roles in both themes |
| Streamlit test IDs targeted | 25, of which 4 matched nothing | dead ones removed or documented |

"""What each available response would do on the recorded summaries.

    python tests/e2e/summary_eval/options.py

The numbers in RESULTS.md "Choosing a response" come from here, measured on
the same 85 adjudicated summaries as the rates: no response is argued for on
numbers the recording cannot reproduce.

A candidate CHECK is scored twice, because the two scores answer different
questions. Per summary: of the summaries it would flag, how many really state a
false figure, and how many of the summaries that do state one it would reach.
Per flag: of the figures it would name, how many are false statements rather
than right arithmetic or a labelled assumption.

REGENERATION is scored as the share of summaries with a false figure that would
still have one on a second draw, at the rate their own document showed across
five draws. It assumes draws are independent, which flatters regeneration if
anything: a document that failed five times out of five is not going to
succeed on the sixth by being asked again.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
FALSE = ("year", "wrong", "invented")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"summary_eval_{name}_for_options", HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


adjudicate = _load("adjudicate")
figures = adjudicate.figures


def _extraction_check():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from skills.structured_extraction_skill import unverified_numbers
    return unverified_numbers


def candidate_flags(per_summary, trials, sources):
    """{candidate: {(fixture, round): [verdict of each figure it would flag]}}.
    A flag the adjudication has no figure for (the classifier found the number
    stated) is recorded as "not adjudicated"."""
    unverified_numbers = _extraction_check()
    out = defaultdict(dict)
    for name, rnd, _kind, verdicts in per_summary:
        key = (name, rnd)
        out["years the source never states"][key] = [v for v, _d, r, _ in verdicts if r["category"] == "year"]
        out["years and figures no arithmetic explains"][key] = [
            v for v, _d, r, _ in verdicts if r["category"] in ("year", "unexplained")]
        out["every figure absent from the source"][key] = [v for v, *_ in verdicts]
        by_digits = defaultdict(list)
        for v, _d, r, _ in verdicts:
            by_digits[re.sub(r"\D", "", r["token"])].append(v)
        text = figures.prepare(trials[key]["summary"])
        out["extraction's unverified_numbers"][key] = [
            (by_digits.get(digits) or ["not adjudicated"])[0]
            for digits in unverified_numbers(text, sources[name]["full_text"])]
    return dict(out)


def score(flags_by_summary, per_summary):
    false_summaries = {(n, r) for n, r, _k, vs in per_summary if any(v in FALSE for v, *_ in vs)}
    flagged = {key for key, fl in flags_by_summary.items() if fl}
    verdicts = Counter(v for fl in flags_by_summary.values() for v in fl)
    return {
        "summaries flagged": len(flagged),
        "flagged with a false figure": len(flagged & false_summaries),
        "false-figure summaries": len(false_summaries),
        "flags": sum(verdicts.values()),
        "false flags": sum(verdicts[v] for v in FALSE),
        "verdicts": dict(verdicts),
    }


def regeneration(per_summary, verdicts=FALSE):
    """(expected summaries still false on a second draw, summaries false now)."""
    by_fixture = defaultdict(list)
    for name, _rnd, _kind, vs in per_summary:
        by_fixture[name].append(any(v in verdicts for v, *_ in vs))
    now = sum(sum(runs) for runs in by_fixture.values())
    again = sum(sum(runs) * sum(runs) / len(runs) for runs in by_fixture.values())
    return again, now


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    trials_path = HERE / "trials.jsonl"
    per_summary, problems = adjudicate.adjudicate(trials_path)
    if problems:
        print("adjudication has problems; run adjudicate.py")
        return 1
    trials = {(t["fixture"], t["round"]): t for t in map(json.loads, open(trials_path, encoding="utf-8"))}
    sources = json.loads(trials_path.with_suffix(".sources.json").read_text(encoding="utf-8"))
    n = len(per_summary)
    for name, flags in candidate_flags(per_summary, trials, sources).items():
        s = score(flags, per_summary)
        print(f"\n{name}")
        print(f"  summaries flagged {s['summaries flagged']}/{n}; with a false figure "
              f"{s['flagged with a false figure']}/{s['summaries flagged']}; false-figure summaries reached "
              f"{s['flagged with a false figure']}/{s['false-figure summaries']}")
        share = s["false flags"] / s["flags"] if s["flags"] else 0
        print(f"  figures flagged {s['flags']}; false statements {s['false flags']} ({share:.0%}); {s['verdicts']}")
    again, now = regeneration(per_summary)
    years_again, years_now = regeneration(per_summary, ("year",))
    print(f"\nregeneration: {again:.1f} of {now} summaries with a false figure would still have one ({again / now:.0%}); "
          f"years alone {years_again:.1f} of {years_now} ({years_again / years_now:.0%})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

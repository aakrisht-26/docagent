"""What the classifier alone says about the recorded summaries, and how far to trust it.

    python tests/e2e/summary_eval/analyse.py [--trials trials.jsonl] [--calibrate] [--review]

Per fixture: how many LLM summaries contain at least one unstated year, one
computed total, one figure derived with its working in the same sentence, one
figure only a wide pool explains, one unexplained figure. Wilson 95% intervals.
These are CLASSIFIER counts; the rates to quote come from adjudicate.py.

--calibrate  how far to trust "computed": every computed figure is jittered by
             3-15% in the same format, put back in ITS OWN SENTENCE, and
             classified again. The share of decoys still computed on the same
             basis or an earlier one is the coincidence rate, by basis and kind.
--review     every unstated year and unexplained figure in context
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASES = ["total", "sentence", "row", "passage", "between"]


def _load_figures():
    """By path, so no directory is left on sys.path to shadow another module."""
    spec = importlib.util.spec_from_file_location("summary_eval_figures", HERE / "figures.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


figures = _load_figures()


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def load(trials_path: Path):
    trials = [json.loads(line) for line in open(trials_path, encoding="utf-8")]
    sources = json.loads(Path(trials_path).with_suffix(".sources.json").read_text(encoding="utf-8"))
    return [t for t in trials if (t.get("method") or "").startswith("llm_")], sources


def kind(row: dict) -> str:
    if row["percent"]:
        return "percentage"
    if row["decimals"] >= 1:
        return "decimal"
    return "integer <1000" if abs(row["value"]) < 1000 else "integer >=1000"


def decoys(token: str, rng: random.Random, count: int = 5):
    fig = next(figures.figures_in(token), None)
    if fig is None:
        return []
    prefix = re.match(r"^[$£€]?\s?", token).group(0)
    suffix = re.sub(r"^[$£€]?\s?-?[\d,]+(?:\.\d+)?", "", token)
    out, tries = [], 0
    while len(out) < count and tries < 50:
        tries += 1
        factor = rng.uniform(1.03, 1.15) if rng.random() < 0.5 else rng.uniform(0.85, 0.97)
        value = round(fig["value"] * factor, fig["decimals"])
        body = f"{value:,.{fig['decimals']}f}" if fig["comma"] else f"{value:.{fig['decimals']}f}"
        candidate = f"{prefix}{body}{suffix}"
        if candidate != token:
            out.append(candidate)
    return out


def calibrate(trials, sources, seed: int = 20260916):
    """{(basis, kind): Counter of what each decoy was classified as}."""
    rng = random.Random(seed)
    parsed, table = {}, defaultdict(Counter)
    for t in trials:
        name = t["fixture"]
        source = parsed.setdefault(name, figures.Source(sources[name]["full_text"]))
        text = figures.prepare(t["summary"])
        for r in figures.classify(text, source, prepared=True):
            if r["category"] != "computed":
                continue
            s0, s1 = r["span"]
            end = r["start"] + len(r["token"])
            for fake in decoys(r["token"], rng):
                sentence = text[s0:r["start"]] + fake + text[end:s1]
                got = [g for g in figures.classify(sentence, source, prepared=True) if g["start"] == r["start"] - s0]
                if got:
                    table[(r["basis"], kind(r))][got[0]["basis"] or got[0]["category"]] += 1
    return table


def coincidence(counts: Counter, basis: str):
    """Decoys still computed on `basis` or one tried before it, and the total."""
    earlier = sum(v for b, v in counts.items() if b in BASES[:BASES.index(basis) + 1])
    return earlier, sum(counts.values())


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", default=str(HERE / "trials.jsonl"))
    ap.add_argument("--review", action="store_true")
    ap.add_argument("--calibrate", action="store_true")
    args = ap.parse_args()

    trials, sources = load(Path(args.trials))
    keys = ["year", "total", "sentence", "weak", "unexplained"]
    stats = defaultdict(lambda: {"n": 0, **{k: 0 for k in keys}})
    parsed, review, by_category = {}, [], Counter()
    for t in trials:
        name = t["fixture"]
        source = parsed.setdefault(name, figures.Source(sources[name]["full_text"]))
        rows = figures.classify(t["summary"], source)
        s = stats[name]
        s["n"] += 1
        for r in rows:
            by_category[r["category"] + (f":{r['basis']}" if r["basis"] else "")] += 1
            if r["category"] in ("year", "unexplained"):
                review.append((name, t["round"], r))
        s["year"] += any(r["category"] == "year" for r in rows)
        s["total"] += any(r["basis"] == "total" for r in rows)
        s["sentence"] += any(r["basis"] == "sentence" for r in rows)
        s["weak"] += any(r["basis"] in ("row", "passage", "between") for r in rows)
        s["unexplained"] += any(r["category"] == "unexplained" for r in rows)

    print(f"{'fixture':26} " + " ".join(f"{k:>11}" for k in keys))
    for name, s in sorted(stats.items()):
        print(f"{name:26} " + " ".join(f"{s[k]}/{s['n']}".rjust(11) for k in keys))
    n = sum(s["n"] for s in stats.values())
    for k in keys:
        got = sum(s[k] for s in stats.values())
        lo, hi = wilson(got, n)
        print(f"  summaries with {k:12} {got:3}/{n}  {got / n:5.0%}  95% CI [{lo:.0%}, {hi:.0%}]")
    print("\n  figures by category:", dict(sorted(by_category.items(), key=lambda kv: -kv[1])))

    if args.calibrate:
        print("\n--- calibration: computed figures jittered 3-15% in their own sentence, classified again")
        table = calibrate(trials, sources)
        for (basis, k), counts in sorted(table.items(), key=lambda kv: (BASES.index(kv[0][0]), kv[0][1])):
            hit, total = coincidence(counts, basis)
            lo, hi = wilson(hit, total)
            print(f"  {basis:9} {k:15} decoys {total:5}  still computed {hit:5} ({hit / total:5.1%}, CI [{lo:.1%}, {hi:.1%}])")

    if args.review:
        print("\n--- review: every unstated year and unexplained figure")
        for name, rnd, r in review:
            near = f"  NEAR {r['near']}" if r["near"] else ""
            print(f"[{name} r{rnd}] {r['category']:11} {r['token']:>12}{' ~' if r['hedged'] else ''}{near}\n      ...{r['context']}...")
    return 0


if __name__ == "__main__":
    sys.exit(main())

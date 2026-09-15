"""Apply the hand adjudication to every recorded summary and report the rates.

    python tests/e2e/summary_eval/adjudicate.py [--show]

THE RATES IN RESULTS.md COME FROM HERE, not from the classifier alone. The
classifier sorts figures; a person decided what the unsettled ones are, and
this script holds that decision to account.

It fails (exit 1) if any figure needing a verdict has none, if a rule matches
nothing, or if a verdict disagrees with its own arithmetic: a `correct` figure
must equal its `expr` at the precision shown, and a `wrong` one must not. "≈"
buys one unit of the last digit shown, or 1% off a figure that is not a
percentage -- 1% of a share near 100 would excuse "≈ 99.5%" for 99.2%, which
the first version of this rule did.

Figures computed on the `total` or `sentence` basis need no rule: their
calibrated coincidence rate is 0-9% (analyse.py --calibrate). A `sentence`
derivation whose operands are not source figures does need one, because right
arithmetic on a wrong or assumed figure is not a fact about the document.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
OPERAND = re.compile(r"\d+(?:\.\d+)?(?:e[+-]?\d+)?")
NOT_SCORED = ("stated", "rounded", "small")


def _load_figures():
    """By path, so no directory is left on sys.path to shadow another module."""
    spec = importlib.util.spec_from_file_location("summary_eval_figures", HERE / "figures.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


figures = _load_figures()


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def shown_equal(token: str, hedged: bool, correct: float) -> bool:
    fig = next(figures.figures_in(token))
    value, correct = abs(fig["value"] * fig["scale"]), abs(correct)
    tol = 0.5 * 10 ** -fig["decimals"] * fig["scale"]
    digits = str(int(abs(fig["value"])))
    if fig["decimals"] == 0 and fig["scale"] == 1.0 and abs(fig["value"]) >= 1000:
        tol = 0.5 * 10 ** (len(digits) - len(digits.rstrip("0")))   # "$384,000" claims 3 figures
    if abs(value - correct) <= tol + 1e-9 * max(1.0, correct):
        return True
    if not hedged:
        return False
    unit = 10 ** -fig["decimals"] * fig["scale"]
    return abs(value - correct) <= unit + 1e-9 or (not fig["percent"] and abs(value - correct) <= 0.01 * correct)


def is_slip(token: str, correct: float) -> bool:
    fig = next(figures.figures_in(token))
    value, correct = abs(fig["value"] * fig["scale"]), abs(correct)
    return (abs(value - correct) <= 1.5 * 10 ** -fig["decimals"] * fig["scale"]
            or abs(value - correct) <= 0.01 * correct)


def rule_for(rules, fixture, row):
    token = re.sub(r"\s", "", row["token"])   # the model writes U+202F before "%"
    for rule in rules:
        if rule["fixture"] == "*":
            if row["category"] == rule.get("category"):
                return rule
            continue
        if rule["fixture"] != fixture or token not in rule["tokens"]:
            continue
        if "context" in rule and rule["context"].lower() not in row["context"].lower():
            continue
        return rule
    return None


def backed(source, why: str) -> bool:
    expr = why.split(" (")[0]
    if expr.startswith("sum of the sentence"):
        return False
    if expr.endswith("*100") and "percent" in why:   # the literal, not an operand
        expr = expr[:-4]
    for tok in OPERAND.findall(expr):
        x = float(tok)
        if not (source.stated(x) or source.stated(x * 100) or source.stated(x / 1e6) or source.stated(x / 1e3)
                or any(figures._close(x, t) for t in source.aggregates)):
            return False
    return True


def adjudicate(trials_path: Path = HERE / "trials.jsonl", adjudication_path: Path = HERE / "adjudication.json"):
    """Returns (per_summary, problems). per_summary is a list of
    (fixture, round, kind, [(verdict, detail, row, rule)])."""
    rules = json.loads(Path(adjudication_path).read_text(encoding="utf-8"))["rules"]
    trials = [json.loads(line) for line in open(trials_path, encoding="utf-8")]
    sources = json.loads(Path(trials_path).with_suffix(".sources.json").read_text(encoding="utf-8"))
    parsed, problems, per_summary, used = {}, [], [], set()
    for t in trials:
        if not (t.get("method") or "").startswith("llm_"):
            continue
        name = t["fixture"]
        source = parsed.setdefault(name, figures.Source(sources[name]["full_text"]))
        verdicts = []
        for r in figures.classify(t["summary"], source):
            if r["category"] in NOT_SCORED:
                continue
            if r["basis"] == "total":
                verdicts.append(("correct", "total", r, None))
                continue
            if r["basis"] == "sentence" and backed(source, r["why"]):
                verdicts.append(("correct", "derived", r, None))
                continue
            where = f"[{name} r{t['round']}] {r['token']!r}"
            rule = rule_for(rules, name, r)
            if rule is None:
                problems.append(f"NO RULE {where} {r['category']}/{r['basis']}: ...{r['context']}...")
                continue
            used.add(id(rule))
            verdict, detail = rule["verdict"], ""
            if "expr" in rule:
                correct = eval(rule["expr"], {"__builtins__": {}})   # arithmetic written in adjudication.json
                equal = shown_equal(r["token"], r["hedged"], correct)
                if verdict == "correct" and not equal:
                    problems.append(f"NOT EQUAL {where} vs {rule['expr']} = {correct:,.6g}")
                if verdict == "wrong" and equal:
                    problems.append(f"ACTUALLY RIGHT {where} = {rule['expr']} = {correct:,.6g}")
                if verdict == "wrong":
                    detail = "slip" if is_slip(r["token"], correct) else "gross"
            elif verdict == "correct" and not rule.get("range"):
                problems.append(f"NO EXPR {where}: a correct verdict must carry its arithmetic")
            elif verdict == "wrong":
                detail = "gross"
            if verdict == "correct":
                detail = "total" if rule.get("kind") == "total" else "derived"
            verdicts.append((verdict, detail, r, rule))
        per_summary.append((name, t["round"], sources[name]["kind"], verdicts))
    for rule in rules:
        if rule["fixture"] != "*" and id(rule) not in used:
            problems.append(f"UNUSED RULE {rule['fixture']} {rule['tokens']} {rule.get('context', '')}".rstrip())
    return per_summary, problems


def _has(verdicts, verdict, detail=None):
    return any(v == verdict and (detail is None or d == detail) for v, d, _r, _rule in verdicts)


def _wrong_total(verdicts):
    return any(v == "wrong" and rule and rule.get("kind") == "total" for v, _d, _r, rule in verdicts)


FLAGS = {
    "invented year": lambda vs: _has(vs, "year"),
    "computed total, correct": lambda vs: _has(vs, "correct", "total"),
    "computed total, wrong": _wrong_total,
    "derived figure, correct": lambda vs: _has(vs, "correct", "derived"),
    "wrong figure": lambda vs: _has(vs, "wrong"),
    "  gross error": lambda vs: _has(vs, "wrong", "gross"),
    "  slips only": lambda vs: _has(vs, "wrong") and not _has(vs, "wrong", "gross"),
    "invented figure": lambda vs: _has(vs, "invented"),
    "outside figure": lambda vs: _has(vs, "outside"),
    "restatement": lambda vs: _has(vs, "restated"),
    "year, wrong or invented": lambda vs: _has(vs, "year") or _has(vs, "wrong") or _has(vs, "invented"),
    "any figure not in source": lambda vs: bool(vs),
}


def rates(per_summary):
    """{group: {flag: (k, n)}} for all summaries, yearless sources, dated sources."""
    groups = {"all": per_summary,
              "e2e": [p for p in per_summary if p[2] == "e2e"],
              "eval": [p for p in per_summary if p[2] == "eval"]}
    return {g: {f: (sum(fn(p[3]) for p in rows), len(rows)) for f, fn in FLAGS.items()}
            for g, rows in groups.items()}


def figure_counts(per_summary) -> Counter:
    return Counter(f"{v}:{d}" if d else v for _n, _r, _k, vs in per_summary for v, d, _row, _rule in vs)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", default=str(HERE / "trials.jsonl"))
    ap.add_argument("--adjudication", default=str(HERE / "adjudication.json"))
    ap.add_argument("--show", action="store_true", help="every adjudicated figure with its verdict")
    args = ap.parse_args()

    per_summary, problems = adjudicate(Path(args.trials), Path(args.adjudication))
    if args.show:
        for name, rnd, _kind, vs in per_summary:
            for v, d, r, rule in vs:
                expr = (rule or {}).get("expr", "")
                print(f"[{name} r{rnd}] {r['token']:>12} {v:9} {d:7} {expr[:40]:40} ...{r['context'][20:110]}...")

    print(f"summaries: {len(per_summary)}")
    print("figures by verdict:", dict(sorted(figure_counts(per_summary).items(), key=lambda kv: -kv[1])))
    labels = {"all": "all", "e2e": "e2e fixtures (no source states a year)", "eval": "eval fixtures (every source is dated)"}
    for group, flags in rates(per_summary).items():
        n = next(iter(flags.values()))[1]
        print(f"\n== {labels[group]}: {n} summaries")
        for flag, (k, n) in flags.items():
            lo, hi = wilson(k, n)
            print(f"  {flag:26} {k:3}/{n}  {k / n if n else 0:5.0%}  95% CI [{lo:.0%}, {hi:.0%}]")

    print("\n== per fixture: summaries with a year / correct total / wrong / invented / outside figure")
    by_fixture = defaultdict(list)
    for name, _rnd, _kind, vs in per_summary:
        by_fixture[name].append(vs)
    for name, runs in sorted(by_fixture.items()):
        cells = [sum(_has(vs, v, d) for vs in runs) for v, d in
                 (("year", None), ("correct", "total"), ("wrong", None), ("invented", None), ("outside", None))]
        print(f"  {name:26} " + "  ".join(f"{c}/{len(runs)}" for c in cells))

    if problems:
        print(f"\n== PROBLEMS: {len(problems)}")
        for p in problems:
            print("  " + p)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

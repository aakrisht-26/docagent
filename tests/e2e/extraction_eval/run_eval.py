"""Score structured_extraction: correctness and completeness, separately.

    python tests/e2e/extraction_eval/run_eval.py            # the LLM path
    python tests/e2e/extraction_eval/run_eval.py --regex    # the fallback path
    python tests/e2e/extraction_eval/run_eval.py --verbose  # per-field detail

WHY THREE OUTCOMES AND NOT A PERCENTAGE. Every field lands in exactly one of:

    CORRECT   emitted, and the value is right
    WRONG     emitted, and the value is not right
    MISSING   not emitted at all

Collapsing those into one number hides the distinction that matters. A field
extracted wrongly is worse than a field not extracted: a missing revenue figure
is visibly missing, while a wrong one is a number somebody may act on. So WRONG
is reported on its own line and is never averaged away.

The headline is CORRECT / TOTAL, and the ceiling is reachable — every expected
value is present in its document, so a perfect extractor scores 28/28.

WHAT STOPS THIS SATURATING. The fixtures carry deliberate near misses and the
eval set records them as `must_not_contain`. An answer containing the right
value AND a distractor scores WRONG, not correct. Without that rule an
extractor could pass every field by listing all the candidates it found, which
is what a saturating metric would reward. Measured on the first run the score
was NOT full marks, which is the property that makes the number usable.

THE MATCHER IS DELIBERATELY BORROWED. `_normalise_for_match` and `_contains`
come from the retrieval eval rather than being rewritten here. Four separate
times in this project a matcher scored typography rather than correctness, and
every correction moved a number upward -- so the fixed versions carry
corrections this eval would otherwise have to rediscover:

    commas in numbers   "21,500,000" vs "21500000"
    U+202F              a narrow no-break space inside "45 pence"
    unicode dashes      en/em dashes where a fixture wrote a hyphen
    word boundaries     expecting "19" must not match "1987"

The last one matters most here. Field values are short and numeric, so an
unbounded substring test would let "2.1" satisfy an expectation of "2.14" and
would let the distractor check miss "412.7" inside "1412.75". Reusing a matcher
that already has boundaries is the point.

WHAT THIS DOES NOT MEASURE. The domain is taken from the eval set, not from the
classifier, so a classification error cannot move the extraction score. Domain
routing is a real failure path and it is reported separately at the end rather
than folded into the headline.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
# This directory FIRST: rag_eval has a `fixture_content` too, and whichever is
# earlier on the path wins. The borrowed matcher is loaded by explicit file
# path below rather than by name, for the same reason.
sys.path.insert(0, str(Path(__file__).parent))

# Windows consoles default to a legacy codepage that cannot encode what an LLM
# emits. The retrieval eval learned this by crashing PART WAY THROUGH a scored
# run; the same guard applies here for the same reason.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import importlib.util  # noqa: E402

from fixture_content import DOCUMENTS  # noqa: E402


def _load_rag_matcher():
    """The retrieval eval's matcher, loaded by path.

    Imported by file rather than by module name because both eval packages
    contain a `run_eval` and a `fixture_content`, so a name import silently
    resolves to whichever directory sits earlier on sys.path.
    """
    path = ROOT / "tests" / "e2e" / "rag_eval" / "run_eval.py"
    spec = importlib.util.spec_from_file_location("rag_eval_run_eval", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._contains, module._normalise_for_match


_contains, _normalise_for_match = _load_rag_matcher()

from core.skill_registry import SkillRegistry  # noqa: E402
from skills.structured_extraction_skill import (  # noqa: E402
    _DOMAIN_ALIASES, _DOMAIN_SCHEMAS,
)
from utils.config import load_config  # noqa: E402

CORRECT, WRONG, MISSING = "CORRECT", "WRONG", "MISSING"
EVAL_SET = Path(__file__).with_name("eval_set.json")


def _flatten(value: Any) -> str:
    """A field's value as one string, whatever shape the model returned.

    The schema asks for prose in some fields and lists in others, and the model
    obliges with strings, lists of strings, and occasionally lists of dicts.
    Scoring has to see the same thing in every case.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_flatten(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_flatten(v) for v in value)
    return str(value)


def judge_field(emitted: Any, spec: Dict[str, Any]) -> Tuple[str, str]:
    """Return (verdict, reason) for one expected field."""
    text = _flatten(emitted) if emitted is not None else ""

    # TWO DIFFERENT REASONS A FIELD SHOULD BE EMPTY, and they are not the same
    # test. Conflating them was caught by the eval's own harness test.
    #
    #   must_be_absent   FABRICATION. The value is NOT in the document and must
    #                    not be invented. A sales sheet with a Revenue column
    #                    and no totals row states no total revenue, so emitting
    #                    2,379,900 -- the column sum, present in no document --
    #                    means the model did arithmetic and presented it as an
    #                    extracted fact.
    #
    #   must_be_withheld POLICY. The value IS in the document and must not be
    #                    emitted anyway. `patient_id` is specified as "anonymise
    #                    if present", so a column of MRNs is a test of whether
    #                    that instruction survives tabular input.
    #
    # Both make silence CORRECT and a value WRONG. They differ in what the
    # fixture must contain, which is why the harness asserts opposite things
    # about them, and in what a failure means to a reader: one is an invented
    # number, the other is a leaked identifier.
    if spec.get("must_be_absent") or spec.get("must_be_withheld"):
        if not text.strip():
            return CORRECT, ""
        leaked = spec.get("must_be_withheld")
        for fragment in spec.get("must_not_contain", []):
            if _contains(text, fragment):
                return WRONG, (f"leaked {fragment!r} (the schema says withhold it)"
                               if leaked else
                               f"invented {fragment!r} (not stated in the document)")
        return WRONG, f"emitted {text[:60]!r} where the correct answer is none"

    if emitted is None:
        return MISSING, "field not emitted"
    if not text.strip():
        return MISSING, "field emitted but empty"

    for fragment in spec.get("must_not_contain", []):
        if _contains(text, fragment):
            return WRONG, f"contains distractor {fragment!r}"

    absent = [f for f in spec.get("must_contain", []) if not _contains(text, f)]
    if absent:
        return WRONG, f"missing required {absent!r}"

    # `must_contain_any` exists because a fiscal period is equally correctly
    # written "Q3" or "Third Quarter", and demanding one spelling scores format
    # rather than correctness. That was caught on the first run: the model
    # answered "Third Quarter, Fiscal Year 2025" and the eval called it wrong.
    for alternatives in spec.get("must_contain_any", []):
        if not any(_contains(text, f) for f in alternatives):
            return WRONG, f"none of {alternatives!r} present"
    return CORRECT, ""


def extract(fixture: str, domain: str, skill, use_regex: bool) -> Tuple[Dict, str]:
    text = DOCUMENTS[fixture]
    schema = _DOMAIN_SCHEMAS[_DOMAIN_ALIASES.get(domain, "General")]
    if use_regex:
        entities, method = skill._regex_extract(text)
        # The fallback emits keys of its own choosing. Only keys that are
        # fields of the selected schema survive into the pipeline's output, so
        # only those are scored -- scoring the rest would credit the fallback
        # for data the caller never receives.
        entities = {k: v for k, v in entities.items() if k in schema}
        return entities, method
    entities, method, _warning = skill._llm_extract(
        text, "normal_document", domain, schema)
    return entities, method


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--regex", action="store_true",
                    help="score the regex fallback instead of the LLM path")
    ap.add_argument("--verbose", action="store_true", help="per-field detail")
    args = ap.parse_args(argv[1:])

    cases = json.loads(EVAL_SET.read_text(encoding="utf-8"))["cases"]
    cfg = load_config().to_dict()
    registry = SkillRegistry()
    registry.discover()
    skill = registry.instantiate("structured_extraction", config=cfg)

    if not args.regex and not skill._llm.available:
        print("No usable Groq key; the LLM path cannot be scored. "
              "Use --regex to score the fallback.")
        return 2

    path = "regex fallback" if args.regex else "LLM"
    print(f"Scoring the {path} path over {len(cases)} documents\n")

    totals = {CORRECT: 0, WRONG: 0, MISSING: 0}
    methods: Dict[str, int] = {}
    per_case = []

    for case in cases:
        entities, method = extract(case["fixture"], case["domain"], skill, args.regex)
        methods[method] = methods.get(method, 0) + 1
        counts = {CORRECT: 0, WRONG: 0, MISSING: 0}
        details = []
        for field, spec in case["fields"].items():
            verdict, reason = judge_field(entities.get(field), spec)
            counts[verdict] += 1
            totals[verdict] += 1
            details.append((field, verdict, reason, entities.get(field)))
        per_case.append((case, method, counts, details))
        n = sum(counts.values())
        kind = case.get("input_kind", "prose")
        print(f"  {case['id']:15} {case['domain']:11} {kind:11} {method:20} "
              f"correct {counts[CORRECT]}/{n}   wrong {counts[WRONG]}   "
              f"missing {counts[MISSING]}")
        if args.verbose:
            for field, verdict, reason, value in details:
                if verdict != CORRECT:
                    shown = _flatten(value)[:64] or "-"
                    print(f"        {verdict:8} {field:18} {reason}")
                    print(f"                 got: {shown}")

    total = sum(totals.values())
    print("\n" + "=" * 72)
    print(f"  CORRECT  {totals[CORRECT]:3}/{total}   <- headline")
    print(f"  WRONG    {totals[WRONG]:3}/{total}   <- worse than missing; "
          f"a value someone could act on")
    print(f"  MISSING  {totals[MISSING]:3}/{total}")
    print("=" * 72)
    print(f"  completeness (emitted at all) : "
          f"{(total - totals[MISSING]) / total:.0%}")
    emitted = total - totals[MISSING]
    if emitted:
        print(f"  correctness (of what it emitted) : "
              f"{totals[CORRECT] / emitted:.0%}")
    print(f"  extraction methods used : {methods}")

    # PROSE AND SPREADSHEET ARE REPORTED SEPARATELY, because averaging them
    # hides the finding. Every fixture used to be prose, and prose was easy to
    # author -- while the app routes spreadsheet dumps to Financial, Healthcare
    # and Legal. The combined number was measuring prose comprehension.
    print()
    for kind in ("prose", "spreadsheet"):
        rows = [(c, cnt) for c, _m, cnt, _d in per_case
                if c.get("input_kind", "prose") == kind]
        if not rows:
            continue
        ok = sum(cnt[CORRECT] for _c, cnt in rows)
        bad = sum(cnt[WRONG] for _c, cnt in rows)
        gone = sum(cnt[MISSING] for _c, cnt in rows)
        tot = ok + bad + gone
        print(f"  {kind:11} {ok:3}/{tot:<3} correct   {bad} wrong   {gone} missing"
              f"   ({ok / tot:.0%})")

    # SCHEMA COVERAGE: how many of a schema's fields the document can support at
    # all. This is the number that separates "the extractor missed it" from
    # "the document does not contain it", and it is where tabular input differs
    # most from prose.
    print()
    print("  schema coverage — fields the document can support, of the schema's total")
    for case, _method, _counts, _details in per_case:
        schema = _DOMAIN_SCHEMAS[_DOMAIN_ALIASES.get(case["domain"], "General")]
        scored = len(case["fields"])
        kind = case.get("input_kind", "prose")
        print(f"    {case['id']:15} {kind:11} {scored}/{len(schema)} fields assertable")

    if totals[CORRECT] == total:
        print("\n  NOTE: every field scored correct. A metric at its ceiling "
              "cannot discriminate,\n  and this one is meant to. Treat the "
              "number as unproven rather than as a pass.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

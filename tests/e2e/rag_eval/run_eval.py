"""Score DocumentChatSkill retrieval against tests/e2e/rag_eval/eval_set.json.

Two metrics, reported separately because they fail for different reasons:

  RETRIEVAL ACCURACY  did the selector put the answering chunk in the context it
                      handed to the model? Costs nothing — no API calls — so it
                      can be run freely and is the primary number.

  ANSWER CORRECTNESS  did the model's reply contain the expected fact? Needs one
                      LLM call per case, so it is opt-in via --with-answers.
                      A wrong answer with correct retrieval is a generation
                      problem; a wrong answer with failed retrieval is not.

Results are broken out per fixture rather than blended, because three of the
fixtures are trivially 100% by construction (2 chunks each, and the selector
returns the whole document), and averaging them in would mask the real signal.

Usage:
    python tests/e2e/rag_eval/run_eval.py                 # retrieval only, free
    python tests/e2e/rag_eval/run_eval.py --keyword       # force the fallback path
    python tests/e2e/rag_eval/run_eval.py --with-answers  # also scores answers
    python tests/e2e/rag_eval/run_eval.py --json          # machine-readable

`--keyword` disables embeddings for the run so the keyword fallback can be
measured on its own. That path serves every query whenever the model is
missing, so its score matters independently of the embedding score.
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SAMPLES = HERE.parent / "samples"

sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from core.models import SkillInput  # noqa: E402
from core.skill_registry import SkillRegistry  # noqa: E402
from utils.config import load_config  # noqa: E402

TRIVIAL = {"sample_report.pdf", "sample_scanned.pdf", "sample_sales.xlsx"}

_PARSE_CACHE: dict = {}


def load_chunks(fixture: str, cfg: dict, registry: SkillRegistry) -> list:
    """Parse a fixture into the chunk dicts the chat skill consumes."""
    if fixture in _PARSE_CACHE:
        return _PARSE_CACHE[fixture]

    path = SAMPLES / fixture
    if not path.exists():
        raise SystemExit(
            f"Missing fixture: {path}\nRegenerate with: python tests/e2e/make_samples.py"
        )

    reader = "excel_reader" if path.suffix.lower() in (".xlsx", ".xls", ".csv") else "pdf_reader"
    key = "excel" if reader == "excel_reader" else "pdf"
    out = registry.instantiate(reader, config=cfg[key]).safe_execute(
        SkillInput(data={"file_path": str(path)})
    )
    if not out.success:
        raise SystemExit(f"Could not parse {fixture}: {out.error}")

    chunks = [{"text": c.text, "page_or_sheet": c.page_or_sheet} for c in out.data.chunks]
    _PARSE_CACHE[fixture] = chunks
    return chunks


def retrieval_hit(case: dict, selected_sources: list) -> bool:
    expected = case["expected_sources"]
    if case.get("match") == "all":
        return all(e in selected_sources for e in expected)
    return any(e in selected_sources for e in expected)


def anchor_sources(chunks: list) -> list:
    """The first and last chunk, which the selector always appends.

    These are structural padding, not retrieval decisions: `_select_chunks`
    adds them regardless of the question. On the 20-page PDF that means pages 1
    and 20 appear in almost every selection, so a "5 chunk" selection is really
    3 ranked choices plus 2 freebies. Counting an anchor as a retrieval success
    would flatter any method that keeps them.
    """
    if not chunks:
        return []
    if len(chunks) == 1:
        return [chunks[0]["page_or_sheet"]]
    return [chunks[0]["page_or_sheet"], chunks[-1]["page_or_sheet"]]


def score_sources(question: str, chunks: list, chat) -> tuple:
    """(source -> score, method) using whichever retrieval method is active.

    Read from the skill rather than reimplemented here, so the ranked/incidental
    classification always uses exactly the scores the selector ranked on. A
    private copy would silently drift the moment retrieval changed.
    """
    scores, method = chat.score_chunks(question, chunks)
    return {c["page_or_sheet"]: s for c, s in zip(chunks, scores)}, method


def classify_hit(case: dict, selected_sources: list, scores: dict,
                 anchors: list) -> str:
    """'ranked' | 'incidental' | 'miss'.

    A hit only counts as **ranked** when the correct chunk *strictly outscored
    every chunk that was left out*. Anything weaker is **incidental**:

      - Tied with an excluded chunk. Its place was decided by tie-break order,
        not by relevance. `lg-xls-02` is the worked example: every quarterly
        sheet scores identically because `_tokenize` matches `[a-z]{3,}` and so
        never sees "Q1" or "Q3" at all. Asking about Q1 and asking about Q3
        produce byte-identical selections; Q1 is "correct" only because it sorts
        first. Counting that as retrieval working would be self-deception.
      - Present only as a first/last anchor, which the selector appends
        regardless of the question.

    For `match: "all"` cases every required chunk must clear the bar.
    """
    expected = case["expected_sources"]
    if not retrieval_hit(case, selected_sources):
        return "miss"

    needed = expected if case.get("match") == "all" else \
        [e for e in expected if e in selected_sources]
    excluded = [s for s in scores if s not in selected_sources]

    for source in needed:
        mine = scores.get(source, 0.0)
        # Tied with, or beaten by, something that did not make the cut.
        if any(scores.get(other, 0.0) >= mine for other in excluded):
            return "incidental"
        # Guaranteed a slot by being an anchor, without earning one.
        if source in anchors and mine <= 0:
            return "incidental"
    return "ranked"


def answer_hit(case: dict, reply: str) -> bool:
    """True if any expected fragment appears in the reply (case-insensitive)."""
    lowered = (reply or "").lower()
    return any(str(f).lower() in lowered for f in case.get("expected_answer_contains", []))


def main(argv: list) -> int:
    with_answers = "--with-answers" in argv
    as_json = "--json" in argv

    if "--keyword" in argv:
        # Force the fallback path for this run so it can be scored on its own.
        from utils import embeddings
        embeddings.is_supported = lambda: False

    spec = json.loads((HERE / "eval_set.json").read_text(encoding="utf-8"))
    cases = spec["cases"]

    cfg = load_config().to_dict()
    registry = SkillRegistry()
    registry.discover()
    chat = registry.instantiate("document_chat", config={"groq": cfg["groq"]})

    results = []
    for case in cases:
        chunks = load_chunks(case["fixture"], cfg, registry)
        selected = chat._select_chunks(case["question"], chunks)
        sources = [c["page_or_sheet"] for c in selected]

        anchors = anchor_sources(chunks)
        scores, method = score_sources(case["question"], chunks, chat)
        # Slots that were an actual choice, i.e. not structural anchors.
        chosen = [s for s in sources if s not in anchors]

        record = {
            "id": case["id"],
            "fixture": case["fixture"],
            "category": case["category"],
            "expected": case["expected_sources"],
            "selected": sources,
            "anchors_in_selection": [s for s in sources if s in anchors],
            "chosen_slots": len(chosen),
            "n_selected": len(selected),
            "n_chunks": len(chunks),
            "retrieval_hit": retrieval_hit(case, sources),
            "hit_kind": classify_hit(case, sources, scores, anchors),
            "method": method,
            "answer_hit": None,
        }

        if with_answers:
            out = chat.safe_execute(SkillInput(data={
                "user_message": case["question"],
                "document_chunks": chunks,
                "conversation_history": [],
                "domain": "General",
            }))
            record["answer_hit"] = answer_hit(case, out.data["reply"]) if out.success else False
            record["error"] = None if out.success else out.error

        results.append(record)

    if as_json:
        print(json.dumps(results, indent=2, default=str))
        return 0

    # ── Per-case detail ───────────────────────────────────────────────────────
    print("=" * 104)
    print(f"{'id':<12} {'category':<15} {'exp':<13} {'selected':<26} "
          f"{'anchors':<9} {'chosen':<7} {'result'}")
    print("=" * 104)
    for r in results:
        ans = "" if r["answer_hit"] is None else ("  ans:HIT" if r["answer_hit"] else "  ans:MISS")
        sel = ",".join(str(s) for s in r["selected"])[:25]
        exp = ",".join(str(s) for s in r["expected"])[:12]
        anc = ",".join(str(s) for s in r["anchors_in_selection"])[:8]
        kind = {"ranked": "RANKED", "incidental": "INCIDENTAL", "miss": "MISS"}[r["hit_kind"]]
        print(f"{r['id']:<12} {r['category']:<15} {exp:<13} {sel:<26} "
              f"{anc:<9} {r['chosen_slots']:<7} {kind}{ans}")

    def summarise(title: str, rows: list) -> None:
        if not rows:
            return
        hits = sum(1 for r in rows if r["retrieval_hit"])
        rank = sum(1 for r in rows if r["hit_kind"] == "ranked")
        line = (f"  {title:<34} any {hits}/{len(rows):<6} ranked {rank}/{len(rows)}")
        scored = [r for r in rows if r["answer_hit"] is not None]
        if scored:
            ah = sum(1 for r in scored if r["answer_hit"])
            line += f"   answers {ah}/{len(scored)}"
        print(line)

    print()
    print("=" * 92)
    print("BY FIXTURE")
    print("=" * 92)
    by_fixture = defaultdict(list)
    for r in results:
        by_fixture[r["fixture"]].append(r)

    meaningful = [r for r in results if r["fixture"] not in TRIVIAL]
    for fixture in sorted(by_fixture):
        label = fixture + ("  [trivial]" if fixture in TRIVIAL else "")
        summarise(label, by_fixture[fixture])

    print()
    print("=" * 92)
    print("BY CATEGORY (meaningful fixtures only)")
    print("=" * 92)
    by_cat = defaultdict(list)
    for r in meaningful:
        by_cat[r["category"]].append(r)
    for category in sorted(by_cat):
        summarise(category, by_cat[category])

    incidental = [r for r in meaningful if r["hit_kind"] == "incidental"]
    if incidental:
        print()
        print("=" * 104)
        print("INCIDENTAL HITS — correct chunk present, but not ranked for this question")
        print("=" * 104)
        for r in incidental:
            print(f"  {r['id']:<12} expected {str(r['expected']):<22} "
                  f"selected {r['selected']} (anchors {r['anchors_in_selection']})")
        print("  These inflate the 'any' number. The 'ranked' column is the honest one.")

    methods = sorted({r["method"] for r in results})
    print()
    print("=" * 104)
    print(f"  retrieval method(s) used: {', '.join(methods)}")
    summarise("HEADLINE (meaningful fixtures)", meaningful)
    summarise("trivial fixtures (no signal)", [r for r in results if r["fixture"] in TRIVIAL])
    anchor_slots = sum(len(r["anchors_in_selection"]) for r in meaningful)
    chosen_slots = sum(r["chosen_slots"] for r in meaningful)
    print(f"  {'slot accounting (meaningful)':<34} "
          f"{chosen_slots} chosen + {anchor_slots} anchor = "
          f"{chosen_slots + anchor_slots} total selected")
    print("=" * 104)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

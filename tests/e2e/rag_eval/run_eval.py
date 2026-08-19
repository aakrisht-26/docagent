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
    python tests/e2e/rag_eval/run_eval.py --multi          # the CROSS-DOCUMENT set
    python tests/e2e/rag_eval/run_eval.py --probe          # the DILUTION probe

`--keyword` disables embeddings for the run so the keyword fallback can be
measured on its own. That path serves every query whenever the model is
missing, so its score matters independently of the embedding score.

`--multi` scores the `multi_doc` block instead: one question against all six
fixtures concatenated. It is a separate run rather than extra cases because a
multi-document source is a (document, page) PAIR while a single-document source
is a bare page label, and because the single-document headline is quoted across
sessions and must stay comparable. Both sets share the fixtures, the selector
and the scoring helpers, so neither can drift away from the code that ships.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SAMPLES = HERE.parent / "samples"

sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

# Windows consoles default to a legacy codepage that cannot encode what an LLM
# emits — U+202F narrow no-break spaces, em-dashes, CJK brackets. Printing one
# raised UnicodeEncodeError and killed the run PART WAY THROUGH, after the
# scored work and the API spend, so the eval reported a truncated result that
# looked like a short run rather than a crash. Test output must never be the
# thing that breaks the test.
for _stream in ("stdout", "stderr"):
    _s = getattr(sys, _stream)
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

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

    # Retrieval operates on passages, not whole pages — same split the app
    # indexes with, so the eval measures what ships. Set DOCAGENT_PASSAGE_WORDS=0
    # to score page-level chunking instead, which is how the before/after
    # comparison is produced.
    if os.environ.get("DOCAGENT_PASSAGE_WORDS", "").strip() != "0":
        from utils.chunking import sub_chunk
        chunks = sub_chunk(chunks)

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
    # A page can now contribute several passages, so a source's score is the
    # BEST of its passages. The previous dict comprehension silently kept
    # whichever passage happened to come last, which would have scored the
    # ranking of an arbitrary fragment rather than of the page.
    best: dict = {}
    for chunk, score in zip(chunks, scores):
        source = chunk["page_or_sheet"]
        if source not in best or score > best[source]:
            best[source] = score
    return best, method


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


def leads_ranking(case: dict, scores: dict) -> bool:
    """Do the required sources occupy the TOP of the ranking?

    The headline metric, because the one it replaces had an unreachable
    ceiling. `answer_rank` returns the WORST rank across required sources, so a
    case needing two pages scores 2 even when retrieval puts them first and
    second — the best outcome that exists. Five of the 33 single-document cases
    are `match: all` with two sources, so "ranked #1" could never exceed 28/33
    however good retrieval became, and 28/33 was being read as five failures.
    A metric whose maximum is not attainable reads as a deficit that is really
    a definition.

    Here `k` is the number of sources the case actually requires — every
    expected source for `match: all`, one for `match: any` — and the case
    passes when those sources hold the top `k` positions. A single-source case
    still means "ranked first". A two-source case means "first and second, in
    either order", which is exactly what perfect retrieval looks like.

    `answer_rank` is kept and still reported: knowing HOW far down a miss
    landed is diagnostic, and it is what moves when chunking changes. It just
    is not the headline.
    """
    if not scores:
        return False
    ordered = sorted(scores.items(), key=lambda kv: -kv[1])
    positions = {src: i + 1 for i, (src, _) in enumerate(ordered)}
    expected = case["expected_sources"]

    if case.get("match") == "all":
        ranks = [positions.get(e) for e in expected]
        if any(r is None for r in ranks):
            return False
        return max(ranks) <= len(expected)

    # `any`: one of them suffices, so the bar is that one of them leads.
    return any(positions.get(e) == 1 for e in expected)


def answer_rank(case: dict, scores: dict) -> int:
    """1-based rank of the best expected source, by score. 0 if it has none.

    Why a second metric exists. The hit/miss headline is saturated: the
    selector takes the top 3 plus first/last anchors, so on an 8-page document
    it returns 4-5 of 8 pages and almost any question "hits". That measures the
    selection budget, not the ranking. Rank is the part that actually moves
    when chunking changes — and on the dense fixture it already varies while
    hit/miss does not, so it discriminates without anyone inventing questions
    designed to fail.

    Reported alongside hit/miss rather than replacing it: hit/miss is what the
    user experiences (was the answer in the context?), rank is what retrieval
    quality actually is.
    """
    if not scores:
        return 0
    ordered = sorted(scores.items(), key=lambda kv: -kv[1])
    positions = {src: i + 1 for i, (src, _) in enumerate(ordered)}
    ranks = [positions.get(e, 0) for e in case["expected_sources"]]
    ranks = [r for r in ranks if r > 0]
    if not ranks:
        return 0
    # For a cross-boundary case every expected chunk is needed, so the honest
    # figure is the worst of them; for the rest it is the best.
    return max(ranks) if case.get("match") == "all" else min(ranks)


# Typography a model emits where a fixture wrote plain ASCII. Each of these
# breaks a substring match while changing nothing a reader would notice.
_UNICODE_LOOKALIKES = {
    " ": " ", " ": " ", " ": " ", " ": " ",  # fixed spaces
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
    "‘": "'", "’": "'", "“": '"', "”": '"',
}


def _normalise_for_match(text: str) -> str:
    """Fold away formatting so the check scores correctness, not typography.

    Lowercases, strips the commas inside numbers, and maps Unicode lookalikes
    onto their ASCII equivalents.

    Every clause here was added because a CORRECT answer was being marked wrong:

      commas       `lg-xls-03` expects "21,500,000"; the model wrote
                   "21500000" after the context gained source labels and it
                   began echoing the sheet's own unpunctuated figures. That
                   would have read as labelling causing a regression.
      U+202F       `md-01` answered "45 pence per mile" with a NARROW NO-BREAK
                   SPACE between "45" and "pence". Three of the thirteen
                   cross-document cases were scored as answer failures on this
                   alone, which would have made cross-corpus answering look
                   materially worse than it is.

    Fixing the matcher rather than the fixtures is deliberate: the fixture states
    the fact, and how a model chooses to punctuate it is not the thing under
    test.
    """
    text = (text or "").lower()
    for bad, good in _UNICODE_LOOKALIKES.items():
        text = text.replace(bad, good)
    text = re.sub(r"(?<=\d),(?=\d)", "", text)
    # A hyphen between words is the same word pair as a space between them.
    # Applied to BOTH sides, so "four-year" and "four year" compare equal
    # whichever way round the fixture and the model happen to write them.
    text = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", text)
    return re.sub(r"\s+", " ", text)


def _contains(reply: str, fragment: Any) -> bool:
    """Is `fragment` present in `reply` as a WHOLE value?

    A plain substring test has no boundaries, and that cuts the opposite way to
    every normalisation above: it makes WRONG answers pass. Expecting "19"
    matched "1987" — a real figure, the incorporation year, sitting on page 2 of
    the same fixture. Expecting "94" matched "1.94". Auditing the two eval sets
    found 127 such collisions reachable from figures that genuinely appear in
    the corpus.

    So a numeric fragment must not be flanked by more digits, and a word
    fragment must not be flanked by more word characters. This is the one
    change to the matcher that can move a score DOWN, which is why it is here:
    the folding above earns its keep only if the comparison it feeds is sound.
    """
    needle = _normalise_for_match(str(fragment))
    if not needle:
        return False
    haystack = _normalise_for_match(reply)

    # A period is only a boundary problem when it is a DECIMAL POINT, i.e. when
    # a digit sits on its far side. Excluding every adjacent period instead
    # rejected "19. Next", where the period ends a sentence.
    left = r"(?<!\d)(?<!\d\.)" if needle[0].isdigit() else r"(?<!\w)"
    right = r"(?!\d)(?!\.\d)" if needle[-1].isdigit() else r"(?!\w)"
    return re.search(left + re.escape(needle) + right, haystack) is not None


def answer_hit(case: dict, reply: str) -> bool:
    """Did the reply contain the expected fact? (case-insensitive)

    Two fields, because a two-fact answer and a one-fact answer with two
    spellings are different things and one list cannot mean both:

      expected_answer_contains  ANY of these — alternative phrasings of ONE
                                fact. `lg-xls-03` lists "21,500,000" and
                                "21.5" because the model may write either.
      expected_answer_all       EVERY one of these — genuinely separate facts.
                                Half an answer to a two-part question is a
                                wrong answer.

    `match` is deliberately not consulted here: it constrains SOURCES, not
    prose. Reading it as a conjunction over `expected_answer_contains` marked
    two fully correct replies wrong — `lg-pdf-05` answered "Nineteen ... 23.4
    days" and was failed for not writing the digits "19".
    """
    required = case.get("expected_answer_all")
    if required:
        return all(_contains(reply, f) for f in required)

    return any(_contains(reply, f)
               for f in case.get("expected_answer_contains", []))


_CITE_RE = re.compile(r"\bpages?\s+(\d+)|\bp\.?\s*(\d+)\b", re.IGNORECASE)


def cited_pages(reply: str) -> list:
    """Page numbers the model claims in its own prose.

    Distinct from `used_pages`, which the SKILL computes from what it selected.
    This reads what the model actually told the user, which is what a reader
    believes. The two only agree by luck unless the context is labelled — the
    system prompt asks the model to "cite page numbers" while the context is
    assembled as bare text joined by `---`, carrying no page numbers at all.
    """
    if not reply:
        return []
    out = []
    for m in _CITE_RE.finditer(reply):
        out.append(int(m.group(1) or m.group(2)))
    return sorted(set(out))


def citation_verdict(case: dict, reply: str) -> str:
    """'correct' | 'wrong' | 'none' — for INTEGER-sourced cases only.

    Sheet-named sources are excluded: a workbook answer cites "Q3 Revenue",
    not a page number, and the regex would score it as no citation at all
    rather than as a citation of a different kind.

    'wrong' is the one that matters. A confidently cited page the answer did
    not come from is worse than no citation, because it looks verifiable.
    """
    expected = [e for e in case["expected_sources"] if isinstance(e, int)]
    if not expected:
        return "n/a"
    claimed = cited_pages(reply)
    if not claimed:
        return "none"
    return "correct" if any(c in expected for c in claimed) else "wrong"


# ══════════════════════════════════════════════════════════════════════════════
# Cross-document scoring
#
# Everything below scores the `multi_doc` block. It reuses load_chunks, the
# selector and the citation helpers; what it adds is that a source is a
# (document, page) PAIR. The single-document path above is deliberately left
# alone — its headline is quoted across sessions.
# ══════════════════════════════════════════════════════════════════════════════

def load_corpus(fixtures: list, cfg: dict, registry: SkillRegistry) -> list:
    """Every fixture's passages in one flat list, each tagged with its document.

    Tagging is safe against the shipped skill because it reads only `text`,
    `page_or_sheet` and `embedding` from a chunk; a `document` key rides along
    untouched. That is what makes a baseline measurable at all — the current
    code can be handed a corpus and its choices attributed afterwards, without
    changing it first and then measuring the thing you changed.
    """
    corpus = []
    for fixture in fixtures:
        for chunk in load_chunks(fixture, cfg, registry):
            tagged = dict(chunk)
            tagged["document"] = fixture
            corpus.append(tagged)
    return corpus


def short_name(fixture: str) -> str:
    """`sample_large_report.pdf` -> `large_report`, for a table that fits."""
    stem = fixture.rsplit(".", 1)[0]
    return stem[len("sample_"):] if stem.startswith("sample_") else stem


def pair(chunk: dict) -> tuple:
    return (chunk.get("document"), chunk.get("page_or_sheet"))


def expected_pairs(case: dict) -> list:
    return [(e["doc"], e["source"]) for e in case["expected_sources"]]


def multi_leads(case: dict, corpus: list, scores: list, chat) -> bool:
    """The cross-document twin of `leads_ranking`.

    Same reasoning: three of the thirteen cases require two (document, page)
    pairs, so any "was it first" metric is capped below 13/13 by definition.
    Here the required pairs must hold the top `k` source positions, k being how
    many the case needs.
    """
    order = sorted(range(len(corpus)), key=lambda i: scores[i], reverse=True)
    positions, seen = {}, set()
    for i in order:
        key = chat.source_key(corpus[i])
        if key in seen:
            continue
        seen.add(key)
        positions[key] = len(positions) + 1

    expected = expected_pairs(case)
    if case.get("match") == "all":
        ranks = [positions.get(e) for e in expected]
        return bool(ranks) and all(r is not None for r in ranks)             and max(ranks) <= len(expected)
    return any(positions.get(e) == 1 for e in expected)


def multi_hit(case: dict, got: list) -> bool:
    exp = expected_pairs(case)
    if case.get("match") == "all":
        return all(e in got for e in exp)
    return any(e in got for e in exp)


def ambiguous_labels(corpus: list) -> dict:
    """page label -> the documents that use it, for labels used more than once.

    This is the measurement that matters most for the baseline. `used_pages` and
    the model's prose both name a bare page label, so any label in here makes a
    citation that cannot be resolved by the person reading it.
    """
    holders = defaultdict(set)
    for chunk in corpus:
        holders[chunk.get("page_or_sheet")].add(chunk.get("document"))
    return {label: sorted(docs) for label, docs in holders.items() if len(docs) > 1}


def label_collisions(corpus: list, scores: list, limit: int = 3) -> list:
    """Passages the page-label dedupe discards in favour of a different document.

    `_select_with_roles` keeps one chunk per `page_or_sheet` so that several
    passages of one page cannot sweep the budget. Across a corpus that same key
    conflates documents: once the review's page 3 has claimed the label "3", the
    manual's page 3 is unreachable at any rank. Returns the (pair, score,
    blocker) triples that lost a slot this way while the winner was still being
    chosen.
    """
    order = sorted(range(len(corpus)), key=lambda i: scores[i], reverse=True)
    winners: dict = {}
    lost = []
    for i in order:
        label = corpus[i].get("page_or_sheet")
        if label in winners:
            j = winners[label]
            if corpus[i].get("document") != corpus[j].get("document"):
                lost.append((pair(corpus[i]), scores[i], pair(corpus[j])))
            continue
        winners[label] = i
        if len(winners) >= limit:
            break
    return lost


# Ways a model says "what I was given does not answer this". Deliberately broad:
# the cost of missing one is understating how often the model declines, which
# would make a prompt change look more effective than it is.
_DECLINE_RE = re.compile(
    r"\b(not (?:found|available|present|stated|specified|mentioned|included|"
    r"provided|contained|given)"
    r"|no (?:information|mention|reference|figure|data|detail|record|breakdown)"
    r"|does not (?:contain|state|mention|specify|provide|include|give|list)"
    r"|do not (?:contain|state|mention|specify|provide|include|give|list)"
    r"|don't (?:contain|state|mention|specify|provide|include)"
    r"|doesn't (?:contain|state|mention|specify|provide|include)"
    # "states no motorcycle rate", "gives no breakdown" — the negation attaches
    # to the verb rather than to a noun this pattern could enumerate.
    r"|(?:states?|gives?|lists?|provides?|specif(?:y|ies)|contains?|"
    r"mentions?|includes?) no\b"
    r"|cannot (?:be )?(?:determin|answer|find|establish)"
    r"|can't (?:be )?(?:determin|answer|find)"
    r"|unable to (?:find|answer|determine)"
    r"|isn't (?:in|available|provided)|is not (?:in|available|provided)"
    r"|only (?:list|cover|give|state)s?\b)", re.IGNORECASE)


def declined(reply: str) -> bool:
    """Did the reply say the context does not support an answer?"""
    return bool(_DECLINE_RE.search(reply or ""))


def asserted_traps(case: dict, reply: str) -> list:
    """Trap values the reply contains.

    A DIAGNOSTIC, not the verdict. "The manual gives 45 pence for a private
    vehicle but states no motorcycle rate" contains a trap value and is exactly
    the right answer. Only a human reading the reply can tell mention from
    assertion, so this counts mentions and the pass criterion stays `declined`.
    """
    return [t for t in case.get("must_not_assert", []) if _contains(reply, t)]


def cited_documents(reply: str, fixtures: list) -> list:
    """Documents the model names in its own prose.

    Currently zero by construction — the context is labelled `[Page 3]` with no
    document anywhere in it, so the model has nothing to name. Measured rather
    than assumed, because that is exactly how the single-document page-citation
    bug stayed invisible: the prompt asked for something the context could not
    support and nothing failed.
    """
    if not reply:
        return []
    lowered = reply.lower()
    return [f for f in fixtures
            if f.lower() in lowered or short_name(f).replace("_", " ") in lowered]


def main_multi(argv: list) -> int:
    with_answers = "--with-answers" in argv
    as_json = "--json" in argv

    if "--keyword" in argv:
        from utils import embeddings
        embeddings.is_supported = lambda: False

    spec = json.loads((HERE / "eval_set.json").read_text(encoding="utf-8"))
    block = spec["multi_doc"]
    fixtures, cases = block["corpus"], block["cases"]

    cfg = load_config().to_dict()
    registry = SkillRegistry()
    registry.discover()
    chat = registry.instantiate("document_chat", config={"groq": cfg["groq"]})

    corpus = load_corpus(fixtures, cfg, registry)
    ambiguous = ambiguous_labels(corpus)
    per_doc = defaultdict(int)
    for chunk in corpus:
        per_doc[chunk["document"]] += 1

    results = []
    for case in cases:
        selected, ranked = chat._select_with_roles(case["question"], corpus)
        sel_pairs, rank_pairs = [pair(c) for c in selected], [pair(c) for c in ranked]
        scores, method = chat.score_chunks(case["question"], corpus)

        # Which document does the single best passage live in? With one slot,
        # that is the document the answer would come from.
        best = max(range(len(corpus)), key=lambda i: scores[i])
        answer_docs = {d for d, _ in expected_pairs(case)}

        # Score the citation the READER is given. `used_pages` is the legacy
        # bare-label list kept for single-document callers; across a corpus it
        # cannot express "page 3 of the manual", so measuring it here would be
        # measuring a field this mode does not use.
        cites = chat.citable_sources_detailed(ranked, selected)
        legacy_cites = chat._citable_sources(ranked, selected)
        collisions = label_collisions(corpus, scores)

        record = {
            "id": case["id"],
            "category": case["category"],
            "expected": expected_pairs(case),
            "ranked": rank_pairs,
            "selected": sel_pairs,
            "hit_ranked": multi_hit(case, rank_pairs),
            "hit_any": multi_hit(case, sel_pairs),
            "rank1_doc_correct": corpus[best]["document"] in answer_docs,
            "leads": multi_leads(case, corpus, scores, chat),
            "rank1_doc": corpus[best]["document"],
            # Documents that reached the model without earning a ranked slot.
            "freeloading_docs": sorted({d for d, _ in sel_pairs}
                                       - {d for d, _ in rank_pairs}),
            "citations": [c["label"] for c in cites],
            # A citation resolves when it names its document. Without one, a
            # bare label that several documents share names none of them.
            "unresolvable_citations": [
                c["label"] for c in cites
                if not c.get("document") and c["source"] in ambiguous
            ],
            "citations_name_document": all(c.get("document") for c in cites) if cites else False,
            # What the page-label key WOULD have thrown away. Kept as a
            # regression guard: if this is non-empty while the expected source
            # is still ranked, the (document, page) key is doing its job.
            "legacy_key_would_discard": [p for p, _, _ in collisions],
            "legacy_citations": legacy_cites,
            # An expected passage the LEGACY page-label dedupe made unreachable.
            # The shipped selector keys on (document, page) and does not.
            "expected_suppressed": [p for p, _, _ in collisions
                                    if p in expected_pairs(case)],
            "collisions": [(p, round(s, 4), b) for p, s, b in collisions],
            "method": method,
            "answer_hit": None,
            "cited_docs": None,
        }

        if with_answers:
            out = chat.safe_execute(SkillInput(data={
                "user_message": case["question"],
                "document_chunks": corpus,
                "conversation_history": [],
                "domain": "General",
            }))
            reply = out.data["reply"] if out.success else ""
            record["answer_hit"] = answer_hit(case, reply) if out.success else False
            record["cited_docs"] = cited_documents(reply, fixtures)
            record["error"] = None if out.success else out.error

        results.append(record)

    if as_json:
        print(json.dumps(results, indent=2, default=str))
        return 0

    def fmt(pairs: list) -> str:
        return " ".join(f"{short_name(d)}:{s}" for d, s in pairs)

    print("=" * 118)
    print(f"CROSS-DOCUMENT RETRIEVAL — {len(fixtures)} documents, "
          f"{len(corpus)} passages, {len(cases)} cases")
    print(f"  corpus: " + ", ".join(
        f"{short_name(f)}={per_doc[f]}" for f in fixtures))
    print(f"  {len(ambiguous)} of {len(set(c['page_or_sheet'] for c in corpus))} "
          f"page/sheet labels are used by more than one document")
    print("=" * 118)
    print(f"{'id':<8} {'category':<16} {'expected':<30} {'ranked slots':<44} result")
    print("-" * 118)
    for r in results:
        verdict = ("RANKED" if r["hit_ranked"]
                   else "anchor-only" if r["hit_any"] else "MISS")
        ans = "" if r["answer_hit"] is None else (
            "  ans:HIT" if r["answer_hit"] else "  ans:MISS")
        print(f"{r['id']:<8} {r['category']:<16} {fmt(r['expected'])[:29]:<30} "
              f"{fmt(r['ranked'])[:43]:<44} {verdict}{ans}")

    print("-" * 118)
    ranked_n = sum(1 for r in results if r["hit_ranked"])
    anchor_n = sum(1 for r in results if r["hit_any"] and not r["hit_ranked"])
    miss_n = len(results) - ranked_n - anchor_n
    print(f"  {'retrieval (ranked slots)':<44} {ranked_n}/{len(results)}")
    print(f"  {'present only as a structural anchor':<44} {anchor_n}/{len(results)}"
          f"   {'<- looks like a hit, is not' if anchor_n else ''}")
    print(f"  {'missed entirely':<44} {miss_n}/{len(results)}")

    by_cat = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r)
    print()
    print("  BY CATEGORY")
    for category in sorted(by_cat):
        rows = by_cat[category]
        hits = sum(1 for r in rows if r["hit_ranked"])
        print(f"    {category:<40} ranked {hits}/{len(rows)}")

    print()
    leads = sum(1 for r in results if r["leads"])
    d1 = sum(1 for r in results if r["rank1_doc_correct"])
    print(f"  {'required sources lead the ranking':<44} "
          f"{leads}/{len(results)}   <- headline")
    print(f"  {'top-scoring passage is in a correct document':<44} "
          f"{d1}/{len(results)}")

    # Attribution. Recall was never the weak part of cross-corpus chat; saying
    # WHERE an answer came from was.
    resolves = sum(1 for r in results if r["citations_name_document"])
    amb = [r for r in results if r["unresolvable_citations"]]
    print(f"  {'citations naming their document':<44} {resolves}/{len(results)}")
    print(f"  {'citations that cannot be resolved to a file':<44} "
          f"{len(amb)}/{len(results)}")
    if with_answers:
        named = sum(1 for r in results if r["cited_docs"])
        print(f"  {'replies naming a document in their prose':<44} "
              f"{named}/{len(results)}")

    supp = [r for r in results if r["expected_suppressed"]]
    print(f"  {'expected source the LEGACY page key would drop':<44} "
          f"{len(supp)}/{len(results)}   (selector keys on (document, page))")

    freeloaders = sum(len(r["freeloading_docs"]) for r in results)
    print(f"  {'documents sent to the model with no ranked slot':<44} "
          f"{freeloaders} across {len(results)} queries")
    print("=" * 118)

    if amb:
        print()
        print("UNRESOLVABLE CITATIONS — the label the reader is given names "
              "more than one document")
        print("-" * 118)
        for r in amb:
            for label in r["unresolvable_citations"]:
                print(f"  {r['id']:<8} cites {label}")

    if supp:
        print()
        print("RESCUED BY THE (document, page) KEY — these would be discarded "
              "at any rank under the old page-label key")
        print("-" * 118)
        for r in supp:
            for p, score, blocker in r["collisions"]:
                if p in r["expected_suppressed"]:
                    rescued = p in r["ranked"]
                    print(f"  {r['id']:<8} {short_name(p[0])}:{p[1]} "
                          f"(score {score}) was blocked by "
                          f"{short_name(blocker[0])}:{blocker[1]}"
                          f"   -> now {'RANKED' if rescued else 'still absent'}")

    # ── Questions the corpus does not answer ─────────────────────────────────
    no_answer = block.get("no_answer_cases") or []
    if no_answer and with_answers:
        print()
        print("=" * 118)
        print(f"NO-ANSWER CASES — {len(no_answer)} questions the corpus does not answer")
        print("=" * 118)
        print(f"{'id':<8} {'category':<24} {'verdict':<10} {'traps seen':<14} reply")
        print("-" * 118)
        na_results = []
        for case in no_answer:
            out = chat.safe_execute(SkillInput(data={
                "user_message": case["question"],
                "document_chunks": corpus,
                "conversation_history": [],
                "domain": "General",
            }))
            reply = out.data["reply"] if out.success else ""
            traps = asserted_traps(case, reply)
            wants_decline = case.get("expects_decline", True)
            row = {
                "id": case["id"],
                "category": case["category"],
                "declined": declined(reply),
                "traps": traps,
                # Two criteria, because two shapes. Where nothing is answerable
                # the reply must decline. Where PART is answerable, declining
                # outright would be wrong and the test is that the fabricated
                # value stays out.
                "passed": declined(reply) if wants_decline else not traps,
                "wants_decline": wants_decline,
                "reply": reply,
            }
            na_results.append(row)
            verdict = ("DECLINED" if row["declined"] else
                       "ok" if row["passed"] else "ANSWERED")
            print(f"{case['id']:<8} {case['category']:<24} "
                  f"{verdict:<10} "
                  f"{str(row['traps'])[:13]:<14} "
                  f"{reply[:44].replace(chr(10), ' ')}")

        print("-" * 118)
        passed = sum(1 for r in na_results if r["passed"])
        wants = [r for r in na_results if r["wants_decline"]]
        print(f"  {'handled correctly':<44} {passed}/{len(na_results)}")
        print(f"  {'declined (of those that must)':<44} "
              f"{sum(1 for r in wants if r['declined'])}/{len(wants)}")
        by_cat = defaultdict(list)
        for r in na_results:
            by_cat[r["category"]].append(r)
        for category in sorted(by_cat):
            rows = by_cat[category]
            print(f"    {category:<40} "
                  f"{sum(1 for r in rows if r['passed'])}/{len(rows)}")
        seen = sum(1 for r in na_results if r["traps"])
        print(f"  {'replies containing a trap value':<44} {seen}/{len(na_results)}"
              f"   (diagnostic: mention is not assertion)")

        failures = [r for r in na_results if not r["passed"]]
        if failures:
            print()
            print("NOT HANDLED — answered without support, or asserted a "
                  "fabricated value")
            print("-" * 118)
            for r in failures:
                print(f"  {r['id']:<8} {r['reply'][:100].replace(chr(10), ' ')}")

    # One legacy-shape sample, so the reason the field changed stays visible.
    dupes = [r for r in results
             if len(r["legacy_citations"]) != len(set(map(str, r["legacy_citations"])))]
    if dupes:
        print()
        print("WHY used_pages IS NOT ENOUGH — the same bare label twice, from "
              "two different documents")
        print("-" * 118)
        for r in dupes[:3]:
            print(f"  {r['id']:<8} used_pages {r['legacy_citations']}")
            for label in r["citations"]:
                print(f"           -> {label}")
    return 0


def main_probe(argv: list) -> int:
    """Score the dilution probe: does topic mixing cost a passage its rank?

    Kept out of the two headline sets so it cannot move numbers quoted across
    sessions. Free — no API calls.
    """
    if "--keyword" in argv:
        from utils import embeddings
        embeddings.is_supported = lambda: False

    spec = json.loads((HERE / "eval_set.json").read_text(encoding="utf-8"))
    block = spec["dilution_probe"]
    cfg = load_config().to_dict()
    registry = SkillRegistry()
    registry.discover()
    chat = registry.instantiate("document_chat", config={"groq": cfg["groq"]})

    rows = []
    for case in block["cases"]:
        chunks = load_chunks(case["fixture"], cfg, registry)
        scores, method = score_sources(case["question"], chunks, chat)
        order = sorted(scores.items(), key=lambda kv: -kv[1])
        positions = {s: i + 1 for i, (s, _) in enumerate(order)}
        page = case["page"]
        rows.append({**case, "rank": positions.get(page, 0),
                     "score": scores.get(page, 0.0),
                     "top": order[0][0], "top_score": order[0][1],
                     "method": method})

    print("=" * 96)
    print(f"DILUTION PROBE — {len(rows)} cases, "
          f"{len(set(r['page'] for r in rows))} answer pages")
    print("=" * 96)
    print(f"{'id':<7} {'topics':>7} {'depth':<8} {'competitor':>11} {'rank':>5} "
          f"{'score':>8} {'beaten by':>10}")
    print("-" * 96)
    for r in rows:
        beat = "" if r["rank"] == 1 else f"page {r['top']}"
        print(f"{r['id']:<7} {r['topics']:>7} {r['depth']:<8} "
              f"{str(r.get('competitor', '-')):>11} {r['rank']:>5} "
              f"{r['score']:>8.4f} {beat:>10}")

    print("-" * 96)
    print(f"  {'answer page ranked first':<40} "
          f"{sum(1 for r in rows if r['rank'] == 1)}/{len(rows)}")
    print()
    print("  BY DECLARED TOPIC COUNT — the hypothesis under test")
    for t in sorted({r["topics"] for r in rows}):
        sub = [r for r in rows if r["topics"] == t]
        print(f"    {t} topic(s): rank-1 {sum(1 for r in sub if r['rank'] == 1)}"
              f"/{len(sub)}   mean score "
              f"{sum(r['score'] for r in sub) / len(sub):.4f}")
    print("  BY DEPTH OF THE ANSWERING FACT — the control variable")
    for d in ("lead", "middle", "final"):
        sub = [r for r in rows if r["depth"] == d]
        if sub:
            print(f"    {d:<8}: rank-1 {sum(1 for r in sub if r['rank'] == 1)}"
                  f"/{len(sub)}   mean score "
                  f"{sum(r['score'] for r in sub) / len(sub):.4f}")

    lost = [r for r in rows if r["rank"] != 1]
    if lost:
        print()
        print("  LOST RANK 1 — note the declared topic count of each")
        for r in lost:
            print(f"    {r['id']}  answer page {r['page']} "
                  f"({r['topics']} topic(s), fact {r['depth']}) "
                  f"-> rank {r['rank']}, beaten by page {r['top']} "
                  f"@ {r['top_score']:.4f}")
    print("=" * 96)
    return 0


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
            "rank": answer_rank(case, scores),
            "leads": leads_ranking(case, scores),
            "method": method,
            "answer_hit": None,
            "citation": None,
        }

        if with_answers:
            out = chat.safe_execute(SkillInput(data={
                "user_message": case["question"],
                "document_chunks": chunks,
                "conversation_history": [],
                "domain": "General",
            }))
            record["answer_hit"] = answer_hit(case, out.data["reply"]) if out.success else False
            record["citation"] = citation_verdict(case, out.data["reply"]) if out.success else "none"
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
        rc = [r for r in by_cat[category] if r["rank"] > 0]
        if rc:
            leads = sum(1 for r in rc if r["leads"])
            t1 = sum(1 for r in rc if r["rank"] == 1)
            mr = sum(r["rank"] for r in rc) / len(rc)
            print(f"  {'':<34} leads {leads}/{len(rc)}   "
                  f"(worst-rank#1 {t1}/{len(rc)}, mean worst rank {mr:.2f})")

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
    # Rank-based figures. These are the ones that can still move once hit/miss
    # saturates: on the dense fixture every case "hits" because the selector
    # returns 4-5 of 8 pages, while the answering page's RANK varies from 1 to 3.
    ranked_cases = [r for r in meaningful if r["rank"] > 0]
    leads = sum(1 for r in meaningful if r["leads"])
    top1 = sum(1 for r in ranked_cases if r["rank"] == 1)
    mean_rank = sum(r["rank"] for r in ranked_cases) / len(ranked_cases) if ranked_cases else 0.0
    print(f"  {'required sources lead the ranking':<34} "
          f"{leads}/{len(meaningful)}   <- headline, ceiling is reachable")
    print(f"  {'worst required source ranked #1':<34} "
          f"{top1}/{len(ranked_cases)}   (mean worst rank {mean_rank:.2f}; "
          f"capped at {len(meaningful) - sum(1 for r in meaningful if len(r['expected']) > 1)}"
          f"/{len(meaningful)} by multi-source cases)")

    # Citation correctness, from the model's own prose rather than from what
    # the selector chose. 'wrong' is the number that matters: a confidently
    # cited page the answer did not come from looks verifiable and is not.
    cited = [r for r in meaningful if r.get("citation") not in (None, "n/a")]
    if cited:
        ok = sum(1 for r in cited if r["citation"] == "correct")
        wrong = sum(1 for r in cited if r["citation"] == "wrong")
        none = sum(1 for r in cited if r["citation"] == "none")
        print(f"  {'prose citations (page-sourced)':<34} "
              f"correct {ok}/{len(cited)}   WRONG {wrong}   none {none}")

    anchor_slots = sum(len(r["anchors_in_selection"]) for r in meaningful)
    chosen_slots = sum(r["chosen_slots"] for r in meaningful)
    print(f"  {'slot accounting (meaningful)':<34} "
          f"{chosen_slots} chosen + {anchor_slots} anchor = "
          f"{chosen_slots + anchor_slots} total selected")
    print("=" * 104)
    return 0


if __name__ == "__main__":
    sys.exit(main_probe(sys.argv) if "--probe" in sys.argv else
             main_multi(sys.argv) if "--multi" in sys.argv else main(sys.argv))

"""End-to-end verification harness for DocAgent.

Runs the real pipeline against real sample files and the live Groq API.
Nothing is mocked and no error is swallowed — anything that fails, fails loudly.

That claim is enforced, not assumed. The pipeline degrades rather than raising
when the LLM is unavailable, which is right for a user and wrong for a test: a
stage can otherwise report PASS with no LLM involved anywhere. So every run
starts by checking the configured model actually answers, and every stage that
should have produced an LLM summary asserts that it did.

Usage:
    python tests/e2e/e2e.py pdf|excel|audio|youtube|rag
    python tests/e2e/e2e.py all

Requires:
    - GROQ_API_KEYS (or GROQ_API_KEY) in .env or the environment
    - ffmpeg + ffprobe on PATH (youtube stage only)
    - network access (all stages call the Groq API; youtube also hits YouTube)

Exit code is 0 when the stage passes, non-zero otherwise.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

# ── Repo-relative paths so a fresh clone works from any cwd ────────────────────
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SAMPLES = HERE / "samples"

sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

YT_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # 19s, public, has speech

# Windows consoles default to a legacy codepage (cp1252 here), which cannot
# encode much of what an LLM emits — U+2714 check marks, em-dashes, narrow
# no-break spaces. Printing one raised UnicodeEncodeError from inside the
# harness and the logging handler, which surfaced as a FAILED stage that had
# nothing wrong with it. Test output must never be the thing that breaks the
# test, so both streams are forced to UTF-8 before anything writes to them.
for _stream in ("stdout", "stderr"):
    _s = getattr(sys, _stream)
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.INFO, format="%(levelname)-5s %(name)s: %(message)s")
for _noisy in ("httpx", "httpcore", "urllib3", "openai", "PIL", "pdfminer"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# Route DocAgent's own logs through the same setup the app uses.
#
# This is not cosmetic. `import paddle` (pulled in by StructureRecognitionSkill's
# GPU probe) resets the ROOT logger level from INFO to WARNING as a side effect.
# Relying on basicConfig alone means every docagent INFO record emitted after
# that import is silently dropped, because the docagent loggers have no handlers
# of their own and inherit the root level. That hid the stage timings for
# structure_recognition, summarize and structured_extraction entirely.
#
# setup_logging() gives the "docagent" logger its own handlers and sets
# propagate=False, so it is unaffected by what third-party imports do to root.
from utils.logger import setup_logging  # noqa: E402

# Honour DOCAGENT_LOG_LEVEL so per-key rate-limit headroom (logged at DEBUG)
# can be surfaced without editing this file:
#     DOCAGENT_LOG_LEVEL=DEBUG python tests/e2e/e2e.py pdf
setup_logging(level=os.environ.get("DOCAGENT_LOG_LEVEL", "INFO"),
              log_file=None, use_rich=False)

from agents.document_agent import DocumentAgent  # noqa: E402
from core.models import SkillInput  # noqa: E402
from core.skill_registry import SkillRegistry  # noqa: E402
from utils.config import load_config  # noqa: E402
from utils.youtube_errors import (  # noqa: E402
    BLOCKED, explain, is_external_block)

STAGES = ("pdf", "scanned", "excel", "audio", "youtube", "rag",
          "questionnaire", "empty")

# ── What a run's exit code means ────────────────────────────────────────────
# A stage that could not run must report neither PASS nor FAIL.
#
# The preflight below already established half of this: a PASS has to mean the
# thing under test ran, which it learned when a retired model turned every
# stage green while testing nothing. BLOCKED is the other half. FAIL means "the
# code is wrong", and a red run that means "YouTube rate-limited this IP" is a
# red run nobody can read — after a few of those, a real failure gets waved
# through as "probably the bot check again". That is the same disease as a
# false green, arriving from the other side.
#
# So a blocked run is neither. It exits 3, prints BLOCKED, and says in words
# that the stage verified nothing.
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_BLOCKED = 3


def _agent_config() -> dict:
    cfg = load_config().to_dict()
    cfg["summarization"]["summary_length"] = "Standard"
    cfg["summarization"]["summary_tone"] = "Professional"
    return cfg


def show(r) -> None:
    print("\n" + "=" * 70)
    print(f"  file        : {r.file_name}")
    print(f"  file_type   : {r.file_type}")
    print(f"  success     : {r.success}   partial: {r.partial}")
    # `classification_confidence` is P(questionnaire), so a confidently
    # classified normal document prints near 0.00. Printing that alone under
    # the label "conf" is what made it look broken across three sessions.
    from core.models import confidence_in_verdict
    _verdict_conf = confidence_in_verdict(r.classification_confidence, r.doc_type)
    _shown = ("NOT CLASSIFIED" if _verdict_conf is None
              else f"{_verdict_conf:.0%} confident")
    print(f"  doc_type    : {r.doc_type}  ({r.classification_method}, "
          f"{_shown}; p(questionnaire)={r.classification_confidence:.2f})")
    print(f"  domain      : {r.domain}")
    print(f"  words/pages : {r.word_count} / {r.page_count}")
    print(f"  tables      : {len(r.tables)}")
    print(f"  summary via : {r.summary_method}  ({len(r.summary)} chars)")
    print(f"  questions   : {len(r.questions)} ({r.question_extraction_method})")
    print(f"  entities    : {list(r.extracted_entities.keys()) if r.extracted_entities else '{}'}")
    print(f"  total ms    : {r.processing_time_ms:.0f}")
    print(f"  timings     : { {k: round(v) for k, v in r.skill_timings.items()} }")
    if r.errors:
        print(f"  ERRORS      : {r.errors}")
    if r.warnings:
        print(f"  WARNINGS    : {r.warnings}")
    print("-" * 70)
    print("  RAW TEXT (first 300):")
    print("   ", r.raw_text[:300].replace("\n", "\n    "))
    print("-" * 70)
    print("  SUMMARY (first 900):")
    print("   ", (r.summary or "<empty>")[:900].replace("\n", "\n    "))
    print("=" * 70)


# Summary-method PREFIXES that mean the LLM genuinely produced the summary.
# Anything else means the pipeline degraded, and a degraded run must not report
# PASS. Prefixes, not exact names, because SummarizationSkill appends the
# provider: the real values are "llm_single_groq" and "llm_map_reduce_groq".
LLM_SUMMARY_PREFIXES = ("llm_single", "llm_map_reduce")


def preflight() -> None:
    """Fail before any stage runs if the configured model is not reachable.

    This harness existed for three sessions while claiming, in its own
    docstring, that nothing is swallowed. Then Groq deprecated
    llama-3.3-70b-versatile and every call began returning 404
    `model_not_found`. The client treats that like any other LLM failure, so
    each stage fell back to extractive and pdf, scanned and audio all reported
    PASS with no LLM involved anywhere. The harness was green and testing
    almost nothing.

    A model name is the one input here that can be wrong in a way that produces
    plausible output, so it is checked first and by itself, rather than being
    inferred from a stage failing later.
    """
    from utils.llm_client import LLMClient

    cfg = load_config().to_dict()
    model = cfg["groq"].get("model", "<unset>")
    llm = LLMClient.from_config(cfg)

    if not llm.available:
        raise SystemExit(
            "\nPREFLIGHT FAILED: no usable Groq API key."
            "\n  Set GROQ_API_KEY or GROQ_API_KEYS in .env or the environment.\n"
        )

    # max_tokens has to be generous. Reasoning models (the gpt-oss family among
    # them) spend their first tokens on internal reasoning and emit no content
    # at all under a tight budget, so a small probe returns None from a model
    # that is working perfectly. A preflight that fails a live model is worse
    # than none: it teaches you to ignore it.
    reply = llm.chat([{"role": "user", "content": "Reply with the single word: ok"}],
                     max_tokens=256)
    if reply is None:
        raise SystemExit(
            "\nPREFLIGHT FAILED: the configured model did not answer."
            f"\n  model: {model}"
            "\n  The key authenticated, so this is the MODEL, not the key. Groq"
            "\n  retires models on notice, and a retired one returns 404"
            "\n  model_not_found -- which every stage would otherwise absorb as"
            "\n  an ordinary LLM failure and continue extractively."
            "\n  See https://console.groq.com/docs/deprecations, then set"
            "\n  groq.model in configs/default.yaml (or DOCAGENT_GROQ_MODEL).\n"
        )

    print(f"  preflight   : {model} responded")


def check_llm_ran(result, stage: str) -> bool:
    """True if this stage's summary really came from the LLM.

    `result.success` stays True through a fallback, by design — a user is
    better served by an extractive summary than an error. That makes it the
    wrong thing for a test to assert on, which is precisely how three stages
    stayed green against a model that no longer existed.
    """
    method = result.summary_method

    if method.startswith(LLM_SUMMARY_PREFIXES):
        return True

    if method == "skipped":
        # The planner's call, not a failure: short documents legitimately skip
        # summarisation. Surfaced so a fixture that quietly shrinks past the
        # threshold cannot turn this stage into a no-op unnoticed.
        print(f"  NOTE: [{stage}] the planner skipped summarisation for this "
              f"fixture, so no LLM summary was expected.")
        return True

    print(f"  FAIL: [{stage}] summary came from {method!r}, not the LLM.")
    print("        The pipeline degraded to its fallback and still reported "
          "success.")
    print("        Check the log above for the underlying LLM error.")
    return False


def _require(path: Path) -> Path:
    if not path.exists():
        raise SystemExit(
            f"Missing sample: {path}\nRegenerate with:  python tests/e2e/make_samples.py"
        )
    return path


def _blocked_reason(result) -> str:
    """The reported text if this failure is an external block, else "".

    NARROW ON PURPOSE, in three ways, because the cost of a false BLOCKED is a
    silently skipped regression:

      1. Only a DOWNLOAD failure qualifies. If the download succeeded and
         transcription, classification or summarisation then failed, that is
         our code and stays a FAIL whatever the message says.
      2. Only signatures on the allowlist in utils/youtube_errors qualify.
         Unrecognised text classifies as UNKNOWN, which is a FAIL.
      3. A removed or private video is NOT a block. It is outside the repo, but
         it means this fixture URL needs replacing, which is work for this
         project and must stay red.
    """
    errors = " ".join(str(e) for e in (result.errors or []))
    if "extraction failed" not in errors.lower():
        return ""            # got past the download; any failure after is ours
    if not is_external_block(errors):
        return ""
    # Report what YOUTUBE said, not the explanation this project wrapped around
    # it. The full message contains both, and echoing our own prose back under
    # the label "Reported by YouTube" is noise pretending to be evidence.
    marker = "(underlying error:"
    if marker in errors:
        underlying = errors.split(marker, 1)[1].strip()
        return underlying[:-1].strip() if underlying.endswith(")") else underlying
    return errors.strip()


def run_stage(stage: str) -> int:
    agent = DocumentAgent(config=_agent_config())
    t0 = time.monotonic()

    if stage in ("pdf", "excel", "audio"):
        sample = {
            "pdf": "sample_report.pdf",
            "excel": "sample_sales.xlsx",
            "audio": "sample_audio.wav",
        }[stage]
        result = agent.run(_require(SAMPLES / sample))
        show(result)
        ok = result.success and check_llm_ran(result, stage)

    elif stage == "scanned":
        # Image-only PDF with no text layer: pdfplumber and PyMuPDF both come
        # back empty, so PDFReaderSkill must escalate to the Tesseract tier.
        result = agent.run(_require(SAMPLES / "sample_scanned.pdf"))
        show(result)
        engine = result.metadata.get("engine")
        ocr_flag = result.metadata.get("ocr")
        print(f"  OCR CHECK   : engine={engine!r} ocr={ocr_flag!r} words={result.word_count}")

        # Assert the OCR tier genuinely ran. Without this the stage would still
        # pass if the fixture were ever regenerated with a text layer, which
        # would silently stop testing OCR at all.
        ok = result.success and check_llm_ran(result, stage)
        if engine != "ocr_tesseract" or not ocr_flag:
            print(f"  FAIL: expected the OCR tier to run, got engine={engine!r}. "
                  f"Is tesseract on PATH? Does sample_scanned.pdf still lack a text layer?")
            ok = False
        elif result.word_count < 100:
            print(f"  FAIL: OCR returned only {result.word_count} words; expected >= 100.")
            ok = False

    elif stage == "questionnaire":
        # The only fixture that exercises LLM classification END TO END.
        #
        # Every other document here is a normal_document, so the questionnaire
        # branch of the pipeline — the `0.7 * llm_score + 0.3 * heuristic` blend
        # and the `question_extraction` stage it gates — had no coverage at all.
        # That gap is why an 80-token classifier budget could disable LLM
        # classification across a model migration without a number moving.
        #
        # The fixture is written so the HEURISTIC does not recognise it: it
        # scores 0.000, below the 0.4 threshold. Only the LLM term carries it
        # over. So if the LLM result is ever discarded again — a budget too
        # small, a parse change, a model swap — this stage fails, and it fails
        # on the thing a user would actually notice: no questions extracted.
        result = agent.run(_require(SAMPLES / "sample_questionnaire.pdf"))
        show(result)
        ok = result.success and check_llm_ran(result, stage)

        print(f"  CLASSIFY    : doc_type={result.doc_type!r} "
              f"method={result.classification_method!r} "
              f"conf={result.classification_confidence:.3f} "
              f"domain={result.domain!r}")
        print(f"  QUESTIONS   : {len(result.questions)} "
              f"({result.question_extraction_method})")

        if result.doc_type != "questionnaire":
            print(f"  FAIL: [{stage}] classified as {result.doc_type!r}.\n"
                  f"        This fixture scores 0.000 on heuristics by design, so\n"
                  f"        only the LLM blend can classify it. Getting\n"
                  f"        'normal_document' means the LLM result was DISCARDED —\n"
                  f"        check the log for a truncation or parse warning.")
            ok = False
        elif not result.classification_method.startswith("hybrid_"):
            print(f"  FAIL: [{stage}] method is {result.classification_method!r}, "
                  f"not hybrid_*.\n"
                  f"        The heuristic cannot reach this verdict alone, so a\n"
                  f"        non-hybrid method means the blend did not run.")
            ok = False
        elif not result.questions:
            print(f"  FAIL: [{stage}] classified as a questionnaire but extracted "
                  f"no questions.\n"
                  f"        The planner gates question_extraction on doc_type, so "
                  f"this is\n        the extraction step failing, not the "
                  f"classifier.")
            ok = False

    elif stage == "empty":
        # A document with no content must produce NO VERDICT.
        #
        # This is the shape the project keeps rediscovering: a stage with no
        # input still emitting a confident-looking answer. Here a failed
        # YouTube download rendered as "Normal Document, Domain: General, 100%
        # confidence" in green, over zero words. The arithmetic was right --
        # empty text scores 0 on the heuristic, the LLM never runs -- and
        # nothing asked whether there had been anything to classify.
        result = agent.run(_require(SAMPLES / "sample_blank.pdf"))
        show(result)

        from core.models import confidence_in_verdict
        verdict_conf = confidence_in_verdict(
            result.classification_confidence, result.doc_type)
        print(f"  EMPTY CHECK : success={result.success} "
              f"words={result.word_count} doc_type={result.doc_type!r} "
              f"verdict_confidence={verdict_conf!r}")

        ok = True
        if result.word_count != 0:
            print(f"  FAIL: [{stage}] the fixture parsed {result.word_count} "
                  f"words; it is supposed to have none. Has it been "
                  f"regenerated with content?")
            ok = False
        if result.doc_type in ("questionnaire", "normal_document"):
            print(f"  FAIL: [{stage}] classified as {result.doc_type!r} with no "
                  f"text to classify. A stage with no input must not produce a "
                  f"verdict.")
            ok = False
        if verdict_conf is not None:
            print(f"  FAIL: [{stage}] reports {verdict_conf:.0%} confidence in a "
                  f"verdict it never reached. This is the original bug: "
                  f"confidence_in_verdict must return None when doc_type is "
                  f"not a real class.")
            ok = False
        if result.success:
            print(f"  FAIL: [{stage}] reported success on a document with no "
                  f"extractable text.")
            ok = False

    elif stage == "youtube":
        result = agent.run_youtube(YT_URL)
        show(result)
        if not result.success:
            blocked = _blocked_reason(result)
            if blocked:
                print()
                print(f"  BLOCKED: [{stage}] YouTube refused this machine, "
                      f"so the stage did not run.")
                print(f"           {explain(BLOCKED)}")
                print(f"           Reported by YouTube: {blocked}")
                print( "           NOTHING WAS VERIFIED. This is not a pass:")
                print( "           the YouTube path is untested on this run, "
                       "and a regression in it would look identical.")
                return EXIT_BLOCKED
        ok = result.success and check_llm_ran(result, stage)

    elif stage == "rag":
        result = agent.run(_require(SAMPLES / "sample_report.pdf"))
        if not result.success:
            print("PDF ingest failed, cannot run RAG:", result.errors)
            return 1
        print(f"Ingested {result.file_name}: {result.word_count} words, "
              f"{len(result.parsed_document.chunks)} chunks")

        registry = SkillRegistry()
        registry.discover()
        chat = registry.instantiate("document_chat", config={"groq": _agent_config()["groq"]})

        chunks = [{"text": c.text, "page_or_sheet": c.page_or_sheet}
                  for c in result.parsed_document.chunks]

        history: list = []
        ok = True
        for question in ("What was the MTBF improvement between Q2 and Q3, and what caused it?",
                         "What is the main risk for Q4?"):
            print("\n" + "=" * 70)
            print("  Q:", question)
            out = chat.safe_execute(SkillInput(data={
                "user_message": question,
                "document_chunks": chunks,
                "conversation_history": history,
                "domain": result.domain,
            }))
            if not out.success:
                print("  RAG FAILED:", out.error)
                return 1
            print("  A:", out.data["reply"][:1200])
            print("  pages used:", out.data["used_pages"])
            history.append({"role": "user", "content": question})
            history.append({"role": "assistant", "content": out.data["reply"]})
        print("=" * 70)

    else:
        print(__doc__)
        return EXIT_USAGE

    print(f"\n[{stage}] wall clock: {time.monotonic() - t0:.1f}s")
    return EXIT_PASS if ok else EXIT_FAIL


def main(argv: list) -> int:
    stage = argv[1] if len(argv) > 1 else ""

    if stage != "all" and stage not in STAGES:
        print(__doc__)
        return EXIT_USAGE

    # Before anything else: a PASS has to mean the thing under test ran.
    preflight()

    if stage == "all":
        results = {}
        for s in STAGES:
            print(f"\n########## STAGE: {s} ##########")
            results[s] = run_stage(s)
        print("\n" + "#" * 70)
        labels = {EXIT_PASS: "PASS", EXIT_BLOCKED: "BLOCKED"}
        for s, code in results.items():
            print(f"  {s:9s} {labels.get(code, 'FAIL')}")

        # A genuine failure outranks a block: if anything is actually broken the
        # run is red and the block is a footnote. Only when nothing failed does
        # BLOCKED decide the code — and it is still not 0, because part of the
        # suite did not run, and reporting that as success is the false green
        # this harness exists to prevent.
        blocked = [k for k, c in results.items() if c == EXIT_BLOCKED]
        failed = [k for k, c in results.items()
                  if c not in (EXIT_PASS, EXIT_BLOCKED)]
        if blocked:
            print()
            print(f"  {len(blocked)} stage(s) BLOCKED externally and did not "
                  f"run: {', '.join(blocked)}")
            print( "  These verified nothing. Re-run from a different network "
                   "to cover them.")
        print("#" * 70)
        if failed:
            return EXIT_FAIL
        return EXIT_BLOCKED if blocked else EXIT_PASS

    return run_stage(stage)


if __name__ == "__main__":
    sys.exit(main(sys.argv))

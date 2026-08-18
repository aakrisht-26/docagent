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

STAGES = ("pdf", "scanned", "excel", "audio", "youtube", "rag", "questionnaire")


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
    print(f"  doc_type    : {r.doc_type}  ({r.classification_method}, conf {r.classification_confidence:.2f})")
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

    elif stage == "youtube":
        result = agent.run_youtube(YT_URL)
        show(result)
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
        return 2

    print(f"\n[{stage}] wall clock: {time.monotonic() - t0:.1f}s")
    return 0 if ok else 1


def main(argv: list) -> int:
    stage = argv[1] if len(argv) > 1 else ""

    if stage != "all" and stage not in STAGES:
        print(__doc__)
        return 2

    # Before anything else: a PASS has to mean the thing under test ran.
    preflight()

    if stage == "all":
        results = {}
        for s in STAGES:
            print(f"\n########## STAGE: {s} ##########")
            results[s] = run_stage(s)
        print("\n" + "#" * 70)
        for s, code in results.items():
            print(f"  {s:9s} {'PASS' if code == 0 else 'FAIL'}")
        print("#" * 70)
        return 0 if all(c == 0 for c in results.values()) else 1

    return run_stage(stage)


if __name__ == "__main__":
    sys.exit(main(sys.argv))

"""End-to-end verification harness for DocAgent.

Runs the real pipeline against real sample files and the live Groq API.
Nothing is mocked and no error is swallowed — anything that fails, fails loudly.

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

STAGES = ("pdf", "scanned", "excel", "audio", "youtube", "rag")


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
        ok = result.success

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
        ok = result.success
        if engine != "ocr_tesseract" or not ocr_flag:
            print(f"  FAIL: expected the OCR tier to run, got engine={engine!r}. "
                  f"Is tesseract on PATH? Does sample_scanned.pdf still lack a text layer?")
            ok = False
        elif result.word_count < 100:
            print(f"  FAIL: OCR returned only {result.word_count} words; expected >= 100.")
            ok = False

    elif stage == "youtube":
        result = agent.run_youtube(YT_URL)
        show(result)
        ok = result.success

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

    if stage not in STAGES:
        print(__doc__)
        return 2

    return run_stage(stage)


if __name__ == "__main__":
    sys.exit(main(sys.argv))

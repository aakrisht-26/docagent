"""Record repeated summaries of every fixture, raw text kept.

    python tests/e2e/summary_eval/run_trials.py --rounds 5 --out new_trials.jsonl [--only a,b]

Each fixture is parsed, cleaned and classified ONCE, the way DocumentAgent does,
then summarised N times with the in-process LLM cache OFF, round-robin across
fixtures so a slow or throttled minute does not land on one document. Settings
are the e2e harness's: Standard length, Professional tone.

Fixtures: the nine e2e samples that produce a summary, and the eight documents
of the extraction eval (text only, so each is wrapped as a one-page document and
gets the same citation instruction a parsed file does).

`--out` APPENDS, and the recorded `trials.jsonl` is what RESULTS.md and
tests/test_summary_eval.py are pinned to, so write new runs to a new file.
The sources are written beside it as `<out>.sources.json`.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from agents.document_agent import SUPPORTED_EXTENSIONS, DocumentAgent  # noqa: E402
from core.models import DocumentChunk, ParsedDocument, SkillInput  # noqa: E402
from utils.config import load_config  # noqa: E402

SAMPLES = ROOT / "tests" / "e2e" / "samples"
E2E = ["sample_report.pdf", "sample_scanned.pdf", "sample_questionnaire.pdf",
       "sample_sales.xlsx", "sample_large_sales.xlsx", "sample_mixed_topics.pdf",
       "sample_dense_manual.pdf", "sample_large_report.pdf", "sample_audio.wav"]


def _eval_documents():
    folder = ROOT / "tests" / "e2e" / "extraction_eval"
    spec = importlib.util.spec_from_file_location("_trial_fixture_content", folder / "fixture_content.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cases = json.loads((folder / "eval_set.json").read_text(encoding="utf-8"))["cases"]
    return module.DOCUMENTS, {c["fixture"]: c["domain"] for c in cases}


def prepare(agent, only):
    items = []
    readers = {"pdf": agent._pdf_reader, "audio": agent._audio_reader, "excel": agent._xls_reader}
    for name in E2E:
        if only and name not in only:
            continue
        path = SAMPLES / name
        parsed = readers[SUPPORTED_EXTENSIONS[path.suffix.lower()]].safe_execute(
            SkillInput(data={"file_path": str(path)})).data
        cleaned = agent._cleaner.safe_execute(SkillInput(data={"parsed_document": parsed}))
        if cleaned.success and cleaned.data:
            parsed = cleaned.data
        verdict = agent._classifier.safe_execute(SkillInput(data={"full_text": parsed.full_text})).data
        items.append({"fixture": name, "kind": "e2e", "parsed": parsed,
                      "doc_type": verdict.doc_type if verdict else "normal_document",
                      "domain": verdict.domain if verdict else "General"})
    documents, domains = _eval_documents()
    for key, text in documents.items():
        if only and key not in only:
            continue
        parsed = ParsedDocument(file_name=key, file_type="text",
                                chunks=[DocumentChunk(text=text, page_or_sheet=1, chunk_index=0)],
                                full_text=text, tables=[], metadata={}, page_count=1)
        items.append({"fixture": key, "kind": "eval", "parsed": parsed,
                      "doc_type": "normal_document", "domain": domains.get(key, "General")})
    return items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    only = {s.strip() for s in args.only.split(",") if s.strip()}

    agent = DocumentAgent(config=load_config().to_dict())
    summariser = agent._summarizer
    summariser._llm._cache = None
    items = prepare(agent, only)

    sources_path = Path(args.out).with_suffix(".sources.json")
    sources = json.loads(sources_path.read_text(encoding="utf-8")) if sources_path.exists() else {}
    for it in items:
        sources[it["fixture"]] = {"full_text": it["parsed"].full_text, "kind": it["kind"],
                                  "domain": it["domain"], "doc_type": it["doc_type"]}
    sources_path.write_text(json.dumps(sources, ensure_ascii=False, indent=1), encoding="utf-8")
    print("prepared:", [(it["fixture"], it["domain"], len(it["parsed"].full_text)) for it in items], flush=True)

    with open(args.out, "a", encoding="utf-8") as fh:
        for rnd in range(args.rounds):
            for it in items:
                started = time.monotonic()
                out = summariser.safe_execute(SkillInput(data={
                    "full_text": it["parsed"].full_text, "doc_type": it["doc_type"],
                    "domain": it["domain"], "parsed_document": it["parsed"],
                    "summary_length": "Standard", "summary_tone": "Professional"}))
                data = out.data or {}
                record = {"fixture": it["fixture"], "kind": it["kind"], "round": rnd,
                          "domain": it["domain"], "method": data.get("method"),
                          "summary": data.get("summary", ""), "warnings": out.warnings,
                          "elapsed_s": round(time.monotonic() - started, 1)}
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                fh.flush()
                print(f"round {rnd} {it['fixture']:26} {record['method']!s:24} "
                      f"{len(record['summary']):6} chars {record['elapsed_s']:5}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

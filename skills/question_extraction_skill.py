"""
QuestionExtractionSkill -- extracts all questions from questionnaire/form documents.

Uses unified LLMClient (Grok API first, Ollama fallback).
LLM runs alongside regex on every questionnaire document (llm_threshold=0 default).
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from core.models import SkillInput, SkillOutput
from skills.base_skill import BaseSkill
from utils.logger import get_logger

logger = get_logger(__name__)


# ─── Regex pattern bank ───────────────────────────────────────────────────────

_P_ENDS_QUESTION = re.compile(
    r"(?:^|\n)[ \t]*(?:\d+[\.\)][ \t]*)?([A-Z][^\n]{8,200}\?)",
    re.MULTILINE,
)
_P_Q_PREFIX = re.compile(
    r"^[ \t]*(?:q(?:uestion)?[\s\.\-]*(?:\d+|[ivxlc]+)[\.\)\:\-]?\s*)(.{5,200})$",
    re.IGNORECASE | re.MULTILINE,
)
_P_NUMBERED_INSTRUCTION = re.compile(
    r"^[ \t]*(?:\d{1,3}[\.\)]\s+)([A-Z][^\n]{10,200}"
    r"(?:select|choose|rate|rank|describe|explain|list|provide|indicate|specify|"
    r"identify|state|outline|comment|assess|evaluate|mention|complete|fill|write"
    r"|do you|would you|have you|are you|can you|will you|should|did you)[^\n]{3,})",
    re.IGNORECASE | re.MULTILINE,
)
_P_PLEASE = re.compile(
    r"^[ \t]*(?:please\s+(?:select|choose|rate|rank|describe|explain|list|provide|"
    r"indicate|specify|identify|state|outline|comment|assess|fill\s+in|write|"
    r"check|tick|mark|circle).{5,200})$",
    re.IGNORECASE | re.MULTILINE,
)
_P_FIELD_LABEL = re.compile(
    r"^[ \t]*([A-Z][A-Za-z\s/\(\)]{2,60}?)\s*(?:[\*\:])\s*(?:_{3,}|\[[\s_]+\]|\(\s*\)|required|optional|____)?[ \t]*$",
    re.MULTILINE,
)
_P_LIKERT = re.compile(
    r"^[ \t]*(.{10,200}?)\s*\n[ \t]*(?:strongly\s+agree|very\s+satisfied|always|never|1\s*[-–]\s*5)",
    re.IGNORECASE | re.MULTILINE,
)
_P_RATING = re.compile(
    r"^[ \t]*((?:rate|on\s+a\s+scale|how\s+(?:likely|often|satisfied|well|much|many|would)\b)[^\n]{5,200})$",
    re.IGNORECASE | re.MULTILINE,
)
_P_YES_NO = re.compile(
    r"^[ \t]*(\d+[\.\)]\s*)?([A-Z][^\n]{8,150}?)[\s\n]+(?:\(\s*Yes\s*\)|\bYes\b).*(?:\(\s*No\s*\)|\bNo\b)",
    re.IGNORECASE | re.MULTILINE,
)
_P_SECTION_Q = re.compile(
    r"^[ \t]*\*{1,2}([A-Z][^\n\*]{5,80}\?)\*{0,2}[ \t]*$",
    re.MULTILINE,
)
_P_ANSWER_OPTION = re.compile(
    r"^[ \t]*(?:[a-eA-E][\.\)]\s|[①②③④⑤]|\(\s*[a-eA-E]\s*\)|"
    r"(?:strongly\s+agree|strongly\s+disagree|agree|disagree|neutral|"
    r"very\s+(?:satisfied|dissatisfied)|not\s+applicable|n/?a|"
    r"always|usually|sometimes|rarely|never|"
    r"yes|no|true|false|other)\s*$)",
    re.IGNORECASE | re.MULTILINE,
)

_ALL_PATTERNS: List[Tuple[re.Pattern, int]] = [
    (_P_ENDS_QUESTION,        1),
    (_P_Q_PREFIX,             1),
    (_P_NUMBERED_INSTRUCTION, 1),
    (_P_PLEASE,               0),
    (_P_RATING,               1),
    (_P_YES_NO,               2),
    (_P_SECTION_Q,            1),
]


class QuestionExtractionSkill(BaseSkill):
    """
    Extracts, cleans, and deduplicates questions from questionnaire documents.

    Config keys:
        dedup           (bool) : Near-duplicate removal (default: True)
        max_questions   (int)  : Hard cap (default: 200)
        llm_threshold   (int)  : LLM runs when regex finds <= N questions (default: 0 = always)
        -- LLM provider settings are read via LLMClient.from_config() --
    """

    name = "question_extraction"
    description = "Extracts and deduplicates all questions from form/questionnaire documents."
    required_inputs = ["full_text"]

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        self._dedup         = self.get_config("dedup", True)
        self._max_q         = self.get_config("max_questions", 200)
        self._min_len       = 4
        self._llm_threshold = self.get_config("llm_threshold", 0)
        # Max windows sent to the LLM — prevents silent truncation on large docs.
        # Increase in config if questions are missing from long questionnaires.
        self._max_windows   = self.get_config("max_windows", 20)

        from utils.llm_client import LLMClient
        self._llm = LLMClient.from_config(self.config)

    # ── Entry point ────────────────────────────────────────────────────────────

    def execute(self, inputs: SkillInput) -> SkillOutput:
        start    = time.monotonic()
        full_text: str = inputs.data["full_text"]
        doc_type: str  = inputs.data.get("doc_type", "normal_document")
        domain: str    = inputs.data.get("domain", "General")
        force: bool    = inputs.data.get("force", False)

        if doc_type != "questionnaire" and not force:
            return SkillOutput(
                success=True,
                data={"questions": [], "method": "skipped",
                      "reason": "Not a questionnaire."},
                duration_ms=(time.monotonic() - start) * 1000,
            )

        # ── Layer 1: Regex ─────────────────────────────────────────────────
        regex_q = self._regex_extract(full_text)
        self.logger.info(f"Regex: {len(regex_q)} candidates")

        # ── Layer 2: LLM ───────────────────────────────────────────────────
        llm_q: List[str] = []
        skill_warnings: List[str] = []
        if self._llm.available:
            llm_q, llm_warnings = self._llm_extract(full_text, domain)
            skill_warnings.extend(llm_warnings)
            self.logger.info(
                f"LLM ({self._llm.provider_label}): {len(llm_q)} candidates"
            )

        if llm_q:
            combined = llm_q + regex_q
            method   = f"llm_{self._llm.provider}" if not regex_q else f"hybrid_{self._llm.provider}"
        else:
            combined = regex_q
            method   = "regex"

        # ── Layer 3: Post-process ──────────────────────────────────────────
        combined = self._remove_answer_options(combined)
        combined = self._clean_questions(combined)
        if self._dedup:
            combined = self._deduplicate(combined)
        combined = combined[: self._max_q]

        self.logger.info(f"Final: {len(combined)} questions (method={method})")
        return SkillOutput(
            success=True,
            data={"questions": combined, "method": method},
            warnings=skill_warnings,
            duration_ms=(time.monotonic() - start) * 1000,
        )

    # ── Regex extraction ───────────────────────────────────────────────────────

    def _regex_extract(self, text: str) -> List[str]:
        found: List[str] = []
        for pattern, group in _ALL_PATTERNS:
            for m in pattern.finditer(text):
                try:
                    q = m.group(group).strip() if group > 0 else m.group(0).strip()
                except IndexError:
                    q = m.group(0).strip()
                if len(q) >= self._min_len:
                    found.append(q)

        for m in _P_FIELD_LABEL.finditer(text):
            label = m.group(1).strip()
            if len(label) >= self._min_len and not _P_ANSWER_OPTION.match(label):
                found.append(label)

        for m in _P_LIKERT.finditer(text):
            q = m.group(1).strip()
            if len(q) >= self._min_len:
                found.append(q)

        found.extend(self._multiline_reconstruct(text))
        return found

    def _multiline_reconstruct(self, text: str) -> List[str]:
        reconstructed: List[str] = []
        lines = text.split("\n")
        for i in range(len(lines) - 1):
            line     = lines[i].strip()
            nextline = lines[i + 1].strip()
            if (
                line and nextline
                and re.match(r"^[A-Z]", line)
                and not re.search(r"[.?!:,;]$", line)
                and nextline.endswith("?")
                and len(line) > 10
                and len(line + nextline) < 250
            ):
                reconstructed.append(line + " " + nextline)
        return reconstructed

    # ── LLM extraction ─────────────────────────────────────────────────────────

    def _llm_extract(self, full_text: str, domain: str) -> Tuple[List[str], List[str]]:
        windows          = self._split_into_windows(full_text, window=5000, overlap=200)
        warnings: List[str] = []
        total_windows    = len(windows)
        if total_windows > self._max_windows:
            warnings.append(
                f"Document has {total_windows} text windows but only the first "
                f"{self._max_windows} were sent to the LLM for question extraction. "
                f"Questions from the remaining {total_windows - self._max_windows} window(s) "
                f"may be missing. Increase 'max_windows' in the question_extraction config "
                f"to process the full document."
            )
            self.logger.warning(
                f"Question extraction truncated: {total_windows} windows → {self._max_windows} "
                f"(set max_windows in config to process more)"
            )
        windows = windows[:self._max_windows]

        all_questions: List[str] = []
        for idx, window in enumerate(windows):
            self.logger.debug(f"  LLM window {idx + 1}/{len(windows)}")
            questions = self._llm_window_extract(window, domain)
            all_questions.extend(questions)
        return all_questions, warnings

    def _llm_window_extract(self, text: str, domain: str) -> List[str]:
        system_msg = (
            "You are an expert Document Analyst tasked with extracting questions and actionable inputs. "
            "You STRICTLY return valid JSON matching this schema: {\"questions\": [\"Q1\", \"Q2\"]} "
            "You do NOT include answer choices, general instructions, or section headings."
        )
        few_shot = (
            f"The document domain is strictly detected as: '{domain}'.\n"
            "Analyze the nature of the document contextually based on this domain:\n"
            "- If it implies an Academic Exam/Test/Assessment: Extract ONLY the test questions (e.g. '1. Describe the algorithm'). "
            "DO NOT extract data table headers (e.g. 'Item1', 'Reg Number') or matrix variables (e.g. 'Time (x)').\n"
            "- If it implies a Form/Survey: Extract the form inputs or survey items (e.g. 'Email', 'First Name', 'How satisfied are you').\n\n"
        )
        content = self._llm.chat(
            messages=[
                {"role": "system", "content": system_msg},
                {
                    "role": "user",
                    "content": (
                        f"{few_shot}"
                        "Extract ALL actionable questions, academic problems, survey items, or form fields from the document below.\n"
                        'Return EXACTLY a JSON object with a single "questions" key containing a list of strings. If none found, return {"questions": []}.\n\n'
                        f"Document:\n{text}\n\n"
                        "JSON Output:"
                    ),
                },
            ],
            temperature=0.0,
            max_tokens=2000,
        )
        if not content:
            return []

        try:
            json_match = re.search(r"\{.*?\"questions\".*?\}", content, re.DOTALL)
            if json_match:
                obj = json.loads(json_match.group())
                candidates = obj.get("questions", [])
                return [str(q).strip() for q in candidates
                        if len(str(q).strip()) >= self._min_len]
        except Exception as exc:
            self.logger.debug(f"LLM window JSON parse error: {exc}")

        return [
            line.strip()
            for line in content.split("\n")
            if line.strip() and len(line.strip()) >= self._min_len
            and not line.strip().startswith("[") and not line.strip().startswith("{")
        ]

    # ── Post-processing ────────────────────────────────────────────────────────

    def _remove_answer_options(self, questions: List[str]) -> List[str]:
        return [
            q for q in questions
            if not _P_ANSWER_OPTION.match(q.strip())
        ]

    def _clean_questions(self, questions: List[str]) -> List[str]:
        cleaned: List[str] = []
        for q in questions:
            q = re.sub(r"^[ \t]*(?:\d{1,3}[\.\)\-]|[a-eA-E][\.\)])\s*", "", q).strip()
            q = re.sub(r"^(?:q\.?\s*\d*\.?|question\s*\d*\.?)\s*[:\-]?\s*",
                       "", q, flags=re.IGNORECASE).strip()
            q = re.sub(r"\*{1,2}([^\*]+)\*{1,2}", r"\1", q).strip()
            q = re.sub(r"\s+\(see\s+\w+[\s\w]*\)$", "", q, flags=re.IGNORECASE).strip()
            q = re.sub(r"\s*[\[\(]?\s*required\s*[\]\)]?\s*$", "", q, flags=re.IGNORECASE).strip()
            if len(q) < self._min_len:
                continue
            q = q[0].upper() + q[1:] if len(q) > 1 else q.upper()
            cleaned.append(q)
        return cleaned

    def _deduplicate(self, questions: List[str]) -> List[str]:
        seen_norms: List[str] = []
        unique: List[str]     = []
        for q in questions:
            norm  = re.sub(r"\s+", " ", q.lower().strip().rstrip("?. "))
            q_toks = set(norm.split())

            best_idx = -1
            best_j   = 0.0
            for i, pn in enumerate(seen_norms):
                pt = set(pn.split())
                u  = q_toks | pt
                if not u:
                    continue
                j = len(q_toks & pt) / len(u)
                if j > best_j:
                    best_j, best_idx = j, i

            if best_j >= 0.72 and best_idx >= 0:
                if len(q) > len(unique[best_idx]):
                    unique[best_idx]     = q
                    seen_norms[best_idx] = norm
            else:
                seen_norms.append(norm)
                unique.append(q)
        return unique

    @staticmethod
    def _split_into_windows(text: str, window: int, overlap: int) -> List[str]:
        if len(text) <= window:
            return [text]
        windows: List[str] = []
        start = 0
        while start < len(text):
            end = min(start + window, len(text))
            windows.append(text[start:end])
            if end >= len(text):
                break
            start = end - overlap
        return windows

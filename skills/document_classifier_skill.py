"""
DocumentClassifierSkill — classifies a document as 'questionnaire' or 'normal_document'.

Classification strategy (layered):
    1. Fast heuristic scan — pattern matching on ~20 signals (questionnaire keywords,
       structural markers, rating scales, form fields, Q-numbering)
    2. LLM disambiguation — when heuristic score is in the borderline range (0.10–0.70),
       the LLM provides a second opinion; results are blended (60% LLM / 40% heuristic)

Output:
    ClassificationResult with doc_type, confidence (0–1), method, and debug signals.

Signal weights are intentionally readable and tunable — see _Q_SIGNALS below.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from core.models import ClassificationResult, SkillInput, SkillOutput
from skills.base_skill import BaseSkill
from utils.logger import get_logger

logger = get_logger(__name__)


# ── Heuristic signal table ────────────────────────────────────────────────────
# Each entry: (compiled_pattern, weight, label)
# Weights are additive; threshold in config determines final classification.

_Q_SIGNALS: List[Tuple[re.Pattern, float, str]] = [
    # Strong explicit signals
    (re.compile(r"^\s*(?:q\s*\.?\s*\d+|question\s+\d+)[:\.\)\s]",
                re.IGNORECASE | re.MULTILINE),                   0.15, "q_numbering"),
    (re.compile(r"^\s*\d+[\.\)]\s+[A-Z].{5,}\?", re.MULTILINE), 0.12, "numbered_question"),
    (re.compile(r"(?:survey|questionnaire|feedback\s+form|"
                r"assessment\s+form|quiz|evaluation\s+form|"
                r"intake\s+form|registration\s+form|application\s+form)",
                re.IGNORECASE),                                   0.20, "form_title"),
    # Likert & rating
    (re.compile(r"(?:strongly\s+(?:agree|disagree)|neutral|"
                r"somewhat\s+agree|very\s+(?:satisfied|dissatisfied))",
                re.IGNORECASE),                                   0.15, "likert_scale"),
    (re.compile(r"(?:on\s+a\s+scale\s+of|rate\s+(?:from|on|your)|rating\s*:)",
                re.IGNORECASE),                                   0.12, "rating_scale"),
    (re.compile(r"(?:net\s+promoter|nps|how\s+likely\s+(?:are\s+you|would\s+you))",
                re.IGNORECASE),                                   0.10, "nps_score"),
    # Multiple choice
    (re.compile(r"(?:check\s+all\s+that\s+apply|select\s+(?:one|all|your)|"
                r"circle\s+(?:one|your)|tick\s+(?:one|all))",
                re.IGNORECASE),                                   0.12, "multi_choice"),
    (re.compile(r"^\s*(?:\([a-eA-E]\)|[a-eA-E][\.\)]\s)",
                re.MULTILINE),                                    0.10, "abcd_options"),
    # Form fields and instructions
    (re.compile(r"_{5,}|\[[\s_]{3,}\]|\(\s{3,}\)",
                re.MULTILINE),                                    0.08, "fill_in_blank"),
    (re.compile(r"(?:please\s+(?:select|choose|rate|indicate|"
                r"rank|circle|check|tick|describe|complete|fill))",
                re.IGNORECASE),                                   0.10, "form_instruction"),
    (re.compile(r"(?:first\s+name|last\s+name|date\s+of\s+birth|"
                r"d[.]?o[.]?b[.]?|email\s*:?\s*_|phone\s*:?\s*_|"
                r"signature\s*:?\s*_|\baddress\s*:?\s*_)",
                re.IGNORECASE),                                   0.08, "personal_fields"),
    (re.compile(r"(?:optional|required|mandatory)\s*[:\*]",
                re.IGNORECASE),                                   0.06, "field_annotation"),
    (re.compile(r"(?:\byes\b.{0,10}\bno\b|\bno\b.{0,10}\byes\b)",
                re.IGNORECASE),                                   0.06, "yes_no_pair"),
    # Consent and declaration blocks (common in forms)
    (re.compile(r"(?:i\s+consent|i\s+agree|i\s+confirm|i\s+declare|i\s+certify)",
                re.IGNORECASE),                                   0.06, "consent_block"),
    # Date / signature fields
    (re.compile(r"(?:date\s*[:\-]\s*_{3,}|signature\s*[:\-]\s*_{3,}|"
                r"\bdd[\/\-]mm[\/\-](?:yyyy|yy)\b)",
                re.IGNORECASE),                                   0.07, "date_signature"),
    # Instructions section
    (re.compile(r"(?:instructions?\s*[:\-]|directions?\s*[:\-]|"
                r"how\s+to\s+(?:complete|fill|answer))",
                re.IGNORECASE),                                   0.06, "instructions"),
    # Checkbox markers
    (re.compile(r"(?:\[\s*\]|\[\s*x\s*\]|\u2610|\u2611|\u2612|\u25a1|\u25a0)",
                re.IGNORECASE),                                   0.07, "checkbox"),
    # Open-ended response cues
    (re.compile(r"(?:comments?\s*:?\s*$|feedback\s*:?\s*$|additional\s+(?:comments?|feedback))",
                re.IGNORECASE | re.MULTILINE),                    0.05, "open_ended_prompt"),
    # Numbered paragraph with answer verb (describe, explain, provide)
    (re.compile(r"^\d+\.\s+(?:describe|explain|provide|state|list|outline)",
                re.IGNORECASE | re.MULTILINE),                    0.06, "numbered_directive"),
]

_MAX_HEURISTIC_SCORE: float = sum(w for _, w, _ in _Q_SIGNALS)


class DocumentClassifierSkill(BaseSkill):
    """
    Classifies a document as 'questionnaire' or 'normal_document'.

    Config keys:
        questionnaire_threshold (float): Score ≥ threshold → questionnaire (default: 0.35)
        -- LLM provider settings are read via LLMClient.from_config() --
    """

    name = "document_classifier"
    description = "Detects whether a document is a questionnaire/form or a normal document."
    required_inputs = ["full_text"]

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        self._threshold = self.get_config("questionnaire_threshold", 0.4)

        from utils.llm_client import LLMClient
        self._llm = LLMClient.from_config(self.config)

    # ── Skill entry point ─────────────────────────────────────────────

    def execute(self, inputs: SkillInput) -> SkillOutput:
        start = time.monotonic()
        full_text: str = inputs.data["full_text"]

        if not full_text.strip():
            result = ClassificationResult(
                doc_type="normal_document", confidence=0.0,
                method="heuristic", signals={"reason": "empty_text"},
            )
            return SkillOutput(success=True, data=result,
                               duration_ms=(time.monotonic() - start) * 1000)

        # ── Step 1: Heuristics ─────────────────────────────────────────
        raw_score, signals = self._heuristic_score(full_text)
        normalized = min(raw_score / max(_MAX_HEURISTIC_SCORE, 0.01), 1.0)
        self.logger.debug(
            f"Heuristic score: {raw_score:.3f} → normalized {normalized:.3f} | signals: {list(signals)}"
        )

        confidence = normalized
        method = "heuristic"
        domain = "General"

        # ── Step 2: LLM Domain Detection & Disambiguation ──────────────
        if self._llm.available:
            self.logger.info(f"Consulting LLM ({self._llm.provider_label}) for domain & classification.")
            llm_result = self._llm_classify(full_text[:3000])
            if llm_result is not None:
                llm_score, _, detected_domain = llm_result
                domain = detected_domain
                signals["domain"] = domain
                
                # Blend structural score unless heuristics are overwhelmingly certain (> 0.85)
                # This ensures simple forms that score very low on heuristics are rescued by the LLM.
                if normalized < 0.85:
                    # Heavy weight to the LLM (70%) to catch nuanced real-world forms
                    confidence = 0.7 * llm_score + 0.3 * normalized
                    method = f"hybrid_{self._llm.provider}"
                    signals["llm_score"] = round(llm_score, 3)
                    self.logger.info(
                        f"Blended score: {confidence:.3f} (LLM {llm_score:.2f}) | Domain: {domain}"
                    )
                else:
                    self.logger.info(f"Domain detected: {domain} (Heuristic dominating: {normalized:.3f})")

        doc_type = "questionnaire" if confidence >= self._threshold else "normal_document"
        signals["final_score"] = round(confidence, 4)

        result = ClassificationResult(
            doc_type=doc_type,
            domain=domain,
            confidence=round(confidence, 4),
            method=method,
            signals=signals,
        )
        self.logger.info(
            f"Classification: {doc_type} | Domain: {domain} ({confidence:.0%} confidence, method={method})"
        )
        return SkillOutput(
            success=True,
            data=result,
            duration_ms=(time.monotonic() - start) * 1000,
        )

    # ── Heuristic engine ──────────────────────────────────────────────

    def _heuristic_score(self, text: str) -> Tuple[float, Dict[str, Any]]:
        """Scan text for questionnaire-indicator patterns and return weighted score."""
        score = 0.0
        signals: Dict[str, Any] = {}

        # Question-mark density
        q_marks = text.count("?")
        words = len(text.split())
        q_density = q_marks / max(words, 1)
        if q_density > 0.008:
            bonus = min(q_density * 6, 0.15)
            score += bonus
            signals["question_density"] = f"{q_density:.4f} (+{bonus:.3f})"

        # Pattern signals
        for pattern, weight, label in _Q_SIGNALS:
            if pattern.search(text):
                score += weight
                signals[label] = True

        return score, signals

    # ── LLM engine ────────────────────────────────────────────────────

    def _llm_classify(self, text_snippet: str) -> Optional[Tuple[float, str, str]]:
        """
        Ask LLM to classify the document structure and semantic domain.
        Returns (score, method, domain) where score is P(questionnaire) in [0, 1].
        """
        content = self._llm.chat(
            messages=[{
                "role": "user",
                "content": (
                    "You are an expert document classifier. Classify the following document structurally, "
                    "and identify its semantic domain (e.g. Financial, Environmental, Healthcare, Legal, Technical, Generic).\n"
                    "Respond with ONLY a valid JSON object — nothing else. Format:\n"
                    '{"type": "questionnaire|normal_document", "confidence": 0.92, "domain": "Financial"}\n\n'
                    f"Document:\n{text_snippet}\n\nClassification JSON:"
                ),
            }],
            temperature=0.0,
            max_tokens=80,
        )
        if not content:
            return None

        try:
            json_match = re.search(r"\{[^{}]+\}", content, re.DOTALL)
            if not json_match:
                self.logger.warning("LLM did not return parseable JSON.")
                return None
            obj  = json.loads(json_match.group())
            typ  = obj.get("type", "normal_document")
            conf = float(obj.get("confidence", 0.5))
            domain = str(obj.get("domain", "General")).strip()
            p_q  = conf if typ == "questionnaire" else (1.0 - conf)
            return p_q, "llm", domain
        except Exception as exc:
            self.logger.warning(f"LLM classification parse failed: {exc}")
            return None

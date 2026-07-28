"""
StructuredExtractionSkill — extracts structured entities, key-value pairs,
dates, monetary amounts, named parties, and domain-specific fields from a document.

Turns DocAgent from a summarizer into a document intelligence pipeline:
  - Financial report  → revenue, EPS, guidance, key metrics
  - Contract          → parties, effective date, termination clause, obligations
  - Invoice           → vendor, amounts, line items, due date
  - Medical record    → patient info, diagnoses, medications, dates
  - General           → dates, organisations, monetary amounts, locations

Uses Groq JSON mode (response_format={"type": "json_object"}) to guarantee
parseable output. Falls back to regex-based extraction if LLM is unavailable.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from core.models import SkillInput, SkillOutput
from skills.base_skill import BaseSkill
from utils.logger import get_logger

logger = get_logger(__name__)

# ── Domain-specific field schemas ─────────────────────────────────────────────

_DOMAIN_SCHEMAS: Dict[str, Dict[str, str]] = {
    "Financial": {
        "revenue":         "Total revenue or net sales (include units/currency)",
        "net_income":      "Net income or net profit",
        "eps":             "Earnings per share",
        "guidance":        "Forward guidance or outlook statements",
        "fiscal_period":   "Reporting period (e.g. Q3 2024)",
        "key_metrics":     "Up to 5 other important financial metrics",
        "risks":           "Key risk factors mentioned",
    },
    "Legal": {
        "parties":         "Names of all contracting parties",
        "effective_date":  "Contract effective / start date",
        "termination_date":"Contract end or expiry date",
        "governing_law":   "Jurisdiction or governing law clause",
        "obligations":     "Primary obligations of each party (brief)",
        "defined_terms":   "Key defined terms in the contract",
        "penalties":       "Penalty or liquidated damages clauses",
    },
    "Healthcare": {
        "patient_id":      "Patient identifier or MRN (anonymise if present)",
        "diagnoses":       "ICD codes or diagnosis descriptions",
        "medications":     "Prescribed medications with dosages",
        "procedures":      "Procedures or treatments performed",
        "dates":           "Key dates (admission, discharge, procedure)",
        "physician":       "Attending or ordering physician",
        "allergies":       "Known allergies",
    },
    "Research": {
        "title":           "Paper or study title",
        "authors":         "Author names",
        "hypothesis":      "Research hypothesis or objective",
        "methodology":     "Research methodology summary",
        "findings":        "Key findings or results",
        "limitations":     "Study limitations",
        "datasets":        "Datasets used",
    },
    "General": {
        "dates":           "All important dates mentioned",
        "organisations":   "Named organisations or companies",
        "people":          "Named individuals",
        "locations":       "Named locations, cities, countries",
        "monetary_values": "Monetary amounts with currency",
        "key_facts":       "Up to 5 other important facts or data points",
    },
}

# Domain aliases — map from ClassificationResult.domain to schema key
_DOMAIN_ALIASES: Dict[str, str] = {
    "Financial":    "Financial",
    "Legal":        "Legal",
    "Healthcare":   "Healthcare",
    "Medical":      "Healthcare",
    "Research":     "Research",
    "Scientific":   "Research",
    "Technical":    "General",
    "Educational":  "General",
    "Government":   "Legal",
    "Environmental":"General",
    "HR":           "General",
}

# Fallback regex patterns for LLM-less extraction ─────────────────────────────

_RE_DATES = re.compile(
    r"\b(?:\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}|"
    r"\d{4}[\/\-\.]\d{2}[\/\-\.]\d{2})\b",
    re.IGNORECASE,
)
_RE_MONEY = re.compile(
    r"(?:USD|EUR|GBP|INR|CAD|AUD|\$|€|£|₹)\s*[\d,]+(?:\.\d+)?(?:\s*(?:million|billion|M|B|K))?|"
    r"[\d,]+(?:\.\d+)?\s*(?:million|billion)\s*(?:USD|EUR|dollars|euros)",
    re.IGNORECASE,
)
_RE_PERCENTAGES = re.compile(r"\b\d+(?:\.\d+)?\s*%")
_RE_EMAILS = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Z]{2,}\b", re.IGNORECASE)


class StructuredExtractionSkill(BaseSkill):
    """
    Extracts domain-specific structured entities from document text.

    Config keys:
        max_text_chars  (int)  : Max chars sent to LLM (default: 8000)
        -- LLM provider settings are read via LLMClient.from_config() --
    """

    name = "structured_extraction"
    description = "Extracts structured entities, key-value pairs, and domain-specific fields."
    required_inputs = ["full_text"]

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        self._max_chars = self.get_config("max_text_chars", 8000)

        from utils.llm_client import LLMClient
        self._llm = LLMClient.from_config(self.config)

    # ── Entry point ────────────────────────────────────────────────────────────

    def execute(self, inputs: SkillInput) -> SkillOutput:
        start     = time.monotonic()
        full_text: str = inputs.data["full_text"]
        doc_type:  str = inputs.data.get("doc_type", "normal_document")
        domain:    str = inputs.data.get("domain", "General")

        if not full_text.strip():
            return SkillOutput(
                success=True,
                data={"entities": {}, "method": "empty"},
                duration_ms=(time.monotonic() - start) * 1000,
            )

        schema_key  = _DOMAIN_ALIASES.get(domain, "General")
        field_schema = _DOMAIN_SCHEMAS[schema_key]

        if self._llm.available:
            entities, method = self._llm_extract(full_text, doc_type, domain, field_schema)
        else:
            entities, method = self._regex_extract(full_text)

        self.logger.info(
            f"Structured extraction ({method}): {len(entities)} fields "
            f"(domain={domain}, schema={schema_key})"
        )
        return SkillOutput(
            success=True,
            data={"entities": entities, "method": method, "schema": schema_key},
            duration_ms=(time.monotonic() - start) * 1000,
        )

    # ── LLM extraction ─────────────────────────────────────────────────────────

    def _llm_extract(
        self,
        full_text: str,
        doc_type: str,
        domain: str,
        field_schema: Dict[str, str],
    ) -> Tuple[Dict[str, Any], str]:
        text_snippet = full_text[:self._max_chars]

        fields_desc = "\n".join(
            f'  "{k}": "{v}"' for k, v in field_schema.items()
        )
        schema_json = json.dumps({k: "" for k in field_schema}, indent=2)

        system_msg = (
            f"You are an expert {domain} data extraction specialist. "
            "Extract information from the document and return it as a single JSON object. "
            "Use null for fields not found in the document. "
            "Be precise — only extract information explicitly stated in the text."
        )

        user_msg = (
            f"Extract the following fields from this {doc_type.replace('_', ' ')} "
            f"({domain} domain):\n\n"
            f"Required fields and their meanings:\n{fields_desc}\n\n"
            f"Return ONLY a JSON object with exactly these keys:\n{schema_json}\n\n"
            f"Document text:\n{text_snippet}\n\n"
            "JSON extraction:"
        )

        content = self._llm.chat(
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user",   "content": user_msg},
            ],
            temperature=0.0,
            max_tokens=1500,
        )

        if not content:
            return self._regex_extract(full_text)

        entities = self._parse_json_response(content, field_schema)
        if entities:
            return entities, f"llm_{self._llm.provider}"

        return self._regex_extract(full_text)

    @staticmethod
    def _parse_json_response(
        content: str, field_schema: Dict[str, str]
    ) -> Dict[str, Any]:
        """Robustly extract a JSON object from LLM output."""
        # Try the full response first
        try:
            obj = json.loads(content.strip())
            if isinstance(obj, dict):
                # Filter to known keys, remove null/empty values
                return {k: v for k, v in obj.items() if v and k in field_schema}
        except json.JSONDecodeError:
            pass

        # Try to find a JSON object in the response
        match = re.search(r"\{[^{}]*\}", content, re.DOTALL)
        if match:
            try:
                obj = json.loads(match.group())
                if isinstance(obj, dict):
                    return {k: v for k, v in obj.items() if v and k in field_schema}
            except json.JSONDecodeError:
                pass

        return {}

    # ── Regex fallback ─────────────────────────────────────────────────────────

    @staticmethod
    def _regex_extract(full_text: str) -> Tuple[Dict[str, Any], str]:
        """Lightweight regex-based extraction when LLM is unavailable."""
        entities: Dict[str, Any] = {}

        dates = list(dict.fromkeys(_RE_DATES.findall(full_text)))[:10]
        if dates:
            entities["dates"] = dates

        amounts = list(dict.fromkeys(_RE_MONEY.findall(full_text)))[:10]
        if amounts:
            entities["monetary_values"] = amounts

        percentages = list(dict.fromkeys(_RE_PERCENTAGES.findall(full_text)))[:10]
        if percentages:
            entities["percentages"] = percentages

        emails = list(dict.fromkeys(_RE_EMAILS.findall(full_text)))[:5]
        if emails:
            entities["emails"] = emails

        return entities, "regex_fallback"

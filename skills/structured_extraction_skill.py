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

#: The schema a domain resolves to. Exposed so the planner can ask the same
#: question this skill asks, rather than keeping a second copy of the alias map
#: that would drift from this one.
GENERAL_SCHEMA = "General"


def schema_for_domain(domain: str) -> str:
    return _DOMAIN_ALIASES.get(domain, GENERAL_SCHEMA)


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


# ── Token budget: measured, not guessed ──────────────────────────────────────
#
# The old budget was a flat `max_tokens=1500`, sized against the expected reply.
# That is the same mistake `_llm_classify` made with 80 tokens for a 25-token
# JSON answer, and it failed the same way: `openai/gpt-oss-120b` reasons before
# it answers, `max_tokens` covers the reasoning AS WELL AS the reply, and an
# undersized budget returns `finish_reason: "length"` with EMPTY content.
#
# Measured across eight documents at a 6000-token budget, so nothing truncated
# and the true demand was visible:
#
#     document                 prompt   reasoning   content   total out
#     health_note                 520         326       166         492
#     research_paper              578         231       244         475
#     legal_msa                   635         314       207         521
#     fin_quarterly               660         400       214         614
#     general_ops                 471         524       200         724
#     sample_large_report        1710         710       241         951
#     sample_mixed_topics        1825         930       221        1151
#     sample_dense_manual        1905        1278       261        1539
#
# Two things follow, and they set the two constants below.
#
# CONTENT IS FLAT. 166-261 tokens across a 4x range of document size. The JSON
# reply is a fixed handful of fields and does not grow with the input, so a
# content budget can be small and stable. 400 clears the worst by 53%.
#
# REASONING IS WHAT SCALES. 231 to 1278, tracking prompt size rather than
# answer size. `sample_dense_manual` needed 1539 tokens in total and had 1500 —
# it truncated by 39 tokens, which is precisely the silent fallback the eval
# found. Reasoning is also noisy run to run, so the allowance clears the worst
# observation with margin rather than fitting it: 2048 against 1278, +60%.
#
# Summarisation's `_REASONING_ALLOWANCE` is 1024 and is NOT reusable here. It
# was sized against summarisation's worst observed reasoning of 902; extraction
# reaches 1278, because the model reasons over the whole document to locate
# fields rather than over one chunk to condense it.
#
# THE COST, stated rather than hidden: Groq counts prompt + max_tokens against
# the per-minute window, so raising the budget makes a 413 refusal more likely.
# That is the right trade only because a refusal is survivable — the client
# rotates keys, and the caller now says which failure happened — whereas
# truncation produced a guaranteed zero result that reported success.
_CONTENT_TOKENS = 400
_REASONING_ALLOWANCE = 2048
_MAX_TOKENS = _CONTENT_TOKENS + _REASONING_ALLOWANCE

#: Bounded by `max_text_chars` (8000 by default), which is what keeps the
#: reasoning figures above from growing without limit. Raising that cap
#: invalidates the measurement and the budget with it.

# ── Outcomes when extraction does not produce fields ─────────────────────────
#
# These are METHOD values, not error strings, because the caller stores the
# method and a reader needs to know which failure happened. They replace a
# single `regex_fallback` that meant four different things.
_M_TRUNCATED    = "unavailable_truncated"
_M_RATE_LIMITED = "unavailable_rate_limited"
_M_LLM_FAILED   = "unavailable_llm_failed"
_M_NO_LLM       = "unavailable_no_llm"
_M_UNPARSEABLE  = "unavailable_unparseable"
_M_REGEX_PARTIAL = "regex_partial"

#: Every method that means "extraction did not run". Used by the caller to
#: decide whether the stage succeeded, and exported so tests and the eval can
#: assert on the set rather than on individual strings.
UNAVAILABLE_METHODS = frozenset({
    _M_TRUNCATED, _M_RATE_LIMITED, _M_LLM_FAILED, _M_NO_LLM, _M_UNPARSEABLE,
})


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
            entities, method, warning = self._llm_extract(
                full_text, doc_type, domain, field_schema)
        else:
            entities, method, warning = self._no_llm_result(full_text, field_schema)

        # A stage that produced nothing must not report success.
        #
        # It used to. Any failure of the LLM path fell through to a regex
        # "fallback" that returns keys no typed schema defines, so the caller
        # received `{}` with `success=True` and a method saying `regex_fallback`
        # -- which reads like a degraded result rather than an absent one. The
        # eval measured that fallback at 0/28: it is not a degraded mode, it is
        # an elaborate way of returning nothing.
        ran = bool(entities) or method not in UNAVAILABLE_METHODS
        warnings = [warning] if warning else []

        if warning:
            self.logger.warning(warning)
        self.logger.info(
            f"Structured extraction ({method}): {len(entities)} fields "
            f"(domain={domain}, schema={schema_key})"
        )
        return SkillOutput(
            success=ran,
            data={"entities": entities, "method": method, "schema": schema_key},
            error=None if ran else warning,
            warnings=warnings,
            duration_ms=(time.monotonic() - start) * 1000,
        )

    # ── Why the LLM path produced nothing ──────────────────────────────────────

    def _diagnose(self) -> Tuple[str, str]:
        """Which failure just happened, as (method, CAUSE clause).

        The clause describes the cause only and carries no verdict, because the
        caller decides the verdict: nothing recovered means "did not run",
        something recovered means "fell back to pattern matching". An earlier
        version baked "did not run" into the clause and then appended "recovered
        2 fields" to it, which contradicted itself in the same sentence.

        Two different failures reached the same silent fallback and were
        indistinguishable to everyone downstream: the budget being too small
        (`finish_reason: "length"`, empty content) and the tier refusing the
        request under rate pressure (every key 413s, `chat()` returns None).
        They need different responses -- one is a code fix, the other is
        "try again in a minute" -- so they get different methods and different
        sentences, the same distinction the 413 handling already makes for
        summarisation.
        """
        finish = getattr(self._llm, "_last_finish_reason", None)
        failure = getattr(self._llm, "_last_failure", None)

        if finish == "length":
            return _M_TRUNCATED, (
                f"the model's reply was cut off at the {_MAX_TOKENS}-token "
                f"budget before the JSON was complete, which is a budget too "
                f"small for this document rather than a model failure"
            )
        if failure == getattr(self._llm, "FAILURE_RATE_LIMIT", "rate_limit_refused"):
            return _M_RATE_LIMITED, (
                "every API key refused the request under the per-minute token "
                "limit, so nothing is wrong with the document or the code and "
                "a retry in a minute will usually succeed"
            )
        return _M_LLM_FAILED, "the model returned no usable reply"

    @staticmethod
    def _compose(cause: str, recovered: int, total: int) -> str:
        """The sentence a human reads, with a verdict that matches the outcome."""
        if recovered:
            return (f"Structured extraction fell back to pattern matching: "
                    f"{cause}. Recovered {recovered} of {total} fields.")
        return (f"Structured extraction did not run: {cause}. "
                f"No fields were extracted.")

    def _regex_supplement(
        self, full_text: str, field_schema: Dict[str, str]
    ) -> Dict[str, Any]:
        """Whatever the regexes can contribute TO THIS SCHEMA, which is often
        nothing.

        Kept, but demoted. It is no longer a fallback that substitutes for the
        LLM path, because it cannot substitute for it: it emits only `dates`,
        `monetary_values`, `percentages` and `emails`, and the caller keeps only
        keys the selected schema defines --

            Financial 0/4   Legal 0/4   Research 0/4   Healthcare 1/4   General 2/4

        -- so for three of the five schemas it is incapable of returning a
        single valid field, by construction rather than by bad luck. Measured
        0 valid fields on 5/5 eval documents and 6/6 e2e fixtures.

        It runs anyway because on a General-schema document with ISO dates or
        currency-marked amounts it can genuinely recover a field or two, and
        one field is better than none. What it must never do again is make the
        stage look like it succeeded.
        """
        entities, _ = self._regex_extract(full_text)
        return {k: v for k, v in entities.items() if k in field_schema}

    def _no_llm_result(
        self, full_text: str, field_schema: Dict[str, str]
    ) -> Tuple[Dict[str, Any], str, Optional[str]]:
        """No LLM configured at all -- a deployment state, not a failure."""
        salvaged = self._regex_supplement(full_text, field_schema)
        cause = ("no LLM is configured, and pattern matching cannot fill a "
                 "typed schema")
        warning = self._compose(cause, len(salvaged), len(field_schema))
        if salvaged:
            return salvaged, _M_REGEX_PARTIAL, warning
        return {}, _M_NO_LLM, warning

    # ── LLM extraction ─────────────────────────────────────────────────────────

    def _llm_extract(
        self,
        full_text: str,
        doc_type: str,
        domain: str,
        field_schema: Dict[str, str],
    ) -> Tuple[Dict[str, Any], str, Optional[str]]:
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
            max_tokens=_MAX_TOKENS,
        )

        if not content:
            method, cause = self._diagnose()
            salvaged = self._regex_supplement(full_text, field_schema)
            warning = self._compose(cause, len(salvaged), len(field_schema))
            if salvaged:
                return salvaged, _M_REGEX_PARTIAL, warning
            return {}, method, warning

        entities = self._parse_json_response(content, field_schema)
        if entities:
            return entities, f"llm_{self._llm.provider}", None

        # A reply arrived and yielded no usable fields. That is a different
        # failure again from an absent reply -- the model answered, and either
        # the JSON was malformed or every value was empty -- so it gets its own
        # method rather than being folded into the others.
        salvaged = self._regex_supplement(full_text, field_schema)
        cause = "the model replied but no schema field could be parsed from it"
        warning = self._compose(cause, len(salvaged), len(field_schema))
        if salvaged:
            return salvaged, _M_REGEX_PARTIAL, warning
        return {}, _M_UNPARSEABLE, warning

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

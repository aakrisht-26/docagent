"""
StructuredExtractionSkill — extracts structured entities, key-value pairs,
dates, monetary amounts, named parties, and domain-specific fields from a document.

Turns DocAgent from a summarizer into a document intelligence pipeline:
  - Financial report  → revenue, EPS, guidance, key metrics
  - Contract          → parties, effective date, termination clause, obligations
  - Medical record    → diagnoses, current medications, procedures, dates
                        (patient identifiers are withheld)
  - Research paper    → title, authors, hypothesis, findings, datasets
  - General           → dates, organisations, monetary amounts, locations

Asks for a JSON object in the prompt and parses the first balanced object in the
reply; no provider JSON mode is requested. Two checks then run on the result:
identifiers are withheld and unverifiable figures dropped. Without an LLM the
stage reports itself unavailable, recovering only the regex-reachable fields a
schema happens to define (`dates`, `monetary_values`).
"""

from __future__ import annotations

import json
import os
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
        "patient_id":      "Patient identifier or MRN. WITHHELD: always null, and never copy an identifier into this or any other field",
        "diagnoses":       "ICD codes or diagnosis descriptions",
        "medications":     "Current medications with dosages; omit any whose status is stopped, discontinued or held",
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

#: Valid JSON, every value empty. NOT an unavailable method and NOT a failure:
#: the document was read and genuinely contains none of the schema's fields.
#: A sales spreadsheet has no net income, no EPS and no stated total revenue,
#: and saying so is the extractor working rather than failing.
_M_NO_FIELDS_FOUND = "no_fields_found"
_M_REGEX_PARTIAL = "regex_partial"

#: Every method that means "extraction did not run". Used by the caller to
#: decide whether the stage succeeded, and exported so tests and the eval can
#: assert on the set rather than on individual strings.
UNAVAILABLE_METHODS = frozenset({
    _M_TRUNCATED, _M_RATE_LIMITED, _M_LLM_FAILED, _M_NO_LLM, _M_UNPARSEABLE,
})


#: Numeric tokens, including thousands separators and decimals.
_RE_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numeric_tokens(text: str) -> List[str]:
    """Every number in `text`, commas stripped, as strings.

    Strings rather than floats: "2.14" and "2.140" are different claims about a
    document, and float equality would merge them.
    """
    return [m.group(0).replace(",", "").rstrip(".")
            for m in _RE_NUMBER.finditer(text or "")]


def unverified_numbers(value: object, source: str) -> List[str]:
    """Numbers in `value` that do not appear anywhere in `source`.

    THIS IS THE FABRICATION CHECK, and it exists because the model's refusal to
    invent a total turned out to be an accident rather than a safeguard.

    Measured: asked for Financial fields from a sales spreadsheet whose Revenue
    column has no totals row, the model emitted 2,379,900 -- exactly the column
    sum, a figure in no document -- on 20 of 20 interleaved runs of one text and
    0 of 20 of another. The two texts differ by ONE LINE, and an A/B over the
    two variables in that line found that BOTH perturbations independently
    cause it: removing a 60-character separator, or adding "FY26" to a sheet
    name. Only the exact original refuses, 0/26. The refusal is not the model
    applying "extract only what is stated"; it is one input that happens to land
    on the right side.

    So a prompt instruction cannot be relied on here, and the arithmetic is
    detectable after the fact: a number the model states as extracted should be
    findable in the text it was extracted from.

    DELIBERATELY NUMBERS ONLY. Prose can be legitimately paraphrased -- "Total
    maintenance spend: 1.94 million (Northern depot)" restates the document in
    words the document does not use, and that is correct behaviour. A FIGURE
    cannot be paraphrased: 1.94 is either in the source or it is not. Checking
    only numerals keeps the rule sharp enough to act on.

    Returns the offending tokens, so the caller can name them.
    """
    if value is None:
        return []
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    haystack = (source or "").replace(",", "")
    missing = []
    for token in _numeric_tokens(text):
        # A bare year or a small integer is too common to be evidence of
        # anything, and appears in dates, row counts and column headers. The
        # figures worth catching are the ones a reader would act on.
        if len(token.replace(".", "")) < 4:
            continue
        if token not in haystack:
            missing.append(token)
    return missing


# ── Withheld fields: enforced after extraction, not only requested ───────────
#
# `patient_id` stays in the Healthcare schema so the model knows the field exists
# and that it must not be filled. Asking was not enough. Measured at temperature
# 0.0 with the cache off, 10 draws each, when the field said "anonymise if
# present":
#
#     ward census sheet      patient_id leaked 10/10, an MRN in some field 9/10
#     prose discharge note   patient_id leaked  6/10
#
# On the sheet the MRNs did not stay in `patient_id`: 5 draws prefixed nearly
# every field with one ("55-40182: Amoxicillin 500 mg TDS"). Rewording the field
# as WITHHELD took both fixtures to 0/50. It did not take the IDENTIFIER to zero:
# with the column headed "Patient ID" instead of "MRN", 1 of 16 draws returned
# `patient_id` null and wrote all three MRNs into `dates`. The instruction held
# the field and not the value, so the value is enforced here, from the document
# rather than from what the model chose to put in the withheld field.
_WITHHELD_FIELDS = frozenset({"patient_id"})
_WITHHELD_MARK = "[withheld]"

#: Labels that introduce a patient identifier, in prose or as a column header.
_RE_ID_LABEL = re.compile(
    r"\b(?:MRN|medical record (?:number|no)|patient (?:id|identifier|number|no)|"
    r"hospital (?:number|no)|NHS (?:number|no))\b\.?",
    re.IGNORECASE)
#: A value written straight after a label: "MRN 55-40182",
#: "Patient identifier: MRN 55-40182", "NHS number: 485 777 3456".
_RE_ID_AFTER_LABEL = re.compile(
    r"[ \t]*[:#=]?[ \t]*(?:MRN[ \t]*[:#=]?[ \t]*)?"
    r"([A-Za-z0-9][A-Za-z0-9/-]*(?:[ \t]\d+)*)")
_RE_ID_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9/-]*")
_RE_CELL = re.compile(r"\S+")
_RE_DATE_TOKEN = re.compile(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}")


def _looks_like_identifier(token: str) -> bool:
    """Four or more digits, and not a date. Loose on purpose: withholding a
    number that was not an identifier costs a reader one value, and missing one
    that was emits a health identifier."""
    return (sum(c.isdigit() for c in token) >= 4
            and not _RE_DATE_TOKEN.fullmatch(token))


def identifiers_in(source: str) -> List[str]:
    """Patient identifiers the document itself labels, as written there.

    Two shapes, because the app produces both:

      PROSE   a label with the value beside it: "Patient identifier: MRN
              55-40182", "(MRN 55-40182)", "NHS number: 485 777 3456".
      TABLE   a label with nothing beside it heads a column, as in the
              `to_string` dump ExcelReaderSkill emits. The cell under the label
              is read in each row of that block, and the column is accepted
              when most rows hold something identifier-shaped. Right-aligned or
              not, a cell under a header overlaps the header's span.

    Measured before it was wired in: it finds the identifiers in both Healthcare
    eval fixtures and in four rewordings of them (a "Patient ID" header, a
    "Hospital No" column moved to the end, a spaced NHS number, an inline MRN),
    and finds nothing in the six other extraction fixtures, the 43 retrieval-eval
    texts, or the nine e2e sample files as the real readers parse them.

    NOT covered: an identifier with no label near it, and a table embedded as
    HTML by structure recognition. Those rest on the model's instruction and on
    whatever the model itself put in a withheld field.
    """
    found = set()
    lines = (source or "").splitlines()
    for n, line in enumerate(lines):
        for label in _RE_ID_LABEL.finditer(line):
            beside = _RE_ID_AFTER_LABEL.match(line, label.end())
            if beside and _looks_like_identifier(beside.group(1)):
                value = beside.group(1)
                found.add(value)
                first = value.split()[0]
                if first != value and _looks_like_identifier(first):
                    found.add(first)
                continue
            block = []
            for row in lines[n + 1:]:
                if not row.strip():
                    break
                block.append(row)
            cells = []
            for row in block:
                under = [m.group() for m in _RE_CELL.finditer(row)
                         if m.start() < label.end() and m.end() > label.start()]
                if len(under) == 1 and _looks_like_identifier(under[0]):
                    cells.append(under[0])
            if block and 2 * len(cells) > len(block):
                found.update(cells)
    return sorted(found)


def _identifier_pattern(identifier: str) -> "re.Pattern[str]":
    """The identifier however the model re-punctuated it: 55-40182, 5540182 and
    55 40182 are one MRN."""
    runs = re.findall(r"[A-Za-z0-9]+", identifier)
    body = r"[\s/-]?".join(re.escape(run) for run in runs)
    return re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", re.IGNORECASE)


def _as_text(value: object) -> str:
    return value if isinstance(value, str) else json.dumps(value, default=str)


def _only_withheld(value: object) -> bool:
    return not re.sub(r"\[withheld\]|[\W_]", "", _as_text(value))


def _redact(value: Any, patterns: List["re.Pattern[str]"]) -> Any:
    """Every identifier in `value`, at any depth, replaced by the mark. A list
    item left holding nothing but the mark is dropped; an untouched one never is."""
    if isinstance(value, str):
        for pattern in patterns:
            value = pattern.sub(_WITHHELD_MARK, value)
        return value
    if isinstance(value, (list, tuple)):
        pairs = [(item, _redact(item, patterns)) for item in value]
        return [new for old, new in pairs if not (new != old and _only_withheld(new))]
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            new_key = _redact(str(key), patterns)
            # Two keys that differed only by an identifier must not overwrite
            # each other once both read "[withheld]".
            while new_key in out:
                new_key += " "
            out[new_key] = _redact(item, patterns)
        return out
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _WITHHELD_MARK if _redact(str(value), patterns) != str(value) else value
    return value


def _first_json_object(text: str) -> str:
    """The first balanced {...} in `text`, or "".

    The previous version used a regex for a brace pair, which cannot match
    an object containing a nested object -- and a schema whose values are lists
    of dicts produces exactly that. Balancing braces costs nothing and removes a
    class of false "unparseable".
    """
    start = text.find("{")
    if start < 0:
        return ""
    depth = 0
    in_string = escaped = False
    for i, ch in enumerate(text[start:], start):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return ""


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

    def _drop_unverified(
        self, entities: Dict[str, Any], source: str
    ) -> Tuple[Dict[str, Any], List[Tuple[str, List[str]]]]:
        """Remove extracted values carrying numbers that are not in the source.

        WHY DROP RATHER THAN FLAG. A wrong figure is worse than a missing one:
        a missing revenue is visibly missing, a wrong one is a number somebody
        may act on. The whole eval is built on that asymmetry, so the check acts
        on it rather than annotating it.

        WHY THIS IS SAFE ENOUGH TO DROP. Measured before switching it on: across
        45 fields extracted from the eval's 8 documents it flagged 2, and BOTH
        were genuine fabrications -- the 2,379,900 column sum, and a `key_facts`
        entry asserting the year 2011, which appears nowhere in that document.
        Zero false positives. That number is worth re-deriving if the schemas or
        the model change; `tests/test_extraction_fabrication.py` pins the cases.

        LIST ITEMS ARE DROPPED INDIVIDUALLY. `key_facts` held four sound facts
        and one invented one; discarding the field would have lost the four to
        punish the one.

        Set DOCAGENT_EXTRACTION_VERIFY=false to disable, because silently
        removing data the model produced is a behaviour change and should be
        reversible without editing code.
        """
        if os.getenv("DOCAGENT_EXTRACTION_VERIFY", "").strip().lower() in (
                "0", "false", "no"):
            return entities, []

        cleaned: Dict[str, Any] = {}
        dropped: List[Tuple[str, List[str]]] = []
        for key, value in entities.items():
            if isinstance(value, (list, tuple)):
                kept, bad = [], []
                for item in value:
                    missing = unverified_numbers(item, source)
                    (bad.extend(missing) if missing else kept.append(item))
                if bad:
                    dropped.append((key, sorted(set(bad))))
                if kept:
                    cleaned[key] = kept
                continue
            missing = unverified_numbers(value, source)
            if missing:
                dropped.append((key, missing))
            else:
                cleaned[key] = value
        return cleaned, dropped

    def _withhold(
        self, entities: Dict[str, Any], source: str, field_schema: Dict[str, str]
    ) -> Tuple[Dict[str, Any], Optional[str]]:
        """Remove withheld fields, and the identifiers they would hold, from
        every field.

        Runs only when the schema has a withheld field, so the other four
        schemas pass through untouched. Identifiers come from the document
        (`identifiers_in`) and from whatever the model put in a withheld field
        when that text also appears in the document, which catches a label this
        module does not know provided the model announced the value.

        NO ESCAPE HATCH, unlike `DOCAGENT_EXTRACTION_VERIFY`. That check is a
        heuristic about fabrication and could be wrong in a way worth reversing.
        This one enforces what the schema says, and a switch that emits patient
        identifiers is not a behaviour to offer.

        The warning never repeats an identifier: it is shown in the UI and
        exported with the report, which is exactly where one must not appear.

        Replayed over every recorded reply before it was wired in -- 20 from
        before the schema change, 60 after, 32 on rewordings of the fixtures --
        it left no identifier in any field, changed no field that carried none,
        and put none in a warning.
        """
        if not _WITHHELD_FIELDS & set(field_schema):
            return entities, None
        removed = sorted(k for k in entities if k in _WITHHELD_FIELDS)
        identifiers = set(identifiers_in(source))
        for key in removed:
            identifiers.update(
                token for token in _RE_ID_TOKEN.findall(_as_text(entities[key]))
                if _looks_like_identifier(token) and token in (source or ""))
        patterns = [_identifier_pattern(i)
                    for i in sorted(identifiers, key=len, reverse=True)]

        cleaned: Dict[str, Any] = {}
        scrubbed: List[str] = []
        for key, value in entities.items():
            if key in _WITHHELD_FIELDS:
                continue
            new = _redact(value, patterns)
            if new != value:
                scrubbed.append(key)
                if _only_withheld(new):
                    continue
            cleaned[key] = new

        if not removed and not scrubbed:
            return cleaned, None
        notes = []
        if removed:
            notes.append(f"Structured extraction withheld {', '.join(removed)}: "
                         f"the schema never emits it.")
        if scrubbed:
            notes.append(f"A patient identifier found in the document was removed "
                         f"from {', '.join(scrubbed)}.")
        notes.append("The identifier is not repeated here.")
        return cleaned, " ".join(notes)

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

        entities, json_found = self._parse_json_response(content, field_schema)
        # Withholding runs FIRST. The fabrication check names the figures it
        # drops, and a re-punctuated MRN ("5540182") is a figure the document
        # never writes, so in the other order the identifier would be printed in
        # the very warning that reports its removal.
        entities, withheld = self._withhold(entities, full_text, field_schema)
        if entities:
            entities, dropped = self._drop_unverified(entities, full_text)
            notes = [withheld] if withheld else []
            if dropped:
                names = "; ".join(f"{k} ({', '.join(nums)})" for k, nums in dropped)
                notes.append(
                    f"Structured extraction discarded {len(dropped)} field(s) "
                    f"carrying figures that do not appear in the document: "
                    f"{names}. A number presented as extracted must be findable "
                    f"in the text it was extracted from."
                )
            return entities, f"llm_{self._llm.provider}", " ".join(notes) or None

        # VALID JSON WITH NOTHING IN IT IS NOT A FAILURE. The model read the
        # document, found none of the schema's fields, and said so in the shape
        # it was asked for. On a sales spreadsheet that is the correct answer:
        # there is no net income, no EPS, no guidance, and no STATED total
        # revenue. Reporting it as a failure blamed the system for working.
        if json_found:
            found_none = (
                f"Structured extraction found none of the {len(field_schema)} "
                f"fields the {domain} schema asks for. The document was read "
                f"and the model reported no match, which is the correct answer "
                f"when a document does not contain them."
            )
            return {}, _M_NO_FIELDS_FOUND, (
                f"{withheld} {found_none}" if withheld else found_none)

        # No JSON object at all. That IS a failure of the reply.
        salvaged = self._regex_supplement(full_text, field_schema)
        cause = "the model replied but the reply contained no JSON object"
        warning = self._compose(cause, len(salvaged), len(field_schema))
        if salvaged:
            return salvaged, _M_REGEX_PARTIAL, warning
        return {}, _M_UNPARSEABLE, warning

    @staticmethod
    def _parse_json_response(
        content: str, field_schema: Dict[str, str]
    ) -> Tuple[Dict[str, Any], bool]:
        """Extract a JSON object from the reply.

        Returns (fields, json_found). The SECOND value is the point.

        "No fields" used to mean two different things and both were reported as
        `unavailable_unparseable`:

          - the reply was not JSON at all, or held no object -- a real failure;
          - the reply was perfect JSON in which every value was null -- the
            model looking at the document and correctly finding nothing.

        `sample_sales.xlsx` produces the second, every time. The model returns
        `{"revenue": null, "net_income": null, ...}` because a sales sheet has
        no net income, no EPS, no guidance and no STATED total revenue, and the
        prompt tells it to use null for what is not there and to extract only
        what is explicitly stated. Reporting that as a parse failure blamed the
        parser for the model being right.

        That the refusal is right was checked rather than assumed: asked the
        same document WITHOUT the "only explicitly stated" instruction, the
        model returned `revenue: "$2,379,900"` -- exactly the sum of the Revenue
        column, a figure that appears nowhere in the sheet.
        """
        for candidate in (content.strip(),
                          _first_json_object(content)):
            if not candidate:
                continue
            try:
                obj = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                return ({k: v for k, v in obj.items() if v and k in field_schema},
                        True)
        return {}, False

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

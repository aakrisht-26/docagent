"""
PipelinePlanner — decides which skills to run for a given document.

Phase 1 (this implementation): rule-based routing.
  - Reads the ClassificationResult and basic document stats.
  - Returns an ordered list of skill names the agent should execute.
  - Saves 1-2 unnecessary LLM calls per run (e.g. question extraction on
    non-questionnaire docs was already skipped at skill level, but the skill
    still made the API round-trip; the planner avoids that entirely).

Phase 2 (future): LLM-driven planning.
  - PipelinePlanner.plan_with_llm() sends a skill menu + doc metadata to the
    LLM and gets back a JSON execution plan, enabling reasoning such as:
    "This legal contract needs clause extraction, not generic summarization."

Usage:
    planner = PipelinePlanner()
    plan    = planner.plan(classification_result, doc_stats)
    # plan == ["text_cleaner", "document_classifier", "summarization"]
"""

from __future__ import annotations

import os

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from skills.structured_extraction_skill import (
    GENERAL_SCHEMA, schema_for_domain)
from utils.logger import get_logger

logger = get_logger(__name__)

# ── Default full pipeline (skills in execution order) ─────────────────────────

_FULL_PIPELINE: List[str] = [
    "text_cleaner",
    "document_classifier",
    "structure_recognition",
    "summarization",
    "question_extraction",
    "structured_extraction",  # new skill (T1-4); skipped if not registered
]

# Domains where structure_recognition adds value (table-heavy documents)
_STRUCTURE_DOMAINS = frozenset({
    "Technical", "Financial", "Research", "Scientific", "Medical",
})


@dataclass
class DocStats:
    """
    Lightweight document statistics forwarded to the planner.
    All fields are optional — planner falls back to defaults when absent.
    """
    file_type:  str = "pdf"
    word_count: int = 0
    page_count: int = 0
    domain:     str = "General"
    doc_type:   str = "normal_document"
    has_tables: bool = False


class PipelinePlanner:
    """
    Rule-based pipeline planner.

    Determines the minimal set of skills needed for a document, skipping
    skills whose output would be meaningless or empty given what is known
    about the document after parsing and classification.
    """

    def plan(
        self,
        stats: DocStats,
        registered_skills: Optional[List[str]] = None,
    ) -> List[str]:
        """
        Return an ordered list of skill names to execute after parsing.

        Args:
            stats: Basic facts about the document (type, domain, size).
            registered_skills: Names of skills currently in the SkillRegistry.
                If provided, skills not in this list are silently excluded.

        Returns:
            Ordered list of skill names. Empty = skip all post-parse steps
            (should not happen in practice).
        """
        plan: List[str] = []

        # ── Step 2: Text cleaning ──────────────────────────────────────
        # Always run — normalises text for all downstream skills.
        plan.append("text_cleaner")

        # ── Step 3: Classification ─────────────────────────────────────
        # Always run — every downstream skill uses doc_type and domain.
        plan.append("document_classifier")

        # ── Step 3.5: Structure recognition ───────────────────────────
        # Only worthwhile for domains with heavy tabular content AND for
        # file types that can contain multi-column layouts.
        # Skip for pure-text formats (audio transcripts, plain CSV).
        if (
            stats.file_type not in ("audio",)
            and (stats.domain in _STRUCTURE_DOMAINS or stats.has_tables)
        ):
            plan.append("structure_recognition")
        else:
            logger.debug(
                f"Planner: skipping structure_recognition "
                f"(file_type={stats.file_type!r}, domain={stats.domain!r})"
            )

        # ── Step 4: Summarization ──────────────────────────────────────
        # Always run — primary output the user cares about.
        plan.append("summarization")

        # ── Step 5: Question extraction ────────────────────────────────
        # Only meaningful for questionnaire/form documents.
        # Skipping here saves an unnecessary LLM round-trip on normal docs
        # (the skill already short-circuits, but the planner avoids even
        # calling it).
        if stats.doc_type == "questionnaire":
            plan.append("question_extraction")
        else:
            logger.debug(
                f"Planner: skipping question_extraction "
                f"(doc_type={stats.doc_type!r})"
            )

        # ── Step 5.5: Structured extraction (T1-4) ────────────────────
        # Entity/KV extraction — useful for contracts, reports, invoices.
        # Skip for questionnaires (their value is in the questions, not KV)
        # and audio transcripts (no structured fields expected).
        #
        # ALSO SKIPPED when the domain resolves to the General schema, which is
        # a deliberate removal of behaviour and is measured rather than assumed.
        # One extraction call reserves 1905 prompt + 2448 budget = 4353 tokens,
        # 54% of a free-tier minute. The General schema's fields are enumerative
        # ("all important dates", "named organisations"), and read against a
        # real General summary its values -- the amounts, the sites, the people,
        # the consultancy -- were already in the prose with comparisons the
        # field list does not carry. So the call bought a subset of the summary.
        #
        # Measured over 11 documents: 4 route to General, so this removes 36%
        # of extraction calls.
        #
        # WHAT IT COSTS, stated rather than glossed. The classifier's domain is
        # imperfect, and the gate makes that consequential: `research_paper`
        # classifies as Technical, resolves to General, and is now skipped
        # entirely rather than getting a General field list. A document whose
        # true domain is typed but which the classifier routes to General loses
        # extraction altogether. Set DOCAGENT_EXTRACT_GENERAL=true to restore
        # the old behaviour without a code change.
        skip_general = (
            schema_for_domain(stats.domain) == GENERAL_SCHEMA
            and os.getenv("DOCAGENT_EXTRACT_GENERAL", "").strip().lower()
            not in ("1", "true", "yes")
        )
        if (stats.doc_type != "questionnaire" and stats.file_type != "audio"
                and not skip_general):
            plan.append("structured_extraction")
        elif skip_general:
            logger.info(
                f"Planner: skipping structured_extraction — domain "
                f"{stats.domain!r} resolves to the General schema, whose fields "
                f"restate the summary. Set DOCAGENT_EXTRACT_GENERAL=true to run "
                f"it anyway."
            )
        else:
            logger.debug(
                f"Planner: skipping structured_extraction "
                f"(doc_type={stats.doc_type!r}, file_type={stats.file_type!r})"
            )

        # ── Filter to registered skills ────────────────────────────────
        if registered_skills is not None:
            original = plan[:]
            plan = [s for s in plan if s in registered_skills]
            skipped = [s for s in original if s not in plan]
            if skipped:
                logger.debug(f"Planner: skills not in registry (skipped): {skipped}")

        logger.info(f"Pipeline plan ({stats.file_type}, {stats.doc_type}, {stats.domain}): {plan}")
        return plan

    def describe(self, plan: List[str]) -> str:
        """Human-readable description of a plan (for logging/debugging)."""
        return " → ".join(plan) if plan else "(empty plan)"

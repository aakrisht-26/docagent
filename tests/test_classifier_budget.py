"""Guards the LLM classification budget against a reasoning model.

`_llm_classify` asked for `max_tokens=80`. The reply is a 25-token JSON object,
so that looked generous. It is not, because gpt-oss-120b is a REASONING model
and spends a variable prefix of the completion budget thinking before emitting
anything. The API said so plainly:

    finish_reason     : length
    content           : ''
    reasoning_tokens  : 78   (of 80)

The client turns empty content into None, `_llm_classify` returns None on a
falsy reply, and classification silently falls back to the heuristic with
domain "General". The "did not return parseable JSON" warning never fires
because it sits downstream of that return, so nothing in the logs said the LLM
result had been discarded.

Two things then changed without anyone choosing them:

* `structure_recognition` is gated on `domain in _STRUCTURE_DOMAINS or
  has_tables`. With domain pinned to General it survives only on `has_tables`,
  so a table-bearing document whose tables the parser missed loses the stage
  that exists to rescue it. Two of five e2e fixtures were affected.
* `confidence = 0.7 * llm_score + 0.3 * heuristic` exists, by its own comment,
  to rescue forms that score low on heuristics. Without the LLM term the
  document never crosses the questionnaire threshold, so `question_extraction`
  is skipped for exactly the documents the blend was there to catch.

Measured across the six e2e fixtures at the 3000-char cap the skill passes:
reasoning 76-168 tokens, content a steady 25, worst total 193.

Run:
    pytest tests/test_classifier_budget.py -v
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Worst completion actually observed across the e2e fixtures (168 reasoning +
# 25 content). The floor is set above it with room for a document that reasons
# harder than anything currently in the suite.
OBSERVED_WORST_COMPLETION = 193
MIN_SAFE_BUDGET = 256


def _classifier_max_tokens() -> int:
    """Read the literal out of the source, so the test cannot drift from it."""
    source = (ROOT / "skills" / "document_classifier_skill.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg == "max_tokens" and isinstance(kw.value, ast.Constant):
                return kw.value.value
    raise AssertionError("no max_tokens found in document_classifier_skill.py")


class TestClassifierBudget(unittest.TestCase):
    def test_the_budget_clears_the_worst_measured_completion(self):
        budget = _classifier_max_tokens()
        self.assertGreater(
            budget, OBSERVED_WORST_COMPLETION,
            "a reasoning model spends the budget before answering; at 80 it "
            "never reached the JSON and classification fell back silently")

    def test_the_budget_has_headroom_over_that_worst_case(self):
        """An unused budget costs nothing; an exhausted one costs the stage."""
        self.assertGreaterEqual(_classifier_max_tokens(), MIN_SAFE_BUDGET)

    def test_the_reason_for_the_budget_is_written_down(self):
        """A bare number here reads as arbitrary and invites shrinking it."""
        source = (ROOT / "skills" / "document_classifier_skill.py").read_text(encoding="utf-8")
        self.assertIn("reasoning", source.lower())
        self.assertIn("finish_reason", source.lower())


class TestDiscardedClassificationIsVisible(unittest.TestCase):
    """The fallback must not be silent, whatever the cause."""

    def test_a_discarded_llm_result_is_logged(self):
        """`_llm_classify` returning None must leave a trace in the caller.

        Without this the only evidence is a domain of "General", which is also
        a legitimate answer, so the failure and the success look identical.
        """
        source = (ROOT / "skills" / "document_classifier_skill.py").read_text(encoding="utf-8")
        execute = source.split("def execute", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("logger.warning", execute,
                      "a discarded LLM result must warn; domain 'General' with "
                      "method 'heuristic' is also what a healthy heuristic-only "
                      "run looks like, so nothing else distinguishes them")


class TestPlannerDependsOnDomain(unittest.TestCase):
    """Documents the blast radius, so it is not rediscovered."""

    def test_structure_recognition_is_gated_on_domain(self):
        from agents.planner import _STRUCTURE_DOMAINS
        self.assertIn("Technical", _STRUCTURE_DOMAINS)
        self.assertIn("Financial", _STRUCTURE_DOMAINS)
        self.assertNotIn("General", _STRUCTURE_DOMAINS,
                         "if General were in here the bug would have been invisible")

    def test_a_general_domain_loses_the_stage_without_tables(self):
        from agents.planner import PipelinePlanner, DocStats
        planner = PipelinePlanner()
        general = planner.plan(DocStats(file_type="pdf", doc_type="normal_document",
                                        domain="General", word_count=500,
                                        has_tables=False))
        technical = planner.plan(DocStats(file_type="pdf", doc_type="normal_document",
                                          domain="Technical", word_count=500,
                                          has_tables=False))
        self.assertNotIn("structure_recognition", general)
        self.assertIn("structure_recognition", technical)


if __name__ == "__main__":
    unittest.main(verbosity=2)

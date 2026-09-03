"""Tests for structured extraction's token budget and its failure reporting.

This skill had no tests at all, which is how it kept two defects the extraction
eval eventually measured.

DEFECT 1 -- A BUDGET SIZED AGAINST THE REPLY. `max_tokens` was 1500. Measured
across eight documents at a 6000-token budget so nothing truncated:

    content   166-261 tokens, FLAT across a 4x range of document size
    reasoning 231-1278 tokens, scaling with the PROMPT

`sample_dense_manual.pdf` needed **1539** and had 1500. It truncated by 39
tokens, `chat()` returned None, and the stage reported success with zero
fields. Same bug as the classifier's 80-token budget for a 25-token reply.

DEFECT 2 -- ONE METHOD NAME FOR FOUR DIFFERENT FAILURES. Truncation, rate-limit
refusal, an unusable reply and no-LLM-configured all became `regex_fallback`,
and the stage returned `success=True` either way. A reader could not tell a
code fix (raise the budget) from "retry in a minute", and the eval measured
that "fallback" at 0/28.

WHY `success=False` NOW. A stage that produced nothing must not look like it
worked. The regex path is kept, but demoted to a supplement: it can only ever
emit `dates`, `monetary_values`, `percentages` and `emails`, and the caller
keeps only keys the selected schema defines, so for Financial, Legal and
Research it is incapable of returning a single valid field. That structural
claim has its own test, because it is the reason the demotion is correct rather
than a matter of taste.

Run:
    pytest tests/test_structured_extraction.py -v
"""

from __future__ import annotations

import unittest

from core.models import SkillInput
from core.skill_registry import SkillRegistry
from skills.structured_extraction_skill import (
    _CONTENT_TOKENS, _DOMAIN_SCHEMAS, _MAX_TOKENS, _REASONING_ALLOWANCE,
    UNAVAILABLE_METHODS,
)

#: Worst totals measured at a 6000-token budget. See the module docstring.
WORST_TOTAL_OBSERVED = 1539
WORST_REASONING_OBSERVED = 1278
WORST_CONTENT_OBSERVED = 261

#: A General-schema document the regexes CAN actually read: ISO dates and
#: currency-marked amounts. Most real documents give them nothing.
REGEX_READABLE = (
    "Board minutes. The contract was signed on 2026-03-14 and renewed on "
    "2026-09-01. Payment of $412,700 was approved, with a retainer of "
    "$38,900. Contact ops@halloway.example for detail. Growth was 6.3%."
)


def _skill():
    registry = SkillRegistry()
    registry.discover()
    return registry.instantiate("structured_extraction", config={})


def _run(skill, text=REGEX_READABLE, domain="Financial"):
    return skill.safe_execute(SkillInput(data={
        "full_text": text, "doc_type": "normal_document", "domain": domain}))


class TestTheBudgetIsSizedFromMeasurement(unittest.TestCase):
    def test_it_clears_the_worst_total_observed(self):
        """1539 on sample_dense_manual, against the old budget of 1500."""
        self.assertGreater(_MAX_TOKENS, WORST_TOTAL_OBSERVED)

    def test_the_old_budget_would_not_have(self):
        """Pins the defect: the previous value was below the observed need."""
        self.assertLess(1500, WORST_TOTAL_OBSERVED)

    def test_the_allowance_clears_the_worst_reasoning(self):
        self.assertGreater(_REASONING_ALLOWANCE, WORST_REASONING_OBSERVED)

    def test_the_content_budget_clears_the_worst_content(self):
        self.assertGreater(_CONTENT_TOKENS, WORST_CONTENT_OBSERVED)

    def test_the_budget_is_content_plus_allowance(self):
        """Two constants rather than one number, because they are sized from
        two different measurements and only one of them scales with input."""
        self.assertEqual(_MAX_TOKENS, _CONTENT_TOKENS + _REASONING_ALLOWANCE)

    def test_summarisations_allowance_would_have_been_too_small(self):
        """1024 was sized against summarisation's worst reasoning of 902.
        Extraction reaches 1278, so reusing it would have kept the bug."""
        self.assertLess(1024, WORST_REASONING_OBSERVED)

    def test_it_still_fits_a_free_tier_minute(self):
        """The cost of a bigger budget is a likelier 413, and it has a limit:
        prompt (~1900 at the 8000-char cap) plus budget must stay inside the
        8000 tokens-per-minute allowance."""
        self.assertLess(1900 + _MAX_TOKENS, 8000)


class TestEveryFailureSaysWhichOneItWas(unittest.TestCase):
    """Four causes, four methods, four sentences. They used to be one."""

    def setUp(self):
        self.skill = _skill()
        self._real_chat = self.skill._llm.chat

    def tearDown(self):
        self.skill._llm.chat = self._real_chat

    def _inject(self, content=None, finish=None, failure=None):
        self.skill._llm.chat = lambda **kw: content
        self.skill._llm._last_finish_reason = finish
        self.skill._llm._last_failure = failure
        if not self.skill._llm.available:
            self.skipTest("no LLM configured; the LLM branch is unreachable")
        return _run(self.skill)

    def test_truncation_is_named_as_a_budget_problem(self):
        out = self._inject(finish="length")
        self.assertEqual(out.data["method"], "unavailable_truncated")
        self.assertIn("budget", out.warnings[0].lower())

    def test_rate_limiting_is_named_as_transient(self):
        out = self._inject(failure="rate_limit_refused")
        self.assertEqual(out.data["method"], "unavailable_rate_limited")
        self.assertIn("retry", out.warnings[0].lower())

    def test_the_two_are_not_the_same_method(self):
        """The whole point. One is a code fix, the other is 'wait a minute'."""
        truncated = self._inject(finish="length").data["method"]
        limited = self._inject(failure="rate_limit_refused").data["method"]
        self.assertNotEqual(truncated, limited)

    def test_an_unparseable_reply_is_its_own_cause(self):
        out = self._inject(content="this is not json", finish="stop")
        self.assertEqual(out.data["method"], "unavailable_unparseable")

    def test_a_stage_that_produced_nothing_does_not_report_success(self):
        for kwargs in ({"finish": "length"},
                       {"failure": "rate_limit_refused"},
                       {"content": "not json", "finish": "stop"}):
            with self.subTest(**kwargs):
                out = self._inject(**kwargs)
                self.assertFalse(out.success)
                self.assertEqual(out.data["entities"], {})

    def test_the_failure_reaches_the_caller_as_a_warning(self):
        """Logged is not enough: the reason has to travel with the result."""
        out = self._inject(finish="length")
        self.assertTrue(out.warnings)
        self.assertTrue(out.error)

    def test_the_verdict_matches_the_outcome(self):
        """An earlier version said 'did not run' and then reported recovered
        fields in the same sentence."""
        out = self._inject(finish="length")
        self.assertIn("did not run", out.warnings[0])
        self.assertIn("No fields were extracted", out.warnings[0])


class TestTheRegexPathIsASupplementNotAFallback(unittest.TestCase):
    def test_it_cannot_serve_the_typed_schemas_at_all(self):
        """The structural claim behind the demotion. The regexes emit only
        these four keys, and three schemas define none of them."""
        emitted = {"dates", "monetary_values", "percentages", "emails"}
        for schema_name in ("Financial", "Legal", "Research"):
            with self.subTest(schema=schema_name):
                fields = set(_DOMAIN_SCHEMAS[schema_name])
                self.assertEqual(emitted & fields, set(),
                                 "if this ever passes, the regex path can serve "
                                 "this schema and the demotion should be revisited")

    def test_two_of_its_keys_are_fields_of_no_schema(self):
        """`percentages` and `emails` are always discarded before the caller
        sees them, so they were never output at all."""
        for key in ("percentages", "emails"):
            for schema_name, fields in _DOMAIN_SCHEMAS.items():
                with self.subTest(key=key, schema=schema_name):
                    self.assertNotIn(key, fields)

    def test_it_still_contributes_where_it_can(self):
        """Demoted, not deleted: on a General-schema document with ISO dates
        and currency amounts it recovers real fields, and one field beats none."""
        skill = _skill()
        real = skill._llm.chat
        skill._llm.chat = lambda **kw: None
        skill._llm._last_finish_reason = "length"
        skill._llm._last_failure = None
        try:
            if not skill._llm.available:
                self.skipTest("no LLM configured")
            out = _run(skill, domain="Technical")
        finally:
            skill._llm.chat = real
        self.assertEqual(out.data["method"], "regex_partial")
        self.assertTrue(out.success, "a partial result is still a result")
        self.assertIn("dates", out.data["entities"])
        self.assertIn("monetary_values", out.data["entities"])

    def test_a_partial_result_still_explains_itself(self):
        skill = _skill()
        real = skill._llm.chat
        skill._llm.chat = lambda **kw: None
        skill._llm._last_finish_reason = "length"
        skill._llm._last_failure = None
        try:
            if not skill._llm.available:
                self.skipTest("no LLM configured")
            out = _run(skill, domain="Technical")
        finally:
            skill._llm.chat = real
        self.assertIn("fell back to pattern matching", out.warnings[0])
        self.assertNotIn("did not run", out.warnings[0])

    def test_regex_partial_is_not_an_unavailable_method(self):
        self.assertNotIn("regex_partial", UNAVAILABLE_METHODS)


class TestUnchangedBehaviour(unittest.TestCase):
    def test_empty_text_is_still_a_clean_success(self):
        out = _run(_skill(), text="   ")
        self.assertTrue(out.success)
        self.assertEqual(out.data["method"], "empty")
        self.assertEqual(out.data["entities"], {})

    def test_a_working_extraction_reports_the_provider(self):
        skill = _skill()
        real = skill._llm.chat
        skill._llm.chat = lambda **kw: '{"revenue": "412.7 million dollars"}'
        skill._llm._last_finish_reason = "stop"
        skill._llm._last_failure = None
        try:
            if not skill._llm.available:
                self.skipTest("no LLM configured")
            out = _run(skill)
        finally:
            skill._llm.chat = real
        self.assertTrue(out.success)
        self.assertTrue(out.data["method"].startswith("llm_"))
        self.assertEqual(out.data["entities"]["revenue"], "412.7 million dollars")
        self.assertFalse(out.warnings)


if __name__ == "__main__":
    unittest.main(verbosity=2)

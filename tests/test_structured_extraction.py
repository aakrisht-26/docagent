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

import os
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


class TestFindingNothingIsNotAFailure(unittest.TestCase):
    """"Unparseable" used to mean two different things.

    `sample_sales.xlsx` -- a real fixture in the e2e harness -- returned
    `{"revenue": null, "net_income": null, ...}` on every run. That is perfect
    JSON. The filter drops falsy values, the result was `{}`, and the stage
    reported `unavailable_unparseable`: the parser blamed for the model being
    right.

    A sales sheet has no net income, no EPS, no guidance, no risks and no
    STATED total revenue -- only a Revenue column. The prompt tells the model to
    use null for what is not there and to extract only what is explicitly
    stated, so null is the correct answer.

    That the refusal is right was checked rather than assumed: asked the same
    document without the "only explicitly stated" instruction, the model
    returned `revenue: "$2,379,900"`, exactly the sum of the Revenue column and
    a figure that appears nowhere in the sheet.

    So a correct refusal to invent data is the system working, and it now
    reports `no_fields_found` with success=True.
    """

    def setUp(self):
        self.skill = _skill()
        self._real_chat = self.skill._llm.chat

    def tearDown(self):
        self.skill._llm.chat = self._real_chat

    def _reply(self, content):
        self.skill._llm.chat = lambda **kw: content
        self.skill._llm._last_finish_reason = "stop"
        self.skill._llm._last_failure = None
        if not self.skill._llm.available:
            self.skipTest("no LLM configured")
        return _run(self.skill)

    def test_all_null_json_is_not_a_failure(self):
        out = self._reply('{"revenue": null, "net_income": null, "eps": null}')
        self.assertEqual(out.data["method"], "no_fields_found")
        self.assertTrue(out.success)
        self.assertIsNone(out.error)

    def test_all_empty_strings_are_the_same_outcome(self):
        out = self._reply('{"revenue": "", "net_income": "", "eps": ""}')
        self.assertEqual(out.data["method"], "no_fields_found")
        self.assertTrue(out.success)

    def test_it_still_explains_itself(self):
        """Success is not silence: the reader needs to know why the panel is
        empty, and that it is the document rather than a fault."""
        out = self._reply('{"revenue": null}')
        self.assertTrue(out.warnings)
        self.assertIn("found none", out.warnings[0])

    def test_a_reply_with_no_json_is_still_a_failure(self):
        """The other half of the split. This one really is unparseable."""
        out = self._reply("I could not find those fields in the document.")
        self.assertEqual(out.data["method"], "unavailable_unparseable")
        self.assertFalse(out.success)

    def test_malformed_json_is_still_a_failure(self):
        out = self._reply('{"revenue": "412.7", "net_income":')
        self.assertEqual(out.data["method"], "unavailable_unparseable")
        self.assertFalse(out.success)

    def test_the_two_outcomes_are_different_methods(self):
        empty = self._reply('{"revenue": null}').data["method"]
        broken = self._reply("not json").data["method"]
        self.assertNotEqual(empty, broken)

    def test_no_fields_found_is_not_an_unavailable_method(self):
        """It is the difference between 'the stage did not run' and 'the stage
        ran and the document has none of these fields'."""
        self.assertNotIn("no_fields_found", UNAVAILABLE_METHODS)

    def test_a_populated_reply_is_unaffected(self):
        out = self._reply('{"revenue": "412.7 million dollars", "eps": null}')
        self.assertTrue(out.data["method"].startswith("llm_"))
        self.assertEqual(out.data["entities"], {"revenue": "412.7 million dollars"})


class TestTheJsonFinderHandlesNesting(unittest.TestCase):
    """The old finder was a regex for a brace pair and could not match an
    object containing an object -- which is what a list-of-dicts field value
    produces. That made a well-formed reply look unparseable."""

    def test_a_nested_object_is_found(self):
        from skills.structured_extraction_skill import _first_json_object
        found = _first_json_object('prose {"a": {"b": 1}, "c": 2} trailing')
        self.assertEqual(found, '{"a": {"b": 1}, "c": 2}')

    def test_braces_inside_strings_do_not_confuse_it(self):
        from skills.structured_extraction_skill import _first_json_object
        found = _first_json_object('{"note": "a } brace", "x": 1}')
        self.assertEqual(found, '{"note": "a } brace", "x": 1}')

    def test_no_object_returns_empty(self):
        from skills.structured_extraction_skill import _first_json_object
        self.assertEqual(_first_json_object("no json here"), "")

    def test_a_nested_reply_parses_end_to_end(self):
        skill = _skill()
        real = skill._llm.chat
        skill._llm.chat = lambda **kw: (
            'Here you go: {"revenue": "412.7", "key_metrics": {"a": "1"}}')
        skill._llm._last_finish_reason = "stop"
        skill._llm._last_failure = None
        try:
            if not skill._llm.available:
                self.skipTest("no LLM configured")
            out = _run(skill)
        finally:
            skill._llm.chat = real
        self.assertTrue(out.data["method"].startswith("llm_"))
        self.assertIn("revenue", out.data["entities"])


class TestTheGeneralSchemaIsGated(unittest.TestCase):
    """The stage no longer runs when the domain resolves to General.

    One call reserves 4353 tokens, 54% of a free-tier minute. The General
    schema's fields are enumerative, and read against a real General summary
    its values -- the amounts, the sites, the people, the consultancy -- were
    already in the prose, with comparisons the field list does not carry. The
    call bought a subset of the summary.

    Measured over 11 documents: 4 resolve to General, so this removes 36% of
    extraction calls. The cost is that a document whose true domain is typed
    but which the classifier routes to General now gets nothing at all --
    `research_paper` classifies as Technical and is skipped. That is why the
    escape hatch exists and has a test.
    """

    def _plan(self, domain, **kw):
        from agents.planner import DocStats, PipelinePlanner
        stats = DocStats(file_type=kw.pop("file_type", "pdf"),
                         word_count=500, domain=domain, **kw)
        return PipelinePlanner().plan(stats)

    def test_typed_schemas_still_run(self):
        for domain in ("Financial", "Legal", "Healthcare", "Research"):
            with self.subTest(domain=domain):
                self.assertIn("structured_extraction", self._plan(domain))

    def test_general_resolving_domains_are_skipped(self):
        """Technical, Educational, Environmental and HR all alias to General,
        and so does any domain with no alias at all."""
        for domain in ("Technical", "Educational", "Environmental", "HR",
                       "General", "Generic", "SomethingUnmapped"):
            with self.subTest(domain=domain):
                self.assertNotIn("structured_extraction", self._plan(domain))

    def test_the_escape_hatch_restores_the_old_behaviour(self):
        """Removing behaviour without a way back is worse than the behaviour."""
        os.environ["DOCAGENT_EXTRACT_GENERAL"] = "true"
        try:
            self.assertIn("structured_extraction", self._plan("Technical"))
        finally:
            os.environ.pop("DOCAGENT_EXTRACT_GENERAL", None)

    def test_the_hatch_is_off_unless_explicitly_set(self):
        for value in ("", "false", "no", "0", "maybe"):
            with self.subTest(value=value):
                os.environ["DOCAGENT_EXTRACT_GENERAL"] = value
                try:
                    self.assertNotIn("structured_extraction", self._plan("Technical"))
                finally:
                    os.environ.pop("DOCAGENT_EXTRACT_GENERAL", None)

    def test_the_older_skips_still_apply(self):
        """A questionnaire and an audio transcript were already skipped and
        must stay skipped even on a typed domain."""
        self.assertNotIn("structured_extraction",
                         self._plan("Financial", doc_type="questionnaire"))
        self.assertNotIn("structured_extraction",
                         self._plan("Financial", file_type="audio"))

    def test_the_planner_and_the_skill_agree_on_the_schema(self):
        """The planner asks the skill rather than keeping a second copy of the
        alias map, so the two cannot drift."""
        from skills.structured_extraction_skill import (
            _DOMAIN_ALIASES, schema_for_domain)
        for domain, expected in _DOMAIN_ALIASES.items():
            with self.subTest(domain=domain):
                self.assertEqual(schema_for_domain(domain), expected)
        self.assertEqual(schema_for_domain("NoSuchDomain"), "General")


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

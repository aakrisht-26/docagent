"""Tests that a too-small token budget is reported, not swallowed.

`chat()` returned None for three different things: the model failing, the model
having nothing to say, and the budget being exhausted before the model started
answering. The API distinguishes the third with `finish_reason: "length"`, and
that was discarded.

It matters because reasoning models spend a variable prefix of the completion
budget thinking. A budget that looks generous against the ANSWER can be far too
small once reasoning is counted: the classifier asked for 80 tokens for a
25-token JSON reply and got `reasoning_tokens: 78, content: ''`. Every caller
saw None and drew its own conclusion, all of them wrong.

The return contract is deliberately unchanged. Of the eight call sites, four
treat a falsy reply as a hard error and three use it as a legitimate fallback
signal, so raising would convert three working fallbacks into crashes. The fix
makes the REASON visible instead.

Run:
    pytest tests/test_llm_truncation.py -v
"""

from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from utils.config import load_config
from utils.llm_client import LLMClient


def _response(content, finish_reason, reasoning_tokens=0, completion_tokens=80):
    details = SimpleNamespace(reasoning_tokens=reasoning_tokens)
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=content),
            finish_reason=finish_reason,
        )],
        usage=SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=completion_tokens,
            total_tokens=100 + completion_tokens,
            completion_tokens_details=details,
        ),
    )


class _ClientCase(unittest.TestCase):
    def setUp(self):
        cfg = load_config().to_dict()
        cfg["groq"] = dict(cfg["groq"])
        cfg["groq"]["api_key"] = "gsk_patched_calls_never_leave_the_process"
        cfg["groq"]["api_keys"] = ""
        self.client = LLMClient.from_config(cfg)
        self.client._cache = None

    def _chat(self, response, max_tokens=80):
        """Run chat() against a canned response, with no network."""
        # The client calls `chat.completions.with_raw_response.create(...)` and
        # then `.parse()`, because the raw wrapper carries the x-ratelimit-*
        # headers. The stand-in mirrors that shape rather than the plain call.
        raw = SimpleNamespace(headers={}, parse=lambda: response)
        fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            with_raw_response=SimpleNamespace(create=lambda **kw: raw))))
        # `_run_with_rotation(operation, what)` calls `operation(client)`; this
        # stands in for the whole retry/rotation layer with one fixed client.
        with patch.object(self.client, "_run_with_rotation",
                          side_effect=lambda fn, what=None: fn(fake)):
            return self.client.chat(
                messages=[{"role": "user", "content": "x"}],
                max_tokens=max_tokens)


class TestEmptyBecauseOfBudget(_ClientCase):
    """The classifier bug, reproduced at the client boundary."""

    def test_it_still_returns_none(self):
        """The contract callers depend on must not change.

        Four call sites treat falsy as a hard error, three as a fallback
        signal. Raising here would break the three.
        """
        with self.assertLogs("docagent.utils.llm_client", level="WARNING"):
            self.assertIsNone(self._chat(_response("", "length", reasoning_tokens=78)))

    def test_it_warns_and_names_the_numbers(self):
        with self.assertLogs("docagent.utils.llm_client", level="WARNING") as cm:
            self._chat(_response("", "length", reasoning_tokens=78))
        logged = " ".join(cm.output)
        self.assertIn("NO CONTENT", logged)
        self.assertIn("80", logged, "the budget must be named")
        self.assertIn("78", logged, "the reasoning tokens must be named")

    def test_it_says_this_is_a_budget_problem_not_a_model_failure(self):
        """Two different fixes; one message for both sends people wrong."""
        with self.assertLogs("docagent.utils.llm_client", level="WARNING") as cm:
            self._chat(_response("", "length", reasoning_tokens=78))
        logged = " ".join(cm.output).lower()
        self.assertIn("too small", logged)
        self.assertIn("max_tokens", logged)

    def test_the_finish_reason_is_exposed(self):
        """So a caller CAN distinguish the cases if it chooses to."""
        with self.assertLogs("docagent.utils.llm_client", level="WARNING"):
            self._chat(_response("", "length", reasoning_tokens=78))
        self.assertEqual(self.client._last_finish_reason, "length")


class TestTruncatedButNonEmpty(_ClientCase):
    def test_a_partial_answer_is_returned_and_flagged(self):
        """Silently handing back half an answer is the worse option."""
        with self.assertLogs("docagent.utils.llm_client", level="WARNING") as cm:
            out = self._chat(_response("half an ans", "length", reasoning_tokens=10))
        self.assertEqual(out, "half an ans")
        self.assertIn("TRUNCATED", " ".join(cm.output))


class TestHealthyCompletion(_ClientCase):
    def test_a_normal_reply_warns_about_nothing(self):
        logger = logging.getLogger("docagent.utils.llm_client")
        with patch.object(logger, "warning") as warn:
            out = self._chat(_response("a full answer", "stop", reasoning_tokens=12))
        self.assertEqual(out, "a full answer")
        warn.assert_not_called()

    def test_finish_reason_is_recorded_for_healthy_calls_too(self):
        self._chat(_response("a full answer", "stop"))
        self.assertEqual(self.client._last_finish_reason, "stop")

    def test_an_empty_reply_that_was_not_truncated_is_not_blamed_on_budget(self):
        """`finish_reason: stop` with no content is a different problem.

        Blaming the budget for it would send the next reader to the wrong fix.
        """
        logger = logging.getLogger("docagent.utils.llm_client")
        with patch.object(logger, "warning") as warn:
            self.assertIsNone(self._chat(_response("", "stop")))
        for call in warn.call_args_list:
            self.assertNotIn("too small", str(call).lower())

    def test_a_missing_usage_block_does_not_crash_the_warning(self):
        """Not every provider returns reasoning_tokens."""
        response = _response("", "length")
        response.usage = None
        with self.assertLogs("docagent.utils.llm_client", level="WARNING"):
            self.assertIsNone(self._chat(response))


class TestRateLimitRefusalRotates(unittest.TestCase):
    """A 413 is key-specific and must move to the next key, not end the call.

    It used to fall through to the catch-all: log once, return None. Every
    caller then saw an indistinguishable empty result, and on the summarisation
    path that was a silent drop to extractive.

    Reading 413 as a per-request SIZE cap is what made that look correct. It is
    not one — it is a rolling per-minute consumption window. Measured on the
    real keys: one key accepted a 34,072-token request and refused an
    8,600-token one minutes later. A refusal describes that key right now, not
    the request, which is precisely what rotation is for.
    """

    def setUp(self):
        from utils.config import load_config
        from utils.llm_client import LLMClient
        cfg = load_config().to_dict()
        cfg["groq"] = dict(cfg["groq"])
        cfg["groq"]["api_key"] = ""
        cfg["groq"]["api_keys"] = ",".join(f"gsk_stub_key_{i}" for i in range(4))
        self.client = LLMClient.from_config(cfg)
        self.client._cache = None

    @staticmethod
    def _error_413():
        import httpx
        import openai
        request = httpx.Request("POST", "https://api.groq.com/v1/chat/completions")
        response = httpx.Response(413, request=request, json={"error": {
            "message": "Request too large ... tokens per minute (TPM): "
                       "Limit 8000, Requested 9585",
            "type": "tokens", "code": "rate_limit_exceeded"}})
        return openai.APIStatusError("Request too large", response=response, body=None)

    def test_a_refusal_on_one_key_completes_on_the_next(self):
        calls = {"n": 0}

        def operation(_sdk):
            calls["n"] += 1
            if calls["n"] == 1:
                raise self._error_413()
            return "served"

        with self.assertLogs("docagent.utils.llm_client", level="WARNING"):
            result = self.client._run_with_rotation(operation, what="chat")
        self.assertEqual(result, "served")
        self.assertEqual(calls["n"], 2, "it must try a second key, not give up")

    def test_it_advances_the_key_pointer(self):
        """Retrying the same key would just be refused again."""
        seen = []

        def operation(_sdk):
            seen.append(self.client._current_key_idx)
            if len(seen) == 1:
                raise self._error_413()
            return "served"

        with self.assertLogs("docagent.utils.llm_client", level="WARNING"):
            self.client._run_with_rotation(operation, what="chat")
        self.assertNotEqual(seen[0], seen[1])

    def test_every_key_refusing_gives_up_bounded(self):
        """If the request really is too large for all of them, stop cleanly."""
        calls = {"n": 0}

        def operation(_sdk):
            calls["n"] += 1
            raise self._error_413()

        with self.assertLogs("docagent.utils.llm_client", level="WARNING"):
            result = self.client._run_with_rotation(operation, what="chat")
        self.assertIsNone(result)
        budget = len(self.client.api_keys) + self.client.max_total_retries
        self.assertLessEqual(calls["n"], budget)

    def test_the_warning_names_the_key_and_the_cause(self):
        def operation(_sdk):
            raise self._error_413()

        with self.assertLogs("docagent.utils.llm_client", level="WARNING") as cm:
            self.client._run_with_rotation(operation, what="chat")
        logged = " ".join(cm.output)
        self.assertIn("413", logged)
        self.assertIn("per-minute budget", logged)

    def test_other_status_errors_still_end_the_call(self):
        """Rotation is for capacity, not for a malformed request."""
        import httpx
        import openai
        request = httpx.Request("POST", "https://api.groq.com/v1/chat/completions")
        response = httpx.Response(400, request=request,
                                  json={"error": {"message": "bad request"}})
        calls = {"n": 0}

        def operation(_sdk):
            calls["n"] += 1
            raise openai.APIStatusError("bad", response=response, body=None)

        with self.assertLogs("docagent.utils.llm_client", level="WARNING"):
            self.assertIsNone(self.client._run_with_rotation(operation, what="chat"))
        self.assertEqual(calls["n"], 1, "a 400 must not be retried across keys")


class TestRefusalIsDistinguishableFromEmptiness(unittest.TestCase):
    """Callers get None for every failure, so the REASON has to travel too.

    "LLM failed or timed out" covered a tier refusal and a dead model equally.
    They need opposite responses -- ask for less or wait, versus fix your
    configuration -- and conflating them is how Exhaustive dropped to extractive
    in silence.
    """

    def setUp(self):
        from utils.config import load_config
        from utils.llm_client import LLMClient
        cfg = load_config().to_dict()
        cfg["groq"] = dict(cfg["groq"])
        cfg["groq"]["api_key"] = ""
        cfg["groq"]["api_keys"] = ",".join(f"gsk_stub_key_{i}" for i in range(3))
        self.client = LLMClient.from_config(cfg)
        self.client._cache = None

    @staticmethod
    def _error_413():
        import httpx
        import openai
        request = httpx.Request("POST", "https://api.groq.com/v1/chat/completions")
        response = httpx.Response(413, request=request, json={"error": {
            "message": "Request too large ... tokens per minute (TPM): "
                       "Limit 8000, Requested 9585"}})
        return openai.APIStatusError("too large", response=response, body=None)

    def test_a_tier_refusal_is_recorded_as_such(self):
        def operation(_sdk):
            raise self._error_413()

        with self.assertLogs("docagent.utils.llm_client", level="WARNING"):
            self.client._run_with_rotation(operation, what="chat")
        self.assertEqual(self.client._last_failure, self.client.FAILURE_RATE_LIMIT)

    def test_a_non_refusal_failure_is_recorded_differently(self):
        import openai

        def operation(_sdk):
            raise openai.APITimeoutError(request=None)

        with self.assertLogs("docagent.utils.llm_client", level="WARNING"):
            self.client._run_with_rotation(operation, what="chat")
        self.assertEqual(self.client._last_failure, self.client.FAILURE_EXHAUSTED)

    def test_a_refusal_that_then_succeeds_is_not_a_failure(self):
        """Rotation rescued it, so nothing should be reported as failed."""
        calls = {"n": 0}

        def operation(_sdk):
            calls["n"] += 1
            if calls["n"] == 1:
                raise self._error_413()
            return "served"

        with self.assertLogs("docagent.utils.llm_client", level="WARNING"):
            self.assertEqual(
                self.client._run_with_rotation(operation, what="chat"), "served")
        self.assertIsNone(self.client._last_failure)

    def test_the_give_up_message_says_it_is_the_tier_not_the_model(self):
        def operation(_sdk):
            raise self._error_413()

        with self.assertLogs("docagent.utils.llm_client", level="ERROR") as cm:
            self.client._run_with_rotation(operation, what="chat")
        logged = " ".join(cm.output)
        self.assertIn("TIER declining", logged)
        self.assertIn("not the model failing", logged)

    def test_summarisation_says_which_cause_applied(self):
        """The user-facing half: a refusal must not read as a model failure."""
        from unittest.mock import patch
        from core.models import SkillInput
        from core.skill_registry import SkillRegistry
        import utils.llm_client as lc

        registry = SkillRegistry()
        registry.discover()
        from utils.config import load_config
        skill = registry.instantiate("summarization", config=load_config().to_dict())

        def refusing(self, operation, what=None):
            self._last_failure = self.FAILURE_RATE_LIMIT
            return None

        with patch.object(lc.LLMClient, "_run_with_rotation", refusing):
            with self.assertLogs("docagent.skill.summarization", level="WARNING") as cm:
                out = skill.safe_execute(SkillInput(data={
                    "full_text": "A depot report. " * 200,
                    "doc_type": "normal_document", "domain": "Technical",
                    "summary_length": "Exhaustive", "summary_tone": "Professional"}))

        self.assertTrue(out.success)
        self.assertEqual(out.data["method"], "extractive")
        self.assertIn("REFUSED the request size", " ".join(cm.output))
        self.assertTrue(any("per-minute token limit" in w for w in out.warnings),
                        "the user needs to be told a shorter length would fit")


class TestBudgetsAtCallSites(unittest.TestCase):
    """Budgets measured against this model rather than guessed."""

    def _budget(self, path: str, marker: str) -> int:
        import re
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / path).read_text(encoding="utf-8")
        after = src.split(marker, 1)[1]
        return int(re.search(r"max_tokens=(\d+)", after).group(1))

    def test_the_summarisation_map_step_has_headroom(self):
        """Measured need is ~943 content tokens on the largest real chunks.

        A truncated map chunk is dropped or half-kept by its caller, so the
        summary silently loses a section rather than failing.

        Asserted through the budget helper rather than by scraping a literal:
        the call site now composes its budget from a content figure plus the
        reasoning allowance, and a regex over the source would only re-encode
        the arithmetic it is supposed to be checking.
        """
        from skills.summarization_skill import (
            _MAP_CONTENT_TOKENS, _with_reasoning_room)
        self.assertGreaterEqual(_MAP_CONTENT_TOKENS, 943,
                                "must cover the measured content need")
        self.assertGreater(_with_reasoning_room(_MAP_CONTENT_TOKENS),
                           _MAP_CONTENT_TOKENS,
                           "and leave room for reasoning on top of it")

    def test_the_classifier_has_headroom(self):
        self.assertGreaterEqual(
            self._budget("skills/document_classifier_skill.py", "def _llm_classify"), 256)


if __name__ == "__main__":
    unittest.main(verbosity=2)

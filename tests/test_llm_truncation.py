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


class TestBudgetsAtCallSites(unittest.TestCase):
    """Budgets measured against this model rather than guessed."""

    def _budget(self, path: str, marker: str) -> int:
        import re
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / path).read_text(encoding="utf-8")
        after = src.split(marker, 1)[1]
        return int(re.search(r"max_tokens=(\d+)", after).group(1))

    def test_the_summarisation_map_step_has_headroom(self):
        """Worst measured chunk needed 549 tokens, 297 of them reasoning.

        A truncated map chunk is dropped by its caller, so the summary silently
        loses a section rather than failing.
        """
        self.assertGreaterEqual(
            self._budget("skills/summarization_skill.py", "def _call_map"), 1000)

    def test_the_classifier_has_headroom(self):
        self.assertGreaterEqual(
            self._budget("skills/document_classifier_skill.py", "def _llm_classify"), 256)


if __name__ == "__main__":
    unittest.main(verbosity=2)

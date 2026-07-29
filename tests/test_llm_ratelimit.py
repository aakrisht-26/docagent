"""Unit tests for LLMClient rate-limit handling.

Uses real `openai` exception types carrying real `httpx` responses and headers,
so the code under test sees exactly what the SDK would hand it in production.
No network access and no API key required.

Run:
    pytest tests/test_llm_ratelimit.py -v
"""

from __future__ import annotations

import time
import unittest

import httpx
import openai

from utils.llm_client import (
    LLMClient,
    parse_duration_seconds,
    parse_rate_limit_error,
)

REQ = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")

TPD_BODY = (
    "Rate limit reached for model `llama-3.3-70b-versatile` in organization "
    "`org_01kp6ehgn9fbzt3jfsskjgpxzf` service tier `on_demand` on tokens per day "
    "(TPD): Limit 100000, Used 99682, Requested 2267. Please try again in 28m3.936s. "
    "Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing"
)

TPM_BODY = (
    "Rate limit reached for model `llama-3.3-70b-versatile` in organization "
    "`org_01kp6ehgn9fbzt3jfsskjgpxzf` service tier `on_demand` on tokens per minute "
    "(TPM): Limit 12000, Used 11900, Requested 500. Please try again in 2.5s."
)


def rate_limit_error(body: str, retry_after: str | None = None) -> openai.RateLimitError:
    headers = {"retry-after": retry_after} if retry_after else {}
    response = httpx.Response(429, request=REQ, headers=headers)
    return openai.RateLimitError(body, response=response, body=None)


def make_client(keys: int = 4, park_threshold: float = 15.0) -> LLMClient:
    client = LLMClient(
        model="llama-3.3-70b-versatile",
        api_keys=[f"gsk_fake{i}" for i in range(keys)],
        cache_enabled=False,
        park_threshold_seconds=park_threshold,
    )
    client._client_for = lambda idx: idx          # operation receives the key index
    client._backoff_seconds = staticmethod(lambda attempt: 0.0)
    return client


class TestDurationParsing(unittest.TestCase):
    def test_compound_and_simple_forms(self):
        cases = {
            "28m3.936s": 1683.936,
            "1h16m19.2s": 4579.2,
            "1m26.4s": 86.4,
            "185ms": 0.185,
            "7.5s": 7.5,
            "60": 60.0,
        }
        for text, expected in cases.items():
            self.assertAlmostEqual(parse_duration_seconds(text), expected, places=3, msg=text)

    def test_unparseable_returns_none(self):
        for text in ("", None, "soon", "abc"):
            self.assertIsNone(parse_duration_seconds(text))

    def test_trailing_period_is_stripped(self):
        # The 429 body ends the duration with a sentence period. If it is not
        # stripped the duration is unparseable and parking silently disables.
        info = parse_rate_limit_error(TPD_BODY)
        self.assertEqual(info["retry_after_text"], "28m3.936s")
        self.assertAlmostEqual(info["retry_after_seconds"], 1683.936, places=2)


class TestRateLimitBodyParsing(unittest.TestCase):
    def test_tpd_body(self):
        info = parse_rate_limit_error(TPD_BODY)
        self.assertEqual(info["limit_type"], "TPD")
        self.assertEqual(info["window"], "day")
        self.assertEqual(info["limit"], 100000)
        self.assertEqual(info["used"], 99682)
        self.assertEqual(info["organization"], "org_01kp6ehgn9fbzt3jfsskjgpxzf")

    def test_tpm_body(self):
        info = parse_rate_limit_error(TPM_BODY)
        self.assertEqual(info["limit_type"], "TPM")
        self.assertEqual(info["window"], "minute")
        self.assertAlmostEqual(info["retry_after_seconds"], 2.5, places=2)


class TestParkingBehaviour(unittest.TestCase):
    def _run(self, client, script):
        calls = []

        def op(idx):
            calls.append(idx)
            step = script[len(calls) - 1] if len(calls) <= len(script) else None
            if step is not None:
                raise step
            return f"ok-key{idx}"

        return client._run_with_rotation(op, what="test"), calls

    def test_long_429_parks_key_and_rotates(self):
        """A TPD refusal (28 minutes) must park the key, not retry it."""
        client = make_client()
        result, calls = self._run(client, [rate_limit_error(TPD_BODY), None])
        self.assertEqual(result, "ok-key1")
        self.assertEqual(calls, [0, 1], "should rotate straight to the next key")
        self.assertIn(0, client._parked_until, "key 0 should be parked")
        self.assertNotIn(0, client._dead_key_idxs, "parking is not retirement")

    def test_parked_key_is_skipped_on_next_call(self):
        client = make_client()
        self._run(client, [rate_limit_error(TPD_BODY), None])
        _, calls = self._run(client, [None])
        self.assertNotIn(0, calls, "parked key must not be retried while parked")

    def test_park_expires_and_key_returns(self):
        """Parking is temporary: daily and per-minute windows do reset."""
        client = make_client()
        self._run(client, [rate_limit_error(TPD_BODY), None])
        self.assertNotIn(0, client._live_key_idxs())

        client._parked_until[0] = time.monotonic() - 1  # window has elapsed
        self.assertIn(0, client._live_key_idxs(), "key should rejoin after expiry")
        self.assertNotIn(0, client._parked_until, "expired park should be cleared")

    def test_short_429_retries_without_parking(self):
        """A 2.5s TPM refusal is a brief throttle — back off, do not park."""
        client = make_client()
        result, calls = self._run(client, [rate_limit_error(TPM_BODY), None])
        self.assertEqual(result, "ok-key1")
        self.assertEqual(client._parked_until, {}, "short waits must not park")

    def test_retry_after_header_takes_precedence(self):
        """The header wins over the body when both are present."""
        client = make_client(park_threshold=15.0)
        # Body says 2.5s (short), header says 600s (long) -> must park.
        err = rate_limit_error(TPM_BODY, retry_after="600")
        result, _ = self._run(client, [err, None])
        self.assertEqual(result, "ok-key1")
        self.assertIn(0, client._parked_until, "header duration should drive the decision")

    def test_all_keys_parked_gives_up(self):
        client = make_client(keys=2)
        result, calls = self._run(
            client, [rate_limit_error(TPD_BODY), rate_limit_error(TPD_BODY)]
        )
        self.assertIsNone(result)
        self.assertEqual(sorted(client._parked_until), [0, 1])

    def test_401_still_retires_permanently(self):
        """401 is permanent; it must not be downgraded to a temporary park."""
        client = make_client()
        err = openai.APIStatusError(
            "invalid api key", response=httpx.Response(401, request=REQ), body=None
        )
        result, calls = self._run(client, [err, None])
        self.assertEqual(result, "ok-key1")
        self.assertIn(0, client._dead_key_idxs)
        self.assertNotIn(0, client._parked_until)


class TestHeadroomRecording(unittest.TestCase):
    def test_headers_are_recorded(self):
        client = make_client()
        headers = httpx.Headers({
            "x-ratelimit-limit-requests": "1000",
            "x-ratelimit-remaining-requests": "986",
            "x-ratelimit-reset-requests": "20m9.6s",
            "x-ratelimit-limit-tokens": "12000",
            "x-ratelimit-remaining-tokens": "11963",
            "x-ratelimit-reset-tokens": "185ms",
        })
        client._record_headroom(0, headers)
        snap = client.headroom_report()[0]
        self.assertEqual(snap["requests_remaining"], "986")
        self.assertEqual(snap["tokens_limit"], "12000")

    def test_missing_headers_are_ignored(self):
        client = make_client()
        client._record_headroom(0, httpx.Headers({}))
        self.assertEqual(client.headroom_report(), {})
        client._record_headroom(0, None)
        self.assertEqual(client.headroom_report(), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)

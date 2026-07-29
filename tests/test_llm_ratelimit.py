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

import utils.llm_client as llm_mod
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
    # Rate-limit state is now shared process-wide per key list, which is the
    # behaviour under test. Tests must therefore reset it or they leak into
    # each other.
    LLMClient.reset_shared_state()
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


class TestDerivedDailyHeadroom(unittest.TestCase):
    """TPD appears in no header, so headroom for it is inferred from refusals."""

    def _trip_tpd(self, client):
        def op(idx):
            raise rate_limit_error(TPD_BODY)
        client._run_with_rotation(op, what="test")

    def test_unknown_before_any_refusal(self):
        client = make_client()
        note = client._daily_headroom_note(0)
        self.assertIn("unknown", note)
        self.assertNotIn("~", note, "must not invent a number")
        self.assertEqual(client.daily_limit_report(), {})

    def test_figures_cached_from_refusal(self):
        client = make_client(keys=1)
        self._trip_tpd(client)
        seen = client.daily_limit_report()[0]
        self.assertEqual(seen["limit_type"], "TPD")
        self.assertEqual(seen["limit"], 100000)
        self.assertEqual(seen["used"], 99682)
        self.assertEqual(seen["requested"], 2267)
        self.assertIsNotNone(seen["resets_at"])

    def test_note_is_labelled_as_derived_not_live(self):
        client = make_client(keys=1)
        self._trip_tpd(client)
        note = client._daily_headroom_note(0)
        self.assertIn("318", note, "remaining = limit - used")
        self.assertIn("derived from refusal", note)
        self.assertIn("not live", note)

    def test_note_reports_reset_once_window_passes(self):
        client = make_client(keys=1)
        self._trip_tpd(client)
        client._observed_daily[0]["resets_at"] = time.monotonic() - 1
        note = client._daily_headroom_note(0)
        self.assertIn("window has since reset", note)
        self.assertIn("unknown until next refusal", note)

    def test_minute_window_refusal_is_not_cached_as_daily(self):
        client = make_client(keys=1)

        def op(idx):
            raise rate_limit_error(TPM_BODY)
        client._run_with_rotation(op, what="test")
        self.assertEqual(client.daily_limit_report(), {},
                         "a per-minute refusal must not populate daily figures")


class TestSweepBeforeBackoff(unittest.TestCase):
    """Short 429s must not cost one retry-after sleep per key.

    Each key has its own per-minute budget, so being throttled on one says
    nothing about the next. Sleeping before an untried key was pure dead time:
    eight keys at 7.5s each burned ~60s before the first untried key was even
    attempted, which is where a 59s classify call came from.
    """

    def setUp(self):
        LLMClient.reset_shared_state()
        self._slept: list = []
        self._real_sleep = llm_mod.time.sleep
        llm_mod.time.sleep = self._slept.append

    def tearDown(self):
        llm_mod.time.sleep = self._real_sleep

    def _client(self, keys=8):
        c = LLMClient(model="m", api_keys=[f"gsk_sweep{i}" for i in range(keys)],
                      cache_enabled=False)
        c._client_for = lambda idx: idx
        return c

    def test_no_sleep_when_a_later_key_succeeds(self):
        client = self._client()
        tried = []

        def op(idx):
            tried.append(idx)
            if idx == 0:
                raise rate_limit_error(TPM_BODY)   # 2.5s, a short throttle
            return "ok"

        self.assertEqual(client._run_with_rotation(op, what="t"), "ok")
        self.assertEqual(tried, [0, 1], "should move straight to the next key")
        self.assertEqual(self._slept, [], "must not sleep before trying an untried key")

    def test_one_sleep_per_full_sweep_not_one_per_key(self):
        client = self._client(keys=8)

        def op(idx):
            raise rate_limit_error(TPM_BODY)       # every key throttled

        client._run_with_rotation(op, what="t")
        # Old behaviour: 8 sleeps of 2.5s = 20s. New: one sleep per exhausted sweep.
        self.assertLessEqual(len(self._slept), 2,
                             f"expected at most one sleep per sweep, got {self._slept}")
        self.assertLess(sum(self._slept), 20.0,
                        "total delay must be far below the per-key-sleep behaviour")

    def test_all_keys_are_tried_before_any_backoff(self):
        client = self._client(keys=8)
        order: list = []

        def op(idx):
            order.append(("try", idx))
            raise rate_limit_error(TPM_BODY)

        original = llm_mod.time.sleep

        def spy(seconds):
            order.append(("sleep", seconds))
            original(seconds)

        llm_mod.time.sleep = spy
        try:
            client._run_with_rotation(op, what="t")
        finally:
            llm_mod.time.sleep = original

        first_sleep = next((i for i, e in enumerate(order) if e[0] == "sleep"), None)
        self.assertIsNotNone(first_sleep, "expected at least one backoff")
        tried_before_sleep = {e[1] for e in order[:first_sleep] if e[0] == "try"}
        self.assertEqual(len(tried_before_sleep), 8,
                         "every usable key must be tried before the first sleep")

    def test_backoff_uses_the_shortest_window(self):
        """The first window to reopen is the shortest, so wait for that one."""
        client = self._client(keys=2)
        waits = iter(["9s", "2s"])

        def op(idx):
            raise rate_limit_error(TPM_BODY, retry_after=next(waits, "2s"))

        client._run_with_rotation(op, what="t")
        self.assertTrue(self._slept, "expected a backoff")
        self.assertAlmostEqual(self._slept[0], 2.0, places=1,
                               msg=f"should wait the shortest window, got {self._slept}")


class TestSharedStateAcrossInstances(unittest.TestCase):
    """Every skill builds its own LLMClient; the key state must still be shared.

    Without this, a key parked during one stage was retried immediately by the
    next, and the "N key(s) still usable" count reset at every stage boundary.
    """

    def _park_key0(self, client):
        def op(idx):
            if idx == 0:
                raise rate_limit_error(TPD_BODY)
            return "ok"
        return client._run_with_rotation(op, what="stage")

    def _plain_client(self, keys=("gsk_shared_a", "gsk_shared_b", "gsk_shared_c")):
        c = LLMClient(model="m", api_keys=list(keys), cache_enabled=False)
        c._client_for = lambda idx: idx
        c._backoff_seconds = staticmethod(lambda attempt: 0.0)
        return c

    def setUp(self):
        LLMClient.reset_shared_state()

    def test_parking_persists_into_a_later_client(self):
        first = self._plain_client()
        self._park_key0(first)
        self.assertIn(0, first._parked_until)

        second = self._plain_client()          # a different stage's client
        self.assertIn(0, second._parked_until, "parking must carry across instances")
        self.assertNotIn(0, second._live_key_idxs(), "parked key must stay skipped")

    def test_usable_count_does_not_reset_between_stages(self):
        counts = []
        for _ in range(3):
            client = self._plain_client()
            counts.append(len(client._live_key_idxs()))
            # Park whichever key is currently first in the rotation.
            def op(idx, _c=client):
                raise rate_limit_error(TPD_BODY)
            client._run_with_rotation(op, what="stage")
        self.assertEqual(counts, [3, 0, 0],
                         "usable count must decrease and stay decreased, not reset")

    def test_401_retirement_is_shared(self):
        first = self._plain_client()

        def op(idx):
            if idx == 0:
                raise openai.APIStatusError(
                    "invalid", response=httpx.Response(401, request=REQ), body=None)
            return "ok"
        first._run_with_rotation(op, what="stage")

        second = self._plain_client()
        self.assertIn(0, second._dead_key_idxs, "401 retirement must be shared too")

    def test_different_key_lists_do_not_share(self):
        a = self._plain_client(("gsk_set1_a", "gsk_set1_b"))
        self._park_key0(a)
        b = self._plain_client(("gsk_set2_a", "gsk_set2_b"))
        self.assertEqual(b._parked_until, {},
                         "unrelated key sets must not share state")

    def test_reset_clears_shared_state(self):
        client = self._plain_client()
        self._park_key0(client)
        LLMClient.reset_shared_state()
        fresh = self._plain_client()
        self.assertEqual(fresh._parked_until, {})

    def test_rotation_pointer_is_shared(self):
        first = self._plain_client()
        self._park_key0(first)
        moved_to = first._current_key_idx
        second = self._plain_client()
        self.assertEqual(second._current_key_idx, moved_to,
                         "a later stage should resume where the previous left off")


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

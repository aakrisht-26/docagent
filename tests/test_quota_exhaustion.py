"""Tests for telling the user when the API quota is spent.

THE BUG. The rotation already knew: it parks a key the tier refuses, and parses
the reset window out of the retry-after. None of it reached the user. An
exhausted-quota run looked exactly like a slow one and then produced an
extractive summary with a note that named no cause.

Three things were wrong, and only the first was the one originally reported.

1.  THE GIVE-UP MESSAGE BLAMED THE KEYS. `_run_with_rotation`'s in-loop exit
    said "every key returned 401; giving up" and set no `_last_failure`. But a
    key leaves the rotation mid-call because it was PARKED on a 429, not
    because it was retired on a 401 — so the one message a user could act on
    told them to replace keys that were perfectly good.

2.  THE SWEEP KEPT HAMMERING A KEY THAT HAD JUST REFUSED. A day-window refusal
    does not always park its key: `_proportional_park_seconds` returns 0.0
    while more than `park_min_request` tokens of headroom remain, reasoning
    that a SMALL call would still fit. Sound for the key, wrong for the call in
    hand, which goes on retrying the same too-large request.

3.  `live` IS THE WRONG THING TO CHECK, and this is the one that reading the
    code would not have shown. Because of (2), `live` reads 8 of 8 while the
    day's budget is plainly gone. A pre-flight written against `live` alone was
    tested against a forced refusal and stayed silent through all of it. Hence
    `day_exhausted`, which is a separate fact and has to be.

MEASURED, against real HTTP 429s carrying a real Groq TPD body (limit 300000,
used 299184, requested 4353, "try again in 1h16m19.2s"), whole pipeline over
`sample_report.pdf`, 8 keys both sides:

    before   43.0s   72 requests   warnings shown: none
    after    32.8s   48 requests   warnings shown: the cause, and the window

The band where (2) and (3) bite is headroom between `park_min_request` and the
request size — 3,853 tokens of a 300,000-token day, which is exactly where a
user who has been working all day sits.

NOTHING HERE IS MOCKED. A local HTTP server returns the refusal, and the
production code — `parse_rate_limit_error`, `_proportional_park_seconds`,
`_park_key`, `_live_key_idxs`, the give-up branch — does the rest. No network,
no API key, no quota.

Run:
    pytest tests/test_quota_exhaustion.py -v
"""

from __future__ import annotations

import http.server
import json
import threading
import unittest

from utils.llm_client import LLMClient

#: A real Groq tokens-per-day refusal. Kept verbatim: `parse_rate_limit_error`
#: reads the limit type, the window, the figures and the wait out of this exact
#: prose, so a paraphrase would test a document we never receive.
TPD_BODY = (
    "Rate limit reached for model `openai/gpt-oss-120b` in organization "
    "`org_01jx` service tier `on_demand` on tokens per day (TPD): "
    "Limit 300000, Used 299184, Requested 4353. "
    "Please try again in 1h16m19.2s. Need more tokens? Upgrade to Dev Tier."
)
#: Same shape, per-MINUTE window. The two must not be treated alike: a minute
#: clears itself, a day does not.
TPM_BODY = (
    "Rate limit reached for model `openai/gpt-oss-120b` in organization "
    "`org_01jx` service tier `on_demand` on tokens per minute (TPM): "
    "Limit 8000, Used 7900, Requested 4353. "
    "Please try again in 0.2s. Need more tokens? Upgrade to Dev Tier."
)

#: Three, not the eight a deployment uses. The sweep costs one attempt per key
#: times the OpenAI SDK's own internal retries, and every one of those is a
#: real socket and a real backoff — at eight keys this file took 4m49s. The
#: claims are all "every key", which three keys demonstrate as well as eight.
#: A per-minute refusal quoting longer than `park_threshold_seconds` (15s).
#: This one PARKS its keys — the proportional scaling is day-only — so it is
#: the case that empties the rotation from inside the sweep, which is the exit
#: that used to assert 401.
TPM_LONG_BODY = TPM_BODY.replace("try again in 0.2s", "try again in 45s")

KEYS = [f"gsk_forcedTESTkey00000000{i}" for i in range(3)]


class _Refuser(http.server.BaseHTTPRequestHandler):
    """Answers every completion with a 429. Set `BODY`/`RETRY_AFTER` per test."""

    protocol_version = "HTTP/1.1"
    BODY = TPD_BODY
    RETRY_AFTER = "4579"
    hits = 0

    def do_POST(self):                                  # noqa: N802
        type(self).hits += 1
        self.rfile.read(int(self.headers.get("content-length") or 0))
        raw = json.dumps({"error": {"message": type(self).BODY,
                                    "type": "tokens",
                                    "code": "rate_limit_exceeded"}}).encode()
        self.send_response(429)
        self.send_header("content-type", "application/json")
        if type(self).RETRY_AFTER is not None:
            # Omitting it is how a test asks for a LONG park without a long
            # test: `_run_with_rotation` falls back to the wait quoted in the
            # body, while the OpenAI SDK, having no header to honour, backs off
            # on its own short schedule instead of sleeping the full window.
            self.send_header("retry-after", type(self).RETRY_AFTER)
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


class _ForcedRefusal:
    """A live HTTP endpoint that refuses, plus a client pointed at it."""

    def __enter__(self):
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Refuser)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        _Refuser.hits = 0
        LLMClient.reset_shared_state()
        return self

    def client(self, **kw):
        kw.setdefault("max_total_retries", 4)
        return LLMClient(
            model="openai/gpt-oss-120b",
            api_keys=list(KEYS),
            base_url=f"http://127.0.0.1:{self.port}/v1",
            timeout=10,
            **kw,
        )

    def __exit__(self, *exc):
        self.srv.shutdown()
        _Refuser.BODY = TPD_BODY
        _Refuser.RETRY_AFTER = "4579"
        LLMClient.reset_shared_state()
        return False


def _ask(client):
    return client.chat(messages=[{"role": "user", "content": "hi"}],
                       max_tokens=4000)


class TestTheRotationNamesWhatWentWrong(unittest.TestCase):
    """A caller gets None back for every kind of failure, so `_last_failure` is
    the only way to tell "the quota is spent" from "the model broke". They need
    opposite words in front of a user."""

    def test_a_spent_day_quota_is_not_reported_as_attempts_exhausted(self):
        with _ForcedRefusal() as f:
            c = f.client()
            self.assertIsNone(_ask(c))
            self.assertEqual(c._last_failure, c.FAILURE_ALL_PARKED)

    def test_it_is_not_reported_as_invalid_keys_either(self):
        """The message that used to appear here sent the user to fix
        GROQ_API_KEYS. The keys are fine; the day is over."""
        with _ForcedRefusal() as f:
            c = f.client()
            _ask(c)
            self.assertNotEqual(c._last_failure, c.FAILURE_ALL_DEAD)

    def test_the_sweep_stops_once_every_key_has_refused(self):
        """At eight keys this was 12 attempts / 36 requests; every one after the
        eighth went to a key that had already said no to this exact call."""
        with _ForcedRefusal() as f:
            _ask(f.client())
            self.assertEqual(_Refuser.hits, len(KEYS) * 3,
                             "one attempt per key, times the SDK's own retries")

    def test_emptying_the_rotation_mid_call_is_not_reported_as_401(self):
        """THE ORIGINAL COMPLAINT, and the exit that produced it. A refusal
        quoting longer than the park threshold takes its key out of the
        rotation, so the sweep runs the rotation dry from the inside — and that
        exit said "every key returned 401; giving up" whatever had emptied it,
        while setting no `_last_failure` at all. It is reached here by a
        per-minute refusal, because a day refusal now stops the sweep earlier.
        """
        _Refuser.BODY, _Refuser.RETRY_AFTER = TPM_LONG_BODY, None
        with _ForcedRefusal() as f:
            c = f.client()
            self.assertIsNone(_ask(c))
            self.assertEqual(c.keys_status()["parked"], len(KEYS),
                             "the rotation was emptied by parking, not by 401")
            self.assertEqual(c._last_failure, c.FAILURE_ALL_PARKED)

    def test_a_short_per_minute_refusal_is_left_alone(self):
        """The stop is for DAY windows only. A minute clears itself, and
        treating it as terminal would abandon a run that backing off would have
        completed."""
        _Refuser.BODY, _Refuser.RETRY_AFTER = TPM_BODY, "0"
        with _ForcedRefusal() as f:
            c = f.client(max_total_retries=1)
            _ask(c)
            self.assertNotEqual(c._last_failure, c.FAILURE_ALL_PARKED)


class TestKeysStatusSeesWhatTheRotationSees(unittest.TestCase):
    def test_healthy_keys_report_nothing_wrong(self):
        LLMClient.reset_shared_state()
        st = LLMClient(model="m", api_keys=list(KEYS)).keys_status()
        self.assertEqual((st["configured"], st["live"], st["parked"]),
                         (len(KEYS), len(KEYS), 0))
        self.assertFalse(st["day_exhausted"])
        self.assertIsNone(st["seconds_until_reset"])

    def test_day_exhausted_is_true_while_every_key_is_still_live(self):
        """THE POINT OF THE FIELD. `live` is 8 of 8 here, because a day refusal
        leaves the key usable for a small call. Checking `live` alone — which
        is what the first version of the pre-flight did — says nothing at all
        in the case this whole file is about."""
        with _ForcedRefusal() as f:
            c = f.client()
            _ask(c)
            st = c.keys_status()
            self.assertTrue(st["day_exhausted"])
            self.assertEqual(st["live"], len(KEYS))
            self.assertEqual(st["parked"], 0)

    def test_the_reset_is_the_day_window_not_the_scaled_park(self):
        """Those answer different questions. The park is scaled down to when a
        500-token call would fit — measured at ~2 minutes where the day window
        had 76 to run. Quoting it would send the user back to the same refusal."""
        with _ForcedRefusal() as f:
            c = f.client()
            _ask(c)
            secs = c.keys_status()["seconds_until_reset"]
            self.assertIsNotNone(secs)
            self.assertGreater(secs, 4000, "the day window, not the small-call park")
            self.assertLessEqual(secs, 4579)

    def test_no_keys_configured_is_its_own_state(self):
        st = LLMClient(model="m", api_keys=[]).keys_status()
        self.assertEqual(st["configured"], 0)
        self.assertFalse(st["day_exhausted"])


class TestDescribeReset(unittest.TestCase):
    """Rounded UP. A user told "30 seconds" who then waits 45 has been misled,
    and the retry-after is itself approximate."""

    def test_the_bands(self):
        for seconds, expected in [(None, "shortly"),
                                  (12, "in under a minute"),
                                  (59, "in under a minute"),
                                  (95, "in about 2 minutes"),
                                  (4579, "in about 1 hour"),
                                  (7200, "in about 2 hours")]:
            with self.subTest(seconds=seconds):
                self.assertEqual(LLMClient.describe_reset(seconds), expected)


class TestTheUserIsTold(unittest.TestCase):
    """The warning has to survive the whole way out to `PipelineResult.warnings`,
    which is where a reader meets it. Stopping at the log is the original bug."""

    def test_the_summary_warning_names_the_cause_and_the_window(self):
        from core.skill_registry import SkillRegistry
        with _ForcedRefusal() as f:
            registry = SkillRegistry()
            registry.discover()
            skill = registry.instantiate("summarization", config={})
            skill._llm = f.client()
            _ask(skill._llm)                     # force the state, as a run would

            out = skill.execute(_summary_input())

        warning = " ".join(out.warnings)
        self.assertIn("quota", warning)
        self.assertIn("in about 1 hour", warning)
        self.assertIn("A shorter length will not help", warning,
                      "the size-refusal advice is wrong here and sends the user "
                      "round a loop that cannot succeed")

    def test_a_size_refusal_still_advises_a_shorter_length(self):
        """The neighbouring branch must not be swallowed by the new one: a 413
        really is fixed by asking for less."""
        from core.skill_registry import SkillRegistry
        LLMClient.reset_shared_state()
        registry = SkillRegistry()
        registry.discover()
        skill = registry.instantiate("summarization", config={})
        skill._llm.chat = lambda **kw: None
        skill._llm._last_failure = LLMClient.FAILURE_RATE_LIMIT

        out = skill.execute(_summary_input())
        warning = " ".join(out.warnings)
        self.assertIn("per-minute token limit", warning)
        self.assertIn("shorter summary length", warning)


def _summary_input():
    from core.models import SkillInput
    text = ("Quarterly Review\n\n" + "The northern depot reported a rise in "
            "maintenance spend against a flat headcount. " * 40)
    return SkillInput(data={"full_text": text, "chunks": [text],
                            "doc_type": "normal_document", "domain": "General"})


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Tests for what a user sees while a stage waits on the model.

THE REPORT. Exhaustive "sat on 'Stage 3.5/6 · Recognising structure… (4s
elapsed)' for over a minute with no further feedback", attributed to the
documented 413s.

WHAT WAS ACTUALLY HAPPENING, measured live on the deployment's eight keys.

  NOT 413s. Zero in 23 HTTP attempts across three runs. Every key reports an
  8,000 tokens-per-minute limit, and Groq still accepted a 10,672-token
  Exhaustive request (3,648 prompt + 7,024 max_tokens) — so the request is not
  refused for its size alone.

  THE OPENAI SDK'S OWN RETRIES. It defaults to two, retries a 429 on the SAME
  key, and sleeps the retry-after first. The rotation never saw the refusal, so
  it neither rotated nor logged, and every request of every run went to key 1
  while keys 2-8 sat at full budget:

      Exhaustive  sample_large_report.pdf   14s + 11s + 9s = 34s asleep on key 1
      Standard    sample_large_report.pdf   10s + 20s      = 30s asleep on key 1
      Exhaustive  sample_dense_manual.pdf   19s + 8s       = 27s asleep on key 1

  So it was not Exhaustive's problem: Standard hung the same way.

  AND A PANEL THAT DESCRIBED THE PAST. The progress text was written once, when
  a stage FINISHED, in that stage's present-progressive form, and left alone
  until the next one finished. "Recognising structure… (3s elapsed)" stood for
  27s over a stage that had taken 0.0s, while summarisation ran beneath it.

The HTTP tests run the real OpenAI SDK against a local server, so the SDK's
retry behaviour is what is under test rather than a stand-in for it.

Run:
    pytest tests/test_progress_feedback.py -v
"""

from __future__ import annotations

import http.server
import json
import threading
import unittest

from ui.streamlit_compat import RerunData, RerunException

import utils.llm_client as llm_mod
from utils.llm_client import LLMClient, rotation_listener

KEYS = [f"gsk_feedbackTESTkey0000000{i}" for i in range(3)]

OK = {
    "id": "chatcmpl-test", "object": "chat.completion", "created": 0,
    "model": "openai/gpt-oss-120b",
    "choices": [{"index": 0, "finish_reason": "stop",
                 "message": {"role": "assistant", "content": "ok"}}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}


def _error(message: str) -> dict:
    return {"error": {"message": message, "type": "tokens",
                      "code": "rate_limit_exceeded"}}


#: The measured case: a per-minute refusal, short enough to sit under the
#: rotation's 15s park threshold, so the right move is straight to an untried
#: key. With the SDK retrying, this refusal was slept on instead.
TPM_429 = (429, _error(
    "Rate limit reached for model `openai/gpt-oss-120b` in organization "
    "`org_01jx` service tier `on_demand` on tokens per minute (TPM): "
    "Limit 8000, Used 6815, Requested 3664. Please try again in 3s."),
    {"retry-after": "3"})

SIZE_413 = (413, _error(
    "Request too large for model `openai/gpt-oss-120b` in organization "
    "`org_01jx` service tier `on_demand` on tokens per minute (TPM): "
    "Limit 8000, Requested 9000, please reduce your message size and try again."),
    {})

#: A tokens-per-DAY refusal with so little headroom left that the key really
#: is parked (under `park_min_request`, 500 tokens) while the other keys stay
#: usable -- so the rotation moves on rather than giving up.
TPD_PARKING_429 = (429, _error(
    "Rate limit reached for model `openai/gpt-oss-120b` in organization "
    "`org_01jx` service tier `on_demand` on tokens per day (TPD): "
    "Limit 300000, Used 299600, Requested 4353. "
    "Please try again in 1h16m19.2s."),
    {"retry-after": "4579"})

TIMEOUT_408 = (408, {"error": {"message": "Request timeout"}}, {})


class _Groq(http.server.BaseHTTPRequestHandler):
    """Answers per key. `SCRIPT[key index]` is (status, body, headers); a key
    that is not scripted gets a normal completion."""

    protocol_version = "HTTP/1.1"
    SCRIPT: dict = {}
    hits: list = []

    def do_POST(self):                                  # noqa: N802
        self.rfile.read(int(self.headers.get("content-length") or 0))
        token = self.headers.get("authorization", "").replace("Bearer ", "")
        idx = KEYS.index(token) if token in KEYS else -1
        type(self).hits.append(idx)
        status, body, headers = type(self).SCRIPT.get(idx, (200, OK, {}))
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


class _Server:
    """`_Server(key0=TPM_429)` scripts key 1's answer; the rest succeed."""

    def __init__(self, **script):
        self.script = {int(name[3:]): answer for name, answer in script.items()}

    def __enter__(self):
        _Groq.SCRIPT = self.script
        _Groq.hits = []
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Groq)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        LLMClient.reset_shared_state()
        return self

    def client(self, max_total_retries: int = 2) -> LLMClient:
        return LLMClient(
            model="openai/gpt-oss-120b",
            api_keys=list(KEYS),
            base_url=f"http://127.0.0.1:{self.srv.server_address[1]}/v1",
            timeout=10,
            max_total_retries=max_total_retries,
            cache_enabled=False,
        )

    def __exit__(self, *exc):
        self.srv.shutdown()
        self.srv.server_close()
        _Groq.SCRIPT = {}
        LLMClient.reset_shared_state()
        return False


def _ask(client: LLMClient):
    return client.chat(messages=[{"role": "user", "content": "hi"}], max_tokens=64)


def _raise_rerun():
    raise RerunException(RerunData())


def _without_time(events):
    return [{k: v for k, v in e.items() if k != "at"} for e in events]


class TestTheSdkNoLongerRetriesUnderneathTheRotation(unittest.TestCase):
    def test_a_rate_limited_key_hands_over_instead_of_being_slept_on(self):
        """THE HANG. The refusal has to reach the rotation, which moves to an
        untried key at once. With the SDK retrying, this was two sleeps on
        key 1 and then key 1 again.

        SLEEPS ARE RECORDED, NOT TIMED. This used to assert the call took under
        2.0s of wall clock, which also counted the first `import openai` (1.25s
        measured) and building an SSL client per key. On 2026-09-14 it failed
        at 4.05s with the rotation working -- keys [0, 1], no sleep -- because
        the machine was loaded. Both regressions it guards are caught without a
        clock: SDK retries hit key 1 again, and any sleep is recorded here."""
        slept: list = []
        real_sleep = llm_mod.time.sleep
        llm_mod.time.sleep = slept.append
        try:
            with _Server(key0=TPM_429) as srv:
                self.assertEqual(_ask(srv.client()), "ok")
        finally:
            llm_mod.time.sleep = real_sleep
        self.assertEqual(_Groq.hits, [0, 1])
        self.assertEqual(slept, [], "nothing may sleep on a key that just refused")

    def test_a_408_is_still_retried_now_that_the_sdk_does_not(self):
        """The SDK used to retry 408 and 409 itself. Turning its retries off
        must not quietly make them fatal."""
        with _Server(key0=TIMEOUT_408) as srv:
            client = srv.client()
            client._backoff_seconds = lambda attempt: 0.0
            self.assertEqual(_ask(client), "ok")
        self.assertEqual(_Groq.hits, [0, 1])


class TestTheRotationSaysWhatItIsDoing(unittest.TestCase):
    def _run(self, **script):
        heard: list = []
        with _Server(**script) as srv, rotation_listener(heard.append):
            result = _ask(srv.client())
        return result, heard

    def test_a_per_minute_refusal_names_the_key_and_the_next_one(self):
        result, heard = self._run(key0=TPM_429)
        self.assertEqual(result, "ok")
        self.assertEqual(_without_time(heard), [
            {"kind": "attempt", "key": 1, "keys": 3, "attempt": 1, "attempts": 5},
            {"kind": "rotated", "reason": "rate_limit", "key": 1, "keys": 3,
             "next_key": 2},
            {"kind": "attempt", "key": 2, "keys": 3, "attempt": 2, "attempts": 5},
        ])

    def test_a_size_refusal_says_it_was_the_size(self):
        result, heard = self._run(key0=SIZE_413)
        self.assertEqual(result, "ok")
        self.assertIn({"kind": "rotated", "reason": "size", "key": 1, "keys": 3,
                       "next_key": 2}, _without_time(heard))

    def test_a_daily_refusal_is_not_called_per_minute(self):
        """A day-window refusal that parks its key must say DAILY. Worded as
        per-minute, it tells the user to expect the key back within a minute,
        which is wrong by hours. Found reading the diff: the park branch sent
        the same reason for both windows."""
        result, heard = self._run(key0=TPD_PARKING_429)
        self.assertEqual(result, "ok")
        self.assertIn({"kind": "rotated", "reason": "daily_limit", "key": 1,
                       "keys": 3, "next_key": 2}, _without_time(heard))

    def test_a_wait_is_announced_before_it_is_slept(self):
        """Once every key is throttled the rotation does sleep, and a sleep is
        exactly the silence the user saw. The event must come first and carry
        the length of the sleep that follows it."""
        order: list = []
        real_sleep = llm_mod.time.sleep
        llm_mod.time.sleep = lambda seconds: order.append(("sleep", seconds))
        try:
            with _Server(key0=TPM_429, key1=TPM_429, key2=TPM_429) as srv, \
                    rotation_listener(lambda e: order.append((e["kind"], e.get("seconds")))):
                _ask(srv.client(max_total_retries=1))
        finally:
            llm_mod.time.sleep = real_sleep
        sleeps = [i for i, entry in enumerate(order) if entry[0] == "sleep"]
        self.assertTrue(sleeps, f"expected a backoff, got {order}")
        self.assertEqual(order[sleeps[0] - 1], ("waiting", order[sleeps[0]][1]))


class TestAListenerBelongsToOneSession(unittest.TestCase):
    def test_another_thread_does_not_hear_it(self):
        """Streamlit runs every browser session on its own thread, in one
        process. A process-wide listener would post one user's retries into
        another user's progress panel."""
        heard: list = []
        seen_from_other: list = []

        def other_session():
            seen_from_other.append(llm_mod._listening())
            llm_mod._notify("attempt", key=1, keys=8)

        with rotation_listener(heard.append):
            worker = threading.Thread(target=other_session)
            worker.start()
            worker.join()
        self.assertEqual(seen_from_other, [False])
        self.assertEqual(heard, [])

    def test_the_previous_listener_is_restored(self):
        outer: list = []
        inner: list = []
        with rotation_listener(outer.append):
            with rotation_listener(inner.append):
                llm_mod._notify("attempt", key=1, keys=1)
            llm_mod._notify("attempt", key=2, keys=1)
        llm_mod._notify("attempt", key=3, keys=1)
        self.assertEqual([e["key"] for e in inner], [1])
        self.assertEqual([e["key"] for e in outer], [2])

    def test_a_listener_that_raises_does_not_fail_the_call(self):
        """A fault in how the wait is displayed must not cost the user the
        model call they are waiting on."""
        def broken(event):
            raise ValueError("display fault")

        with _Server() as srv, rotation_listener(broken):
            self.assertEqual(_ask(srv.client()), "ok")

    def test_a_rerun_inside_the_listener_is_deferred_by_the_ui_guard(self):
        """The UI's listener makes Streamlit calls from inside a model call,
        which is exactly where a queued widget interaction lands. Routed through
        the guard, the call completes and the interaction is kept."""
        from ui.app import _rerun_guard

        holder, ui = _rerun_guard()
        with _Server(key0=TPM_429) as srv, \
                rotation_listener(lambda event: ui(_raise_rerun)):
            self.assertEqual(_ask(srv.client()), "ok")
        self.assertIsNotNone(holder["exc"])

    def test_without_the_guard_a_rerun_would_unwind_the_call(self):
        """Pins WHY the guard is required: `_notify` drops an `Exception`, and
        Streamlit's rerun request is a `BaseException`."""
        with _Server() as srv, rotation_listener(lambda event: _raise_rerun()):
            with self.assertRaises(RerunException):
                _ask(srv.client())


class TestTheLiveLine(unittest.TestCase):
    """The words in the panel. Every time shown is an event on the run's own
    clock -- never a counter that looks live and is not."""

    START = 100.0

    @staticmethod
    def _ui():
        from ui.app import _clock, _describe_rotation_event, _stage_bar_text
        return _clock, _describe_rotation_event, _stage_bar_text

    def test_the_clock(self):
        clock, _d, _b = self._ui()
        for seconds, expected in [(0, "0:00"), (7.4, "0:07"), (102, "1:42"),
                                  (-3, "0:00")]:
            with self.subTest(seconds=seconds):
                self.assertEqual(clock(seconds), expected)

    def test_a_finished_stage_is_not_described_as_still_running(self):
        """THE PANEL DEFECT. This was "Stage 3.5/6 · Recognising structure…
        (3s elapsed)", and it stood for 27s after the stage had finished."""
        _c, _d, bar = self._ui()
        text = bar("structure_recognition", True, 3.2)
        self.assertEqual(text, "Stage 3.5/6 finished at 0:03")
        self.assertNotIn("Recognising", text)
        self.assertNotIn("elapsed", text)

    def test_a_failed_stage_still_says_failed(self):
        _c, _d, bar = self._ui()
        self.assertIn("FAILED", bar("summarize", False, 9.0))

    def test_a_request_says_when_it_went_out_and_on_which_key(self):
        _c, describe, _b = self._ui()
        self.assertEqual(
            describe({"kind": "attempt", "at": 131.2, "key": 1, "keys": 8}, self.START),
            "Waiting for the model — request sent at 0:31 on key 1 of 8.")

    def test_a_rotation_names_both_keys(self):
        _c, describe, _b = self._ui()
        self.assertEqual(
            describe({"kind": "rotated", "reason": "rate_limit", "key": 1,
                      "keys": 8, "next_key": 2, "at": 110.0}, self.START),
            "Key 1 of 8 hit its per-minute limit at 0:10 — trying key 2 of 8.")

    def test_a_size_refusal_reads_as_one(self):
        _c, describe, _b = self._ui()
        text = describe({"kind": "rotated", "reason": "size", "key": 3,
                         "keys": 8, "next_key": 4, "at": 100.0}, self.START)
        self.assertIn("refused a request this size", text)

    def test_a_daily_limit_reads_as_daily(self):
        _c, describe, _b = self._ui()
        self.assertEqual(
            describe({"kind": "rotated", "reason": "daily_limit", "key": 1,
                      "keys": 8, "next_key": 2, "at": 100.0}, self.START),
            "Key 1 of 8 hit its daily limit at 0:00 — trying key 2 of 8.")

    def test_a_wait_says_when_it_ends(self):
        _c, describe, _b = self._ui()
        self.assertEqual(
            describe({"kind": "waiting", "reason": "rate_limit", "seconds": 11.0,
                      "keys": 8, "at": 130.8}, self.START),
            "All 8 usable keys are at their per-minute limit — "
            "waiting until 0:42 for the first to reopen.")

    def test_a_transient_wait_says_when_it_retries(self):
        _c, describe, _b = self._ui()
        self.assertEqual(
            describe({"kind": "waiting", "reason": "transient", "seconds": 3.0,
                      "key": 2, "keys": 8, "at": 105.0}, self.START),
            "The model service had a problem on key 2 of 8 — retrying at 0:08.")

    def test_one_key_is_not_one_of_one(self):
        _c, describe, _b = self._ui()
        self.assertNotIn(" of 1", describe(
            {"kind": "attempt", "at": 100.0, "key": 1, "keys": 1}, self.START))

    def test_an_unknown_event_shows_nothing(self):
        _c, describe, _b = self._ui()
        self.assertIsNone(describe({"kind": "mystery", "at": 100.0}, self.START))

    def test_a_key_change_keeps_its_reason_on_screen_for_the_retry(self):
        """FOUND IN THE BROWSER, not by reading the code. The rotation line was
        overwritten by the next attempt's about a tenth of a second later, and
        a sampler reading the panel every 150ms never saw it: the user got "on
        key 2 of 8" -- that the key changed, not why. The reason has to survive
        into the line that stays up while the retried request runs."""
        from ui.app import _live_line

        pending: dict = {}
        lines = [_live_line(e, self.START, pending) for e in (
            {"kind": "attempt", "at": 110.0, "key": 1, "keys": 8},
            {"kind": "rotated", "reason": "rate_limit", "key": 1, "keys": 8,
             "next_key": 2, "at": 110.1},
            {"kind": "attempt", "at": 110.1, "key": 2, "keys": 8},
        )]
        self.assertEqual(
            lines[2],
            "Key 1 of 8 hit its per-minute limit at 0:10 — trying key 2 of 8.  \n"
            "Waiting for the model — request sent at 0:10 on key 2 of 8.")

    def test_the_reason_is_shown_once_and_not_carried_forward(self):
        """A key change explains the request it caused, not every later one."""
        from ui.app import _live_line

        pending: dict = {}
        _live_line({"kind": "rotated", "reason": "size", "key": 1, "keys": 8,
                    "next_key": 2, "at": 100.0}, self.START, pending)
        _live_line({"kind": "attempt", "at": 100.0, "key": 2, "keys": 8},
                   self.START, pending)
        self.assertEqual(
            _live_line({"kind": "attempt", "at": 105.0, "key": 2, "keys": 8},
                       self.START, pending),
            "Waiting for the model — request sent at 0:05 on key 2 of 8.")

    def test_real_events_from_a_real_refusal_read_as_sentences(self):
        """Ties the client's event shape to the UI's wording: if either drifts,
        this goes red rather than the panel going quiet."""
        _c, describe, _b = self._ui()
        heard: list = []
        with _Server(key0=TPM_429) as srv, rotation_listener(heard.append):
            _ask(srv.client())
        # Each event is measured from its own moment, so every clock reads 0:00.
        # The claim is the shape and the wording; a real socket's round-trip
        # must not decide whether it passes -- and it did, once, putting the
        # second event at 0:01 on a cold connection.
        self.assertEqual(
            [describe(e, e["at"]) for e in heard],
            ["Waiting for the model — request sent at 0:00 on key 1 of 3.",
             "Key 1 of 3 hit its per-minute limit at 0:00 — trying key 2 of 3.",
             "Waiting for the model — request sent at 0:00 on key 2 of 3."])


class TestTheRunPanelIsListening(unittest.TestCase):
    """The wiring rather than the wording: `_run_pipeline` must have the
    listener installed for the whole of `agent.run`. Without it every test above
    still passes and the panel says nothing -- which is the original complaint.

    Streamlit runs in bare mode here. `st.status` is replaced because its
    `update()` exists only under `streamlit run`. The agent is a stub whose
    `run` emits one event through the real `_notify`; everything between that
    and the panel is the app's own code.
    """

    def test_a_rotation_event_during_the_run_reaches_the_panel(self):
        import ui.app as app
        import ui.components.results_view as results_view

        written: list = []

        class _Slot:
            def markdown(self, text, *args, **kwargs):
                written.append(text)

            def empty(self):
                return None

            def progress(self, *args, **kwargs):
                return self

        class _Panel(_Slot):
            def empty(self):
                return _Slot()

            def update(self, **kwargs):
                return None

        class _Result:
            success = False
            errors = ["stub run"]
            warnings: list = []
            doc_type = "unknown"
            parsed_document = None

        class _Agent:
            def _log_step(self, *args, **kwargs):
                return None

            def run(self, path):
                llm_mod._notify("attempt", key=1, keys=8, attempt=1, attempts=12)
                return _Result()

        saved = (app._get_agent, app._keys_status, app.st.status, app.log_usage,
                 results_view.render_results)
        app._get_agent = lambda **kwargs: _Agent()
        app._keys_status = lambda: None
        app.st.status = lambda *args, **kwargs: _Panel()
        app.log_usage = lambda *args, **kwargs: None
        results_view.render_results = lambda *args, **kwargs: None
        try:
            with open("tests/e2e/samples/sample_report.pdf", "rb") as fh:
                app._run_pipeline("wiring_probe.pdf", {"bytes": fh.read()}, {})
        finally:
            (app._get_agent, app._keys_status, app.st.status, app.log_usage,
             results_view.render_results) = saved

        self.assertTrue(
            any(t.startswith("Waiting for the model — request sent at") for t in written),
            f"the live line never received the event; the panel got {written}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

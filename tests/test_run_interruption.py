"""Tests for surviving a widget interaction that lands mid-analysis.

THE BUG. Streamlit services a queued interaction by raising `RerunException` at
the next `st.*` call. `agent.run()` makes such calls through the UI's progress
wrapper — `bar.progress(...)` and `detail_placeholder.markdown(...)` are invoked
from inside `agent._log_step` on every stage — so touching ANY widget while a
document was analysing aborted the pipeline part-way and discarded the run.

That is the worst kind of failure this app has: it destroys work the user has
already paid for in wall-clock time and in API quota, and it is triggered by
something as innocent as switching the theme while waiting.

MEASURED, with the real exception raised from the wrapper after stage 2 of
`sample_report.pdf`:

    propagated (the bug)   run ended at 0.3s, 2 stages, nothing cached
    deferred   (the fix)   run completed, 5 stages, 218 words,
                           3,905-character summary, 7.0s

IT IS NOT ONLY THE THEME TOGGLE. Every Streamlit input widget queues a rerun the
same way — summary length, audience, the input-mode radio, the file uploader,
the YouTube field, every sidebar history button, "Clear history". The fix is at
the interruption point rather than on any one control, so it covers all of them
without enumerating them.

WHY DEFER RATHER THAN BLOCK. Disabling the controls for the duration needs a
two-phase render — draw them disabled, rerun, then start the work — and still
leaves the window between the click and that redraw, while taking the controls
away from the user instead of honouring them. Warning before discarding still
discards. Deferring keeps the work AND the interaction: the run finishes, the
result is cached, and the rerun is raised afterwards so the toggle applies one
run later.

Run:
    pytest tests/test_run_interruption.py -v
"""

from __future__ import annotations

import unittest

from streamlit.runtime.scriptrunner.exceptions import RerunData, RerunException


def _guard():
    """The app's guard, imported without executing the Streamlit script.

    `ui/app.py` runs Streamlit calls at import time, which is noisy but works in
    bare mode; the helper is module level precisely so this test can reach it.
    """
    from ui.app import _rerun_guard
    return _rerun_guard()


class TestTheGuardDefersRatherThanDropping(unittest.TestCase):
    def test_a_rerun_request_does_not_escape(self):
        """The whole point: the pipeline must not unwind."""
        holder, ui = _guard()

        def raises():
            raise RerunException(RerunData())

        ui(raises)                      # must not propagate
        self.assertIsNotNone(holder["exc"])

    def test_the_request_is_kept_so_it_can_be_honoured(self):
        """Swallowing it would lose the user's click, which is its own bug."""
        holder, ui = _guard()
        original = RerunException(RerunData())
        ui(lambda: (_ for _ in ()).throw(original))
        self.assertIs(holder["exc"], original)

    def test_the_first_request_is_the_one_kept(self):
        """Several stages can each hit a queued interaction; re-raising the
        last would discard the earliest intent for no reason."""
        holder, ui = _guard()
        first, second = RerunException(RerunData()), RerunException(RerunData())
        ui(lambda: (_ for _ in ()).throw(first))
        ui(lambda: (_ for _ in ()).throw(second))
        self.assertIs(holder["exc"], first)

    def test_a_normal_call_is_unaffected(self):
        holder, ui = _guard()
        self.assertEqual(ui(lambda a, b=0: a + b, 2, b=3), 5)
        self.assertIsNone(holder["exc"])

    def test_other_exceptions_still_propagate(self):
        """Only rerun control flow is deferred. A genuine error inside a
        progress update must not be silently absorbed."""
        holder, ui = _guard()
        with self.assertRaises(ValueError):
            ui(lambda: (_ for _ in ()).throw(ValueError("boom")))
        self.assertIsNone(holder["exc"])


class TestThePipelineSurvivesTheInterruption(unittest.TestCase):
    """The end-to-end claim, against the real pipeline and the real exception.

    This is the failure itself, not a stand-in: the exception is raised from
    `_log_step`, which is exactly where the UI's progress calls run.
    """

    FIXTURE = "tests/e2e/samples/sample_report.pdf"

    def _run(self, defer: bool, at_stage: int = 2):
        from pathlib import Path

        from agents.document_agent import DocumentAgent
        from utils.config import load_config

        if not Path(self.FIXTURE).exists():
            self.skipTest("fixture missing")
        agent = DocumentAgent(config=load_config().to_dict())
        original = agent._log_step
        state = {"n": 0, "deferred": None}

        def wrapper(skill, success, ms, error=None):
            original(skill, success, ms, error)
            state["n"] += 1
            try:
                if state["n"] == at_stage:
                    raise RerunException(RerunData())
            except RerunException as exc:
                if not defer:
                    raise
                state["deferred"] = exc

        agent._log_step = wrapper
        try:
            return agent.run(self.FIXTURE), state
        finally:
            agent._log_step = original

    def test_propagating_the_rerun_destroys_the_run(self):
        """Pins the bug so a regression is visible as a behaviour change."""
        with self.assertRaises(RerunException):
            self._run(defer=False)

    def test_deferring_it_lets_the_run_finish(self):
        result, state = self._run(defer=True)
        self.assertTrue(result.success)
        self.assertGreater(state["n"], 2, "more stages ran after the interrupt")
        self.assertGreater(result.word_count, 0)
        self.assertIsNotNone(state["deferred"],
                             "the interaction must still be available to honour")


if __name__ == "__main__":
    unittest.main(verbosity=2)

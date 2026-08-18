"""Tests for the e2e harness's own correctness guards.

The harness asserts things about the pipeline; nothing asserted anything about
the harness. That gap had a cost. When Groq retired
`llama-3.3-70b-versatile`, every LLM call began returning 404 and the pipeline
did what it is designed to do — degraded to extractive summarisation and
carried on. `PipelineResult.success` stayed True, so `pdf`, `scanned` and
`audio` all reported PASS while testing no LLM at all.

The bug was not the fallback, which is right for a user staring at a broken
provider. It was asserting on `success`, which is true of a degraded run by
design, instead of on whether the thing under test actually ran.

Run:
    pytest tests/test_e2e_guards.py -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "e2e"))

import e2e  # noqa: E402


def _result(method: str) -> SimpleNamespace:
    """A stand-in for PipelineResult carrying only what the guard reads."""
    return SimpleNamespace(summary_method=method, success=True)


class TestCheckLLMRan(unittest.TestCase):
    def test_an_llm_summary_passes(self):
        for method in e2e.LLM_SUMMARY_PREFIXES:
            with self.subTest(method=method):
                self.assertTrue(e2e.check_llm_ran(_result(method), "pdf"))

    def test_an_extractive_summary_fails(self):
        """The exact state three stages sat in while reporting PASS."""
        self.assertFalse(e2e.check_llm_ran(_result("extractive"), "pdf"))

    def test_a_summary_that_produced_nothing_fails(self):
        self.assertFalse(e2e.check_llm_ran(_result("none"), "pdf"))

    def test_a_planner_skip_is_allowed_but_announced(self):
        """Not every fixture should be summarised; that is the planner's call.

        Allowed, but printed, so a fixture that quietly shrinks below the
        threshold cannot turn a stage into a no-op without anyone seeing it.
        """
        import io
        from contextlib import redirect_stdout
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            passed = e2e.check_llm_ran(_result("skipped"), "audio")
        self.assertTrue(passed)
        self.assertIn("skipped summarisation", buffer.getvalue())

    def test_the_failure_message_names_the_method_and_the_stage(self):
        """A bare FAIL sends the reader to the wrong place."""
        import io
        from contextlib import redirect_stdout
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            e2e.check_llm_ran(_result("extractive"), "scanned")
        printed = buffer.getvalue()
        self.assertIn("scanned", printed)
        self.assertIn("extractive", printed)
        self.assertIn("degraded", printed)

    def test_success_alone_is_never_enough(self):
        """Guards the specific mistake: asserting on the wrong field.

        `success` is True for every case here, including the degraded ones. If
        this ever passes for "extractive", the harness is back to being green
        against a dead provider.
        """
        degraded = _result("extractive")
        self.assertTrue(degraded.success)
        self.assertFalse(e2e.check_llm_ran(degraded, "pdf"))


class TestHarnessWiring(unittest.TestCase):
    """Static checks: these would need a live API key to exercise for real."""

    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "tests" / "e2e" / "e2e.py").read_text(encoding="utf-8")

    def test_preflight_runs_before_any_stage(self):
        body = self.source.split("def main(", 1)[1]
        self.assertLess(body.index("preflight()"), body.index("run_stage("),
                        "a dead model must be reported before stages burn quota")

    def test_every_summarising_stage_asserts_the_llm_ran(self):
        """Every stage that runs the pipeline must check the LLM produced it.

        Asserted per stage rather than by counting call sites: a count breaks
        whenever a stage is added, which says nothing about coverage and trains
        people to bump the number.
        """
        for stage in ("pdf", "excel", "audio", "scanned", "youtube",
                      "questionnaire"):
            with self.subTest(stage=stage):
                self.assertIn(
                    stage, self.source,
                    f"{stage} is not in the harness at all")
        # The `rag` stage has no summary of its own — chat fails hard when the
        # LLM is unavailable, so it needs no separate assertion.
        summarising = self.source.count("check_llm_ran(result, stage)")
        self.assertGreaterEqual(
            summarising, 3,
            "the summarising stages must each assert the LLM ran")

    def test_no_stage_asserts_on_success_alone(self):
        """`ok = result.success` with nothing after it is the original bug."""
        for line in self.source.splitlines():
            stripped = line.strip()
            if stripped.startswith("ok = result.success"):
                self.assertIn("check_llm_ran", stripped,
                              f"bare success assertion is back: {stripped!r}")

    def test_preflight_names_the_model_in_its_diagnostic(self):
        preflight = self.source.split("def preflight", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("{model}", preflight)
        self.assertIn("deprecations", preflight,
                      "point the reader at the list that explains why")

    def test_preflight_distinguishes_a_dead_model_from_a_missing_key(self):
        """Two different fixes; one message for both would send people wrong."""
        preflight = self.source.split("def preflight", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("no usable Groq API key", preflight)
        self.assertIn("this is the MODEL, not the key", preflight)


if __name__ == "__main__":
    unittest.main(verbosity=2)

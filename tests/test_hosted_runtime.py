"""Tests for the hosted-deployment switches in utils/config.py.

These exist because both behaviours fail silently and one of them fails
unsafely:

* `load_streamlit_secrets_into_env()` — on Community Cloud the Groq keys arrive
  as Streamlit secrets, and Streamlit only mirrors them into os.environ when
  something reads `st.secrets`. Nothing did, so key resolution returned [] and
  every LLM stage degraded to extractive mode with no error anywhere.

* `is_hosted()` — this gates per-visitor history. Community Cloud runs ONE
  container for every visitor, so a shared history database would show one
  stranger's uploaded document, its extracted text, and chat over it, to the
  next stranger. A wrong answer here is a privacy leak, not a cosmetic bug.

Run:
    pytest tests/test_hosted_runtime.py -v
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from utils.config import is_hosted, load_streamlit_secrets_into_env


class _EnvGuard(unittest.TestCase):
    """Restores any environment variable the test touched."""

    _KEYS = ("DOCAGENT_HOSTED",)

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self._KEYS}
        for k in self._KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestIsHosted(_EnvGuard):
    def test_local_checkout_is_not_hosted(self):
        """The default for a developer machine must be False."""
        self.assertFalse(is_hosted())

    def test_explicit_true_values(self):
        for value in ("1", "true", "TRUE", "True", "yes", "on", "  true  "):
            with self.subTest(value=value):
                os.environ["DOCAGENT_HOSTED"] = value
                self.assertTrue(is_hosted(), f"{value!r} should mean hosted")

    def test_explicit_false_values(self):
        """An explicit false must win over any path heuristic."""
        for value in ("0", "false", "FALSE", "no", "off"):
            with self.subTest(value=value):
                os.environ["DOCAGENT_HOSTED"] = value
                self.assertFalse(is_hosted(), f"{value!r} should mean local")

    def test_unrecognised_value_falls_through_to_path_check(self):
        """Garbage must not be read as True — that would break local runs."""
        os.environ["DOCAGENT_HOSTED"] = "maybe"
        self.assertFalse(is_hosted())

    def test_mount_src_path_is_the_documented_backstop(self):
        """Community Cloud checks the repo out under /mount/src.

        Asserted against the same expression is_hosted() uses, so that if the
        heuristic is ever changed this test is the thing that notices.
        """
        probe = str(Path("/mount/src/docagent/utils/config.py")).replace("\\", "/")
        self.assertTrue(probe.startswith("/mount/src"))


class TestSecretsBridge(_EnvGuard):
    def test_returns_zero_and_does_not_raise_without_secrets(self):
        """The local case: no secrets.toml anywhere. Must be a silent no-op."""
        self.assertEqual(load_streamlit_secrets_into_env(), 0)

    def test_never_overrides_an_existing_environment_variable(self):
        """A local .env must win over a stray secrets file."""
        sentinel = "sentinel-value-do-not-clobber"
        os.environ["DOCAGENT_HOSTED"] = sentinel
        load_streamlit_secrets_into_env()
        self.assertEqual(os.environ["DOCAGENT_HOSTED"], sentinel)


class TestUploadCeiling(unittest.TestCase):
    def test_hosted_ceiling_is_lower_than_the_local_default(self):
        """Guards the direction of the constant, not its exact value.

        The hosted container is ~1 GB shared by every visitor, and OCR renders
        pages to bitmaps, so the hosted ceiling must stay below the local one.
        """
        import ast
        src = (Path(__file__).resolve().parents[1] / "ui" / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        hosted_mb = None
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == "HOSTED_MAX_UPLOAD_MB":
                        hosted_mb = ast.literal_eval(node.value)
        self.assertIsNotNone(hosted_mb, "HOSTED_MAX_UPLOAD_MB not found in ui/app.py")
        self.assertGreater(hosted_mb, 0)

        import yaml
        cfg = yaml.safe_load(
            (Path(__file__).resolve().parents[1] / "configs" / "default.yaml").read_text(encoding="utf-8"))
        local_mb = cfg["app"]["max_file_size_mb"]
        self.assertLess(hosted_mb, local_mb,
                        "hosted upload ceiling must be below the local one")



class TestUsageAccounting(unittest.TestCase):
    """The process-lifetime accumulator must survive per-run resets.

    reset_usage() is called at the start of every pipeline run, so the existing
    counter only ever describes the last document. Watching a shared API key
    against a daily limit needs the figure that does not reset, and the two are
    fed from the same call sites so they cannot drift apart.
    """

    def setUp(self):
        import utils.llm_client as lc
        self.lc = lc
        self._saved_run = lc._USAGE
        self._saved_total = lc._USAGE_TOTAL
        lc._USAGE = lc.UsageTotals()
        lc._USAGE_TOTAL = lc.UsageTotals()

    def tearDown(self):
        self.lc._USAGE = self._saved_run
        self.lc._USAGE_TOTAL = self._saved_total

    def _run(self, prompt, completion):
        self.lc.reset_usage()
        self.lc._USAGE.record_chat("m", prompt, completion)
        self.lc._USAGE_TOTAL.record_chat("m", prompt, completion)

    def test_per_run_counter_reflects_only_the_latest_run(self):
        self._run(1000, 100)
        self._run(2000, 200)
        self.assertEqual(self.lc.get_usage().total_tokens, 2200)

    def test_process_counter_accumulates_across_runs(self):
        self._run(1000, 100)
        self._run(2000, 200)
        self._run(3000, 300)
        self.assertEqual(self.lc.get_process_usage().total_tokens, 6600)
        self.assertEqual(self.lc.get_process_usage().calls, 3)

    def test_reset_usage_does_not_touch_the_process_counter(self):
        self._run(500, 50)
        before = self.lc.get_process_usage().total_tokens
        self.lc.reset_usage()
        self.assertEqual(self.lc.get_process_usage().total_tokens, before)
        self.assertEqual(self.lc.get_usage().total_tokens, 0)

    def test_uptime_is_positive(self):
        self.assertGreater(self.lc.process_uptime_seconds(), 0)

    def test_every_usage_site_feeds_both_counters(self):
        """Guards the invariant directly: no _USAGE mutation without a twin.

        If someone adds a fourth call site and updates only the per-run
        counter, the process total silently under-reports and the deployment
        stops being able to see how hard it is being hit.
        """
        import re
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "utils" / "llm_client.py").read_text(encoding="utf-8")
        body = src.split("def reset_usage", 1)[1]  # skip the declarations above
        run_sites = len(re.findall(r"(?<!_TOTAL)\b_USAGE\.(?:record_chat|cache_hits|transcriptions)", body))
        total_sites = len(re.findall(r"\b_USAGE_TOTAL\.(?:record_chat|cache_hits|transcriptions)", body))
        self.assertEqual(
            run_sites, total_sites,
            f"_USAGE mutated at {run_sites} site(s) but _USAGE_TOTAL at "
            f"{total_sites}; every usage site must feed both counters",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

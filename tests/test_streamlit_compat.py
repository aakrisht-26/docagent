"""Tests that the app can START, and that it does not depend on where
Streamlit happens to keep its internals.

THE OUTAGE. `ui/app.py` imported `RerunException` from
`streamlit.runtime.scriptrunner.exceptions`. That module exists on Streamlit
1.37.1, which this machine had, and not on 1.63.0, which Community Cloud
installed from `streamlit>=1.35.0`. The deployed app died at import with
ModuleNotFoundError before rendering anything.

WHY 584 TESTS AND 8 E2E STAGES PASSED ANYWAY. Two gaps, and this file closes
only the first by itself:

  1. Nothing imported `ui/app.py` at module level in a fresh interpreter. The
     e2e harness drives the agent, never the Streamlit script; the UI tests
     reach into `ui.app` for helpers, but on the same Streamlit. The import
     tests below execute the whole module top to bottom, in both the local and
     the hosted branch -- the hosted branch runs extra code at import on Cloud.

  2. Every test ran on the Streamlit installed HERE. Verified by reverting the
     fix: on 1.37.1 the import test still passes with the broken import in
     place, and on 1.63.0 it fails. So it guards the deployment only while the
     Streamlit installed locally is the one the deployment resolves.

Run:
    pytest tests/test_streamlit_compat.py -v
"""

from __future__ import annotations

import ast
import importlib
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import ui.streamlit_compat as compat

ROOT = Path(__file__).resolve().parent.parent


def _python(code: str, hosted: bool = False) -> subprocess.CompletedProcess:
    """Run `code` in a fresh interpreter from the repo root, as the app would
    start: nothing already imported, nothing cached by another test."""
    env = dict(os.environ)
    env.pop("DOCAGENT_HOSTED", None)
    if hosted:
        env["DOCAGENT_HOSTED"] = "true"
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code)],
                          cwd=ROOT, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)


def _tail(proc: subprocess.CompletedProcess, lines: int = 25) -> str:
    return "\n".join((proc.stdout + proc.stderr).splitlines()[-lines:])


class TestTheAppStarts(unittest.TestCase):
    """The failure itself: the module has to import, top to bottom."""

    def test_ui_app_imports_in_a_fresh_interpreter(self):
        proc = _python("import ui.app")
        self.assertEqual(proc.returncode, 0,
                         "ui/app.py does not import -- the app cannot start:\n" + _tail(proc))

    def test_the_hosted_branch_imports_too(self):
        """On Community Cloud `HOSTED` is true, and the module runs code at
        import that no local run executes -- including a write to Streamlit's
        internal config. It has to start there as well."""
        proc = _python("import ui.app as app; assert app.HOSTED, 'hosted branch not taken'",
                       hosted=True)
        self.assertEqual(proc.returncode, 0,
                         "ui/app.py does not import in hosted mode:\n" + _tail(proc))


class TestTheRerunGuardIsReal(unittest.TestCase):
    def test_the_guard_is_active_on_this_streamlit(self):
        """A degraded guard keeps the app up, and must still fail the suite:
        otherwise a future move ships silently and the Task 2 bug returns."""
        import streamlit
        self.assertTrue(
            compat.RERUN_GUARD_ACTIVE,
            f"RerunException not found on Streamlit {streamlit.__version__} in any of "
            f"{compat.RERUN_EXCEPTION_CANDIDATES}; find where it moved and add it.")

    def test_it_is_the_class_streamlits_script_runner_handles(self):
        """Catching a class of the right name is not enough; it has to be the
        one Streamlit's own script runner uses. Measured on 1.37.1 and 1.63.0:
        `streamlit.runtime.scriptrunner.script_runner` holds it on both."""
        try:
            runner = importlib.import_module("streamlit.runtime.scriptrunner.script_runner")
        except Exception:
            self.skipTest("script_runner is not where it was on 1.37.1-1.63.0")
        if not hasattr(runner, "RerunException"):
            self.skipTest("script_runner no longer names RerunException")
        self.assertIs(compat.RerunException, runner.RerunException)

    def test_ui_app_catches_the_compat_class(self):
        import ui.app
        self.assertIs(ui.app.RerunException, compat.RerunException)


class TestItDegradesInsteadOfCrashing(unittest.TestCase):
    """If Streamlit moves it again, the app must start without the guard."""

    def test_a_missing_module_is_skipped(self):
        self.assertEqual(compat.resolve("RerunException", ["no_such_module_for_this_test"]),
                         (None, None))

    def test_a_module_without_the_name_is_skipped(self):
        self.assertEqual(compat.resolve("RerunException", ["json"]), (None, None))

    def test_a_module_that_fails_while_importing_is_skipped(self):
        """A half-moved internal can raise something other than ImportError."""
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "compat_probe_broken.py").write_text(
                "raise RuntimeError('half-moved internal')\n", encoding="utf-8")
            sys.path.insert(0, tmp)
            try:
                self.assertEqual(
                    compat.resolve("RerunException", ["compat_probe_broken", "json"]),
                    (None, None))
            finally:
                sys.path.remove(tmp)
                sys.modules.pop("compat_probe_broken", None)

    def test_the_app_starts_when_no_candidate_provides_it(self):
        """The end-to-end claim. Every candidate is made unimportable for the
        shim only -- Streamlit's own imports are untouched -- then the app is
        imported and its guard exercised with the placeholder."""
        proc = _python(f"""
            import importlib
            _real = importlib.import_module
            _blocked = set({compat.RERUN_EXCEPTION_CANDIDATES!r})
            def _import_module(name, package=None):
                if name in _blocked:
                    raise ModuleNotFoundError(name)
                return _real(name, package)
            importlib.import_module = _import_module

            import ui.streamlit_compat as c
            assert not c.RERUN_GUARD_ACTIVE, "guard should be off"
            assert c.RerunException is c.RerunGuardUnavailable

            import ui.app as app
            holder, ui = app._rerun_guard()
            assert ui(lambda: 7) == 7, "a normal call must still work"
            try:
                ui(lambda: (_ for _ in ()).throw(ValueError("boom")))
            except ValueError:
                pass
            else:
                raise SystemExit("a genuine error was swallowed")
            print("DEGRADED-AND-RUNNING")
        """)
        self.assertEqual(proc.returncode, 0, _tail(proc))
        self.assertIn("DEGRADED-AND-RUNNING", proc.stdout)
        self.assertIn("mid-run interaction guard is OFF", proc.stderr,
                      "a disabled guard has to say so in the log")


#: Streamlit imports that are allowed outside the shim, and why.
_ALLOWED = {
    "streamlit": "the public API",
    "streamlit.components.v1": "public and documented",
    # Internal. Used once, for a server option with no public setter; the
    # module and `set_option` exist unchanged on 1.37.1 and 1.63.0, and the
    # hosted import test above executes the call. Listed here so a new use is
    # a decision rather than an accident.
    "streamlit.config": "internal, verified on 1.37.1 and 1.63.0",
}
#: Files allowed to name internals: the shim itself, and this test, which checks
#: the shim's class against the one Streamlit's own script runner holds.
_EXEMPT = {Path("ui") / "streamlit_compat.py", Path("tests") / "test_streamlit_compat.py"}
_SCANNED = ("agents", "core", "skills", "ui", "utils", "tests")


class TestNoStreamlitInternalsOutsideTheShim(unittest.TestCase):
    """The outage was an internal import nobody had listed. This lists them, by
    reading every module's imports, and fails on any new one."""

    def test_every_streamlit_import_is_public_or_allowed(self):
        offenders = []
        for top in _SCANNED:
            for path in sorted((ROOT / top).rglob("*.py")):
                rel = path.relative_to(ROOT)
                if rel in _EXEMPT:
                    continue
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(rel))
                for node in ast.walk(tree):
                    modules = []
                    if isinstance(node, ast.Import):
                        modules = [a.name for a in node.names]
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        if node.module == "streamlit":
                            modules = [f"streamlit.{a.name}" for a in node.names]
                        else:
                            modules = [node.module]
                    elif (isinstance(node, ast.Call) and node.args
                          and isinstance(node.args[0], ast.Constant)
                          and isinstance(node.args[0].value, str)
                          and getattr(node.func, "attr", getattr(node.func, "id", ""))
                          in ("import_module", "__import__")):
                        modules = [node.args[0].value]
                    for module in modules:
                        if module.split(".")[0] != "streamlit" or module in _ALLOWED:
                            continue
                        offenders.append(f"{rel}:{node.lineno}  {module}")
        self.assertEqual(
            offenders, [],
            "Streamlit internals imported outside ui/streamlit_compat.py. They move "
            "between releases; route them through the shim:\n  " + "\n  ".join(offenders))


if __name__ == "__main__":
    unittest.main(verbosity=2)

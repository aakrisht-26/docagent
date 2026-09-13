"""Streamlit internals this app needs, imported defensively.

Streamlit does not publish `RerunException`. It is the control-flow exception
Streamlit raises at the next `st.*` call to service a queued widget
interaction, and `ui/app.py`'s rerun guard has to name it in order to defer it
(see `_rerun_guard`). Its module has MOVED between releases, and a hard import
of the old path took the deployed app down at import time with a
ModuleNotFoundError:

    streamlit.runtime.scriptrunner.exceptions         defines it on 1.37.1
                                                      (this machine); absent
                                                      on 1.63.0
    streamlit.runtime.scriptrunner_utils.exceptions   defines it on 1.63.0
                                                      (Community Cloud); the
                                                      package is absent on 1.37.1
    streamlit.runtime.scriptrunner                    re-exports it on both

Measured by importing each path on both versions, not recalled. Cloud resolved
`streamlit>=1.35.0` to 1.63.0 -- the `index.ByR4Z2EF.js` it serves is the one
in the 1.63.0 wheel -- while local tests ran 1.37.1.

So every candidate is tried, and any failure while importing one (not only
ImportError: a half-moved internal can fail in other ways) moves on to the
next. If none yields the class, the guard DEGRADES: `RerunException` becomes a
placeholder Streamlit never raises, so `except RerunException` catches nothing
and a mid-run interaction aborts the run, exactly as it did before the guard
existed. That is a worse app, not a broken one -- a rerun guard that takes the
whole app down is worse than no guard. `RERUN_GUARD_ACTIVE` says which state
this process is in, and `tests/test_streamlit_compat.py` fails loudly when it is
False, so a future move is caught by the suite rather than in production.
"""

from __future__ import annotations

import importlib
import logging
from typing import Iterable, Optional, Tuple

logger = logging.getLogger(__name__)

#: Where `RerunException` has lived. The defining modules come first so the
#: source reported is the real one; the package re-export is the backstop.
RERUN_EXCEPTION_CANDIDATES = (
    "streamlit.runtime.scriptrunner_utils.exceptions",
    "streamlit.runtime.scriptrunner.exceptions",
    "streamlit.runtime.scriptrunner",
)

#: Where `RerunData` has lived. Only the tests construct one.
RERUN_DATA_CANDIDATES = (
    "streamlit.runtime.scriptrunner_utils.script_requests",
    "streamlit.runtime.scriptrunner.script_requests",
    "streamlit.runtime.scriptrunner",
)


class RerunGuardUnavailable(BaseException):
    """Stands in for `RerunException` when no candidate provides it.

    Streamlit never raises this, so an `except` naming it catches nothing and
    reruns propagate as if the guard were not there. A `BaseException`, like
    the real one, so the `except` clauses that name it stay valid Python.
    """


def resolve(name: str, candidates: Iterable[str]) -> Tuple[Optional[object], Optional[str]]:
    """Return (`name`, module path) from the first candidate that has it.

    Returns (None, None) when none does. Never raises: an import that fails
    for any reason is logged at debug level and skipped.
    """
    for module_path in candidates:
        try:
            module = importlib.import_module(module_path)
        except Exception as exc:
            logger.debug("streamlit_compat: %s not importable (%s: %s)",
                         module_path, type(exc).__name__, exc)
            continue
        found = getattr(module, name, None)
        if found is not None:
            return found, module_path
    return None, None


_found, RERUN_EXCEPTION_SOURCE = resolve("RerunException", RERUN_EXCEPTION_CANDIDATES)

#: True when the real exception was found and the rerun guard can defer it.
RERUN_GUARD_ACTIVE: bool = isinstance(_found, type) and issubclass(_found, BaseException)

RerunException = _found if RERUN_GUARD_ACTIVE else RerunGuardUnavailable

if not RERUN_GUARD_ACTIVE:
    RERUN_EXCEPTION_SOURCE = None
    logger.warning(
        "Streamlit's RerunException was not found in any known location (%s). "
        "The app will run, but the mid-run interaction guard is OFF: touching a "
        "widget during an analysis will abort it. Add the new location to "
        "ui/streamlit_compat.py.",
        ", ".join(RERUN_EXCEPTION_CANDIDATES),
    )

RerunData, RERUN_DATA_SOURCE = resolve("RerunData", RERUN_DATA_CANDIDATES)

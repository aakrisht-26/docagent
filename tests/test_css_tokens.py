"""Guards against CSS custom properties that are referenced but never defined.

This exists because of a real miss. The theme rebuild renamed `--accent-light`
out of existence, and a brace-balance check over `custom.css` reported the
stylesheet clean — but three `var(--accent-light)` references lived in inline
`style="…"` attributes inside `results_view.py`, which that check never read.
An undefined custom property does not error: the declaration is simply dropped
and the element silently falls back to an inherited colour.

The failure mode is the same one DEPENDENCIES.md section 5 describes for
Streamlit's test IDs — styling that quietly stops applying — so it gets the
same treatment: an automated check rather than a promise to remember.

Run:
    pytest tests/test_css_tokens.py -v
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STYLESHEET = REPO_ROOT / "ui" / "styles" / "custom.css"

# Definitions live in the dark token block in custom.css and the light override
# block in app.py. Both are `--name: value` inside a `:root { … }`.
_DEFINITION = re.compile(r"^\s*(--[a-z0-9-]+)\s*:", re.MULTILINE)
_REFERENCE = re.compile(r"var\(\s*(--[a-z0-9-]+)")

# Set at runtime by JavaScript via style.setProperty(), so it is never declared
# in a stylesheet. Both uses supply a fallback, e.g. var(--pct, 28.5%).
_RUNTIME_DEFINED = {"--pct"}


def _sources() -> list[Path]:
    """Every file that can emit a var() reference: the stylesheet and the UI."""
    return [STYLESHEET, *sorted((REPO_ROOT / "ui").rglob("*.py"))]


def _strip_css_comments(text: str) -> str:
    """Drop /* … */ blocks so prose about tokens is not read as code."""
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


class TestCssTokens(unittest.TestCase):
    def setUp(self):
        self.defined = set()
        for path in (STYLESHEET, REPO_ROOT / "ui" / "app.py"):
            self.defined |= set(_DEFINITION.findall(_strip_css_comments(
                path.read_text(encoding="utf-8"))))
        self.defined |= _RUNTIME_DEFINED

    def test_stylesheet_defines_tokens(self):
        """Guard the guard: a bad path would make every other test vacuous."""
        self.assertTrue(STYLESHEET.is_file(), f"missing {STYLESHEET}")
        self.assertGreater(len(self.defined), 20,
                           "expected a full token block; found almost none")

    def test_every_referenced_token_is_defined(self):
        dangling: dict[str, list[str]] = {}
        for path in _sources():
            text = _strip_css_comments(path.read_text(encoding="utf-8"))
            for token in sorted(set(_REFERENCE.findall(text))):
                if token not in self.defined:
                    rel = path.relative_to(REPO_ROOT).as_posix()
                    dangling.setdefault(token, []).append(rel)

        self.assertEqual(
            dangling, {},
            "var() references with no matching definition — these silently "
            f"do nothing at runtime: {dangling}",
        )

    def test_light_theme_overrides_only_known_tokens(self):
        """A typo in the light block leaves that surface on the dark value."""
        app_py = (REPO_ROOT / "ui" / "app.py").read_text(encoding="utf-8")
        light = set(_DEFINITION.findall(_strip_css_comments(app_py)))
        css_tokens = set(_DEFINITION.findall(_strip_css_comments(
            STYLESHEET.read_text(encoding="utf-8"))))

        unknown = sorted(light - css_tokens)
        self.assertEqual(
            unknown, [],
            "the light theme defines tokens the dark base never declares, so "
            f"they override nothing: {unknown}",
        )

    def test_no_token_is_defined_as_itself(self):
        """`--x: var(--x)` resolves to nothing; a bulk rename can produce it."""
        text = _strip_css_comments(STYLESHEET.read_text(encoding="utf-8"))
        circular = [a for a, b in
                    re.findall(r"(--[a-z0-9-]+)\s*:\s*var\(\s*(--[a-z0-9-]+)\s*\)", text)
                    if a == b]
        self.assertEqual(circular, [], f"self-referential tokens: {circular}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

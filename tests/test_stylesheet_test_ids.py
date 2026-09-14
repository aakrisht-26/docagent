"""Every Streamlit test ID the stylesheet targets exists in the installed Streamlit.

A selector that stops matching fails silently: the surface falls back to
Streamlit's own colours, which config.toml pins dark, so the break usually shows
only in light mode. When the deployment ran 1.63.0 and this machine 1.37.1, the
code block, the button pairs and the page decoration rules all targeted IDs
1.63.0 no longer renders, and nothing here noticed.

This reads the frontend bundle shipped inside the installed streamlit package,
which tests/test_streamlit_compat.py holds equal to the pinned one. It proves an
ID exists in the release. It cannot prove the element is on a page, or that a
structural selector such as `> div > div` still lands on the right element, or
anything about class names or data-baseweb attributes; those were measured in
the browser (DEPENDENCIES.md section 5).
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

import streamlit

ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "ui" / "styles" / "custom.css"
BUNDLE_DIR = Path(streamlit.__file__).parent / "static" / "static" / "js"

# Rendered by every Streamlit page. If the reader cannot find these, the bundle
# format changed and an "absent" result would mean nothing.
_ALWAYS_RENDERED = ("stApp", "stSidebar", "stMarkdownContainer")


def _stylesheet_test_ids() -> set[str]:
    css = re.sub(r"/\*.*?\*/", "", CSS.read_text(encoding="utf-8"), flags=re.S)
    return set(re.findall(r'\[data-testid="([^"]+)"\]', css))


def _bundle() -> str:
    return "\n".join(p.read_text(encoding="utf-8", errors="ignore")
                     for p in sorted(BUNDLE_DIR.glob("*.js")))


def _in_bundle(test_id: str, bundle: str) -> bool:
    if re.search(rf"(?<![\w-]){re.escape(test_id)}(?![\w-])", bundle):
        return True
    # Button IDs are built at runtime as `stBaseButton-${kind}`.
    prefix, _, kind = test_id.partition("-")
    return (prefix == "stBaseButton" and "stBaseButton-${" in bundle
            and re.search(rf"[`\"']{re.escape(kind)}[`\"']", bundle) is not None)


class TestStylesheetTestIdsExistInTheInstalledStreamlit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = _bundle()
        cls.ids = _stylesheet_test_ids()

    def test_the_bundle_reader_finds_ids_every_page_renders(self):
        self.assertTrue(BUNDLE_DIR.is_dir(), BUNDLE_DIR)
        missing = [i for i in _ALWAYS_RENDERED if not _in_bundle(i, self.bundle)]
        self.assertEqual(missing, [], "the bundle format changed; this test would pass vacuously")

    def test_the_stylesheet_names_test_ids(self):
        self.assertGreater(len(self.ids), 20)

    def test_every_targeted_test_id_exists(self):
        missing = sorted(i for i in self.ids if not _in_bundle(i, self.bundle))
        self.assertEqual(
            missing, [],
            f"custom.css targets test IDs that Streamlit {streamlit.__version__} never "
            "renders, so those rules match nothing; find what the element is called "
            "now (DEPENDENCIES.md section 5)")


if __name__ == "__main__":
    unittest.main()

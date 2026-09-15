"""packages.txt stays exactly the two bare package names the deploy installs.

Streamlit Community Cloud passes every line of this file to `apt-get install`.
It broke a deploy once by carrying comments: `# ...` reached apt as a package
name and the build died with "Unable to locate package #". DEPLOYMENT.md says
the file must stay literal, and the build log of 2026-09-14 shows this exact
content installing ffmpeg and tesseract-ocr.

A blank line is rejected as well. Whether Cloud's installer skips one has never
been tested, and a file that is only ever two package names never needs to find
out. One final newline is allowed; it adds no line.

Run:
    pytest tests/test_packages_txt.py -v
"""

from __future__ import annotations

import unittest
from pathlib import Path

PACKAGES = Path(__file__).resolve().parents[1] / "packages.txt"


class TestPackagesTxtIsBarePackageNames(unittest.TestCase):
    def setUp(self):
        self.raw = PACKAGES.read_bytes()
        self.lines = self.raw.decode("utf-8").split("\n")
        if self.lines and self.lines[-1] == "":
            self.lines.pop()

    def test_there_are_no_carriage_returns(self):
        self.assertNotIn(b"\r", self.raw,
                         "a CRLF line reaches apt as 'ffmpeg\\r', which does not exist")

    def test_every_line_is_a_bare_package_name(self):
        for number, line in enumerate(self.lines, 1):
            with self.subTest(line=number):
                self.assertRegex(line, r"^[a-z0-9][a-z0-9.+-]*$",
                                 f"line {number} is {line!r}, and apt would receive "
                                 f"it as a package name")

    def test_it_lists_exactly_what_the_deploy_installs(self):
        self.assertEqual(self.lines, ["ffmpeg", "tesseract-ocr"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

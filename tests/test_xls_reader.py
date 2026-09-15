"""Legacy .xls: read by xlrd, the engine chosen by the file's bytes, and any
refusal said in words a user can act on.

WHY. .xls is advertised by the uploader and was never readable:
`ExcelReaderSkill` sent every non-CSV file to openpyxl, which cannot read BIFF8.
`tests/test_advertised_formats.py` records that finding.

WHY BY BYTES, NOT BY NAME. openpyxl refuses by EXTENSION before it reads a byte,
and spreadsheets in the wild are mislabelled. Measured on real files, each of
these failed with a message only a developer could read:

    an .xlsx renamed to .xls          "Excel xlsx file; not supported"
    a web page Excel saved as .xls    "Expected BOF record; found b'<html xm'"
    a truncated .xls                  "IndexError: array index out of range",
                                      after OLE2 warnings printed to stdout

WHY THE TEXT MUST MATCH .xlsx. xlrd returns every number as a float and every
date as a serial, so copied as they came 400000 read "400000.0". Everything
downstream reads the text -- the fabrication check, citations, retrieval -- so a
workbook has to read the same whichever format it was saved in. The paired
samples were written by Excel 16.0 in both formats from one workbook.

Run:
    pytest tests/test_xls_reader.py -v
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from core.models import SkillInput
from skills.excel_reader_skill import ExcelReaderSkill

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "tests" / "e2e" / "samples"


def _read(path: Path):
    return ExcelReaderSkill(config={}).safe_execute(SkillInput(data={"file_path": str(path)}))


def _sheets(path: Path):
    out = _read(path)
    assert out.success, out.error
    return out.data.sheet_names, [chunk.text for chunk in out.data.chunks]


class TestAnXlsReadsLikeTheSameWorkbookSavedAsXlsx:
    def test_the_sales_sample(self):
        assert _sheets(SAMPLES / "sample_sales.xls") == _sheets(SAMPLES / "sample_sales.xlsx")

    def test_dates_times_booleans_errors_formulas_and_a_merged_cell(self):
        """Each of these leaves xlrd in a different raw shape from openpyxl's,
        and an empty second sheet is kept by name and skipped by both."""
        assert _sheets(SAMPLES / "sample_types.xls") == _sheets(SAMPLES / "sample_types.xlsx")

    def test_whole_numbers_are_not_written_as_floats(self):
        text = _read(SAMPLES / "sample_sales.xls").data.full_text
        assert "400000" in text
        assert "400000.0" not in text


class TestTheEngineIsChosenByContent:
    def test_an_xlsx_named_xls_is_read(self, tmp_path):
        renamed = tmp_path / "sales.xls"
        renamed.write_bytes((SAMPLES / "sample_sales.xlsx").read_bytes())
        assert _sheets(renamed) == _sheets(SAMPLES / "sample_sales.xlsx")

    def test_a_web_page_named_xls_is_refused_in_plain_words(self, tmp_path):
        page = tmp_path / "export.xls"
        page.write_text('<html xmlns:o="urn:schemas-microsoft-com:office:office">'
                        "<body><table><tr><td>North</td></tr></table></body></html>",
                        encoding="utf-8")
        out = _read(page)
        assert not out.success
        assert out.error.startswith("'export.xls' has an Excel file name but is a web page")
        for jargon in ("openpyxl", "xlrd", "BOF", "Failed to parse"):
            assert jargon not in out.error

    def test_a_damaged_xls_is_refused_in_plain_words_and_quietly(self, tmp_path):
        """"Quietly" is checked in a fresh process. xlrd binds its default log
        stream when it is imported, which under pytest is pytest's own capture,
        so an in-process check could not see the warnings: a mutation removing
        the log sink passed it. Measured outside pytest, without the sink this
        truncated file prints 113 bytes of OLE2 warnings to stdout."""
        broken = tmp_path / "broken.xls"
        broken.write_bytes((SAMPLES / "sample_sales.xls").read_bytes()[:4096])
        out = _read(broken)
        assert not out.success
        assert out.error.startswith("'broken.xls' is a legacy .xls workbook that could not be read")
        script = ("import sys; sys.path.insert(0, sys.argv[1]); "
                  "from core.models import SkillInput; "
                  "from skills.excel_reader_skill import ExcelReaderSkill; "
                  "ExcelReaderSkill(config={}).safe_execute("
                  "SkillInput(data={'file_path': sys.argv[2]}))")
        proc = subprocess.run([sys.executable, "-c", script, str(ROOT), str(broken)],
                              capture_output=True, text=True, timeout=120)
        assert "OLE2" not in proc.stdout, "xlrd's own warnings reached stdout"

    def test_a_password_protected_xls_says_so(self):
        """Written by Excel 16.0 with a password on open."""
        out = _read(SAMPLES / "sample_protected.xls")
        assert not out.success
        assert "password-protected" in out.error


class TestTheUserIsToldWhy:
    def test_the_pipeline_carries_the_reason_into_its_error(self, tmp_path):
        """"Parsing failed" was the whole of the error box for a .xls upload."""
        from agents.document_agent import DocumentAgent

        page = tmp_path / "export.xls"
        page.write_text("<html><body><table></table></body></html>", encoding="utf-8")
        result = DocumentAgent(config={"groq": {"enabled": False}}).run(page)
        assert not result.success
        assert result.errors[0].startswith("Parsing failed: 'export.xls' has an Excel file name")

    def test_a_real_xls_runs_through_the_whole_pipeline(self):
        from agents.document_agent import DocumentAgent

        result = DocumentAgent(config={"groq": {"enabled": False}}).run(SAMPLES / "sample_sales.xls")
        assert result.success, result.errors
        assert result.file_type == "excel"
        assert "Orion Controller" in result.raw_text


def test_xlrd_is_declared_not_just_installed():
    """xlrd was on the development machine, required by nothing and missing from
    requirements.txt, so this machine would have hidden a missing dependency
    until the hosted build ran without it."""
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert re.search(r"^xlrd[<>=]", requirements, re.M)

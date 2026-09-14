"""Tests for withholding patient identifiers after extraction.

WHY IT EXISTS. The Healthcare schema's `patient_id` said "anonymise if present".
Measured at temperature 0.0 with the cache off, 10 draws each:

    ward census sheet      patient_id leaked 10/10, an MRN in some field 9/10
    prose discharge note   patient_id leaked  6/10

On the sheet 5 draws prefixed nearly every field with an MRN, e.g. medications
"55-40182: Amoxicillin 500 mg TDS (Active); ...". The worst is pinned below.

WHY THE PROMPT CHANGE IS NOT ENOUGH ON ITS OWN. Rewording the field as
"WITHHELD: always null" took both fixtures to 0/50. On a rewording of the sheet
it did not reach zero: with the identifier column headed "Patient ID", 1 of 16
draws returned `patient_id` null and wrote "(patient 55-40182)" into `dates` for
all three patients. The instruction held the field and not the value. That draw
is pinned below too, so both claims are re-derivable without an API call.

WHY IDENTIFIERS ARE READ FROM THE DOCUMENT. In that draw the withheld field was
empty, so nothing the model returned said what to remove. The document does: a
label with a value beside it, or a label heading a column.

Run:
    pytest tests/test_extraction_withholding.py -v
"""

from __future__ import annotations

import importlib.util
import json
import re
import unittest
from pathlib import Path

import pandas as pd

from skills.structured_extraction_skill import (
    _DOMAIN_SCHEMAS, _WITHHELD_FIELDS, StructuredExtractionSkill, identifiers_in,
)

ROOT = Path(__file__).resolve().parents[1]
HEALTH = _DOMAIN_SCHEMAS["Healthcare"]
MRNS = ["55-40182", "55-40183", "55-40184"]


def _eval_documents():
    """The extraction eval's documents, loaded by path under a private name, so
    nothing called `fixture_content` is left on sys.path or in sys.modules to
    shadow the retrieval eval's module in later tests."""
    path = ROOT / "tests" / "e2e" / "extraction_eval" / "fixture_content.py"
    spec = importlib.util.spec_from_file_location("_withholding_fixtures", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.DOCUMENTS


DOCUMENTS = _eval_documents()
SHEET = DOCUMENTS["health_census_sheet"]
NOTE = DOCUMENTS["health_note"]


def _census(header: str, ids, last: bool) -> str:
    """A census rendered the way ExcelReaderSkill renders one."""
    df = pd.DataFrame({
        "Diagnosis": ["Community-acquired", "Cellulitis", "COPD"],
        "Consultant": ["Alanna Whitcombe", "Alanna Whitcombe", "Peter Ngoma"],
    })
    if last:
        df[header] = ids
    else:
        df.insert(0, header, ids)
    return f"[Sheet: Ward Census]\n- Total Rows: 3\n\n{df.to_string(index=False)}\n"


def _squashed(text: str) -> str:
    return re.sub(r"[\s/-]", "", text)


def _carries_an_mrn(value) -> bool:
    flat = _squashed(json.dumps(value, default=str))
    return any(_squashed(m) in flat for m in MRNS)


#: The worst draw recorded before the schema change: an MRN in all seven fields.
BEFORE_WORST = {
    "patient_id": "55-40182, 55-40183, 55-40184",
    "diagnoses": "55-40182: Community-acquired; 55-40183: Cellulitis; 55-40184: COPD",
    "medications": "55-40182: Amoxicillin 500 mg TDS (Active); Ibuprofen 400 mg PRN "
                   "(Stopped); Metformin 850 mg BD (Active)",
    "procedures": "55-40182: CT pulmonary angiogram (2026-05-05); Bronchoscopy with "
                  "lavage (2026-05-06)",
    "dates": "55-40182 Admission: 2026-05-04, Discharge: 2026-05-11; 55-40183 Admission: "
             "2026-05-06, Discharge: 2026-05-13; 55-40184 Admission: 2026-05-07, "
             "Discharge: 2026-05-12; Procedures: 2026-05-05, 2026-05-06",
    "physician": "55-40182: Alanna Whitcombe; 55-40183: Alanna Whitcombe; 55-40184: Peter Ngoma",
    "allergies": "55-40182: Penicillin; 55-40183: None; 55-40184: Latex",
}

#: The draw that beat the reworded prompt: patient_id null, the MRNs in `dates`.
AFTER_PROMPT_LEAK = {
    "diagnoses": "Community-acquired; Cellulitis; COPD",
    "medications": "Amoxicillin 500 mg TDS; Metformin 850 mg BD",
    "procedures": "CT pulmonary angiogram (2026-05-05); Bronchoscopy with lavagechir (2026-05-06)",
    "dates": "2026-05-04 admission, 2026-05-11 discharge (patient 55-40182); 2026-05-06 "
             "admission, 2026-05-13 discharge (patient 55-40183); 2026-05-07 admission, "
             "2026-05-12 discharge (patient 55-40184); 2026-05-05 procedure; 2026-05-06 procedure",
    "physician": "Alanna Whitcombe; Peter Ngoma",
    "allergies": "Penicillin; Latex",
}

#: A prose draw from before the change: the MRN only in patient_id.
BEFORE_PROSE = {
    "patient_id": "MRN 55-40182",
    "diagnoses": "community-acquired pneumonia, type 2 diabetes mellitus",
    "medications": "amoxicillin 500 mg three times daily for 5 days, metformin 850 mg twice daily",
    "physician": "Dr Alanna Whitcombe",
    "allergies": "penicillin V causes a documented rash",
}


class TestIdentifiersAreReadFromTheDocument(unittest.TestCase):
    def test_the_census_column(self):
        self.assertEqual(identifiers_in(SHEET), MRNS)

    def test_the_labelled_line_in_the_discharge_note(self):
        self.assertEqual(identifiers_in(NOTE), ["55-40182"])

    def test_a_column_headed_patient_id(self):
        """The rewording that beat the prompt."""
        self.assertEqual(identifiers_in(_census("Patient ID", MRNS, last=False)), MRNS)

    def test_a_hospital_number_column_at_the_end(self):
        ids = ["H5540182", "H5540183", "H5540184"]
        self.assertEqual(identifiers_in(_census("Hospital No", ids, last=True)), ids)

    def test_a_spaced_nhs_number(self):
        self.assertEqual(identifiers_in("NHS number: 485 777 3456\nAttending: Dr Whitcombe"),
                         ["485 777 3456"])

    def test_an_inline_mrn(self):
        self.assertEqual(
            identifiers_in("The patient (MRN 55-40182) was admitted on 4 May 2026."),
            ["55-40182"])

    def test_nothing_is_found_where_no_identifier_is_labelled(self):
        """Measured the same way across 43 retrieval texts and nine parsed e2e
        samples; these six are the ones cheap enough to pin."""
        for key in ("fin_quarterly", "legal_msa", "research_paper", "general_ops",
                    "fin_sales_sheet", "legal_register_sheet"):
            with self.subTest(document=key):
                self.assertEqual(identifiers_in(DOCUMENTS[key]), [])

    def test_a_date_or_a_small_number_under_a_label_is_not_an_identifier(self):
        for text in ("MRN\n2026-05-04\n", "Patient number: 12", "MRN 04/05/2026"):
            with self.subTest(text=text):
                self.assertEqual(identifiers_in(text), [])


class TestWithholding(unittest.TestCase):
    def setUp(self):
        self.skill = StructuredExtractionSkill(config={})

    def test_the_worst_recorded_draw_leaves_no_identifier(self):
        cleaned, _note = self.skill._withhold(BEFORE_WORST, SHEET, HEALTH)
        self.assertNotIn("patient_id", cleaned)
        for field, value in cleaned.items():
            with self.subTest(field=field):
                self.assertFalse(_carries_an_mrn(value))
        self.assertTrue(cleaned["medications"].startswith("[withheld]: Amoxicillin 500 mg"))
        self.assertEqual(set(cleaned), set(BEFORE_WORST) - {"patient_id"})

    def test_the_draw_that_beat_the_prompt(self):
        """patient_id was already null, so only the document said what to remove."""
        source = _census("Patient ID", MRNS, last=False)
        cleaned, note = self.skill._withhold(AFTER_PROMPT_LEAK, source, HEALTH)
        self.assertFalse(_carries_an_mrn(cleaned))
        self.assertIn("2026-05-04 admission, 2026-05-11 discharge (patient [withheld])",
                      cleaned["dates"])
        for field in set(AFTER_PROMPT_LEAK) - {"dates"}:
            self.assertEqual(cleaned[field], AFTER_PROMPT_LEAK[field])
        self.assertIn("dates", note)

    def test_fields_without_an_identifier_are_untouched(self):
        cleaned, _note = self.skill._withhold(BEFORE_PROSE, NOTE, HEALTH)
        self.assertEqual(cleaned, {k: v for k, v in BEFORE_PROSE.items() if k != "patient_id"})

    def test_a_repunctuated_identifier_is_still_caught(self):
        cleaned, _note = self.skill._withhold(
            {"dates": "5540182 admitted 2026-05-04", "allergies": "55 40183 latex"},
            SHEET, HEALTH)
        self.assertEqual(cleaned, {"dates": "[withheld] admitted 2026-05-04",
                                   "allergies": "[withheld] latex"})

    def test_a_value_that_was_only_an_identifier_is_dropped(self):
        cleaned, _note = self.skill._withhold(
            {"diagnoses": ["55-40182", "Cellulitis"], "physician": "55-40183"}, SHEET, HEALTH)
        self.assertEqual(cleaned, {"diagnoses": ["Cellulitis"]})

    def test_nested_shapes_and_numbers(self):
        entities = {"medications": [{"55-40182": "Amoxicillin"},
                                    {"patient": 5540183, "drug": "Metformin"}]}
        cleaned, _note = self.skill._withhold(entities, SHEET, HEALTH)
        self.assertFalse(_carries_an_mrn(cleaned))
        self.assertIn("Amoxicillin", json.dumps(cleaned))
        self.assertIn("Metformin", json.dumps(cleaned))

    def test_an_identifier_only_the_model_announced(self):
        """A label this module does not know ("URN"), but the model put the value
        in the withheld field and that value is in the document."""
        source = "Case reference URN 88231\nAmoxicillin 500 mg"
        cleaned, _note = self.skill._withhold(
            {"patient_id": "URN 88231", "medications": "88231: Amoxicillin 500 mg"},
            source, HEALTH)
        self.assertEqual(cleaned, {"medications": "[withheld]: Amoxicillin 500 mg"})

    def test_the_warning_never_repeats_the_identifier(self):
        """It is shown in the UI and exported with the report."""
        _cleaned, note = self.skill._withhold(BEFORE_WORST, SHEET, HEALTH)
        self.assertIn("patient_id", note)
        self.assertIn("medications", note)
        self.assertFalse(_carries_an_mrn(note))

    def test_nothing_withheld_means_no_warning(self):
        cleaned, note = self.skill._withhold({"allergies": "Penicillin"}, NOTE, HEALTH)
        self.assertEqual(cleaned, {"allergies": "Penicillin"})
        self.assertIsNone(note)

    def test_schemas_without_a_withheld_field_are_not_touched(self):
        entities = {"parties": "55-40182 Northwind", "dates": "55-40183"}
        for name in ("Financial", "Legal", "Research", "General"):
            with self.subTest(schema=name):
                cleaned, note = self.skill._withhold(entities, SHEET, _DOMAIN_SCHEMAS[name])
                self.assertEqual(cleaned, entities)
                self.assertIsNone(note)


class TestTheExtractionPathWithholds(unittest.TestCase):
    """Through `_llm_extract`, with the model's reply supplied."""

    def _extract(self, reply: dict):
        skill = StructuredExtractionSkill(config={})
        skill._llm.chat = lambda **kw: json.dumps(reply)
        skill._llm._last_finish_reason = "stop"
        skill._llm._last_failure = None
        return skill._llm_extract(SHEET, "normal_document", "Healthcare", HEALTH)

    def test_withholding_runs_before_the_fabrication_check_can_name_the_identifier(self):
        """"5540182" is a figure the document never writes, so the fabrication
        check would drop it AND print it in its warning. Withholding first turns
        it into the mark before that check sees it."""
        entities, method, warning = self._extract(
            {"patient_id": None, "dates": "5540182 admitted", "medications": "Amoxicillin 500 mg"})
        self.assertTrue(method.startswith("llm_"))
        self.assertEqual(entities, {"dates": "[withheld] admitted",
                                    "medications": "Amoxicillin 500 mg"})
        self.assertFalse(_carries_an_mrn(warning or ""))

    def test_a_reply_holding_only_the_withheld_field(self):
        entities, method, warning = self._extract({"patient_id": "55-40182"})
        self.assertEqual(entities, {})
        self.assertEqual(method, "no_fields_found")
        self.assertIn("withheld patient_id", warning)
        self.assertFalse(_carries_an_mrn(warning))


class TestTheSchemaWording(unittest.TestCase):
    """Both measured fixes live partly in field descriptions, so a quiet revert of
    the wording would undo them with nothing else failing."""

    def test_every_withheld_field_tells_the_model_so(self):
        for field in _WITHHELD_FIELDS:
            carriers = [s for s in _DOMAIN_SCHEMAS.values() if field in s]
            self.assertTrue(carriers)
            for schema in carriers:
                self.assertIn("WITHHELD", schema[field])

    def test_medications_are_current_and_exclude_stopped_drugs(self):
        """"Prescribed medications" admitted a drug whose Status column read
        Stopped, 10/10 on the census sheet. A stopped drug WAS prescribed, so the
        model was answering the field as written. "Current", with stopped,
        discontinued and held named, measured 0/50 there and 0/32 on two sheet
        rewordings."""
        text = HEALTH["medications"].lower()
        self.assertIn("current", text)
        for word in ("stopped", "discontinued", "held"):
            self.assertIn(word, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)

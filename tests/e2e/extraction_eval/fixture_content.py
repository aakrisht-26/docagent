"""Documents for the structured-extraction eval, built to be hard on purpose.

THE POINT OF THESE FIXTURES IS THE DISTRACTORS. The existing e2e samples each
carry one obvious candidate per field: exactly one revenue figure, one date,
one company. An extractor that grabs the first plausible token scores full
marks on them, which is why measuring against those samples would have produced
a saturated number and told us nothing.

Every scored field here has at least one NEAR MISS in the same document —
a figure or name that is the right shape, sits near the right words, and is the
wrong answer:

    revenue          412.7 is the answer; 388.1 is LAST YEAR's revenue, 430-445
                     is GUIDANCE, and 96.4 is one SEGMENT's revenue.
    net_income       38.9 is the answer; 61.2 is operating income and 180.3 is
                     gross profit, both larger and both nearer the word
                     "income" in the text.
    eps              2.14 is diluted; 2.21 is basic and appears first.
    effective_date   1 March 2026; the document also carries a SIGNATURE date
                     and an AMENDMENT date, both formatted identically.
    parties          two contracting parties; a guarantor and two law firms are
                     named in the same paragraph.
    governing_law    England and Wales; New York appears as the ARBITRATION
                     SEAT two sentences later.
    diagnoses        the patient's; the note also lists FAMILY HISTORY
                     conditions and a RULED OUT differential.
    medications      current; one drug is explicitly DISCONTINUED.
    authors          this paper's; four cited papers carry their own authors.
    datasets         the one used; two more are named as RELATED WORK.

The eval set records those near misses as `must_not_contain`, so an answer that
hedges by listing every candidate scores WRONG rather than partially right.
That is deliberate: a field extracted wrongly is worse than a field not
extracted, and an extractor that dumps all candidates is not extracting.

Numbers are arbitrary but internally consistent. Each fact appears once.
"""

from __future__ import annotations

# ── Financial ────────────────────────────────────────────────────────────────
FIN_QUARTERLY = """Meridian Components Incorporated
Quarterly Report - Third Quarter, Fiscal Year 2025

Prepared for the board and for filing. This report covers the three months
ending 30 September 2025. Comparative figures for the second quarter of fiscal
2025 and for the full fiscal year 2024 are given where useful.

Consolidated results

Total revenue for the quarter was 412.7 million dollars. In the same quarter of
the prior year the company recorded revenue of 388.1 million dollars, so the
year-on-year increase is 6.3 percent. Revenue in the second quarter of fiscal
2025 was 401.2 million dollars.

Gross profit was 180.3 million dollars. Operating income was 61.2 million
dollars after research spending of 44.8 million dollars. Net income for the
quarter was 38.9 million dollars, which is the figure carried to the statement
of retained earnings.

Earnings per share are reported on two bases. Basic earnings per share were
2.21 dollars. Diluted earnings per share were 2.14 dollars, reflecting the
convertible notes issued in March.

Segment detail

The Industrial Sensors segment contributed revenue of 214.5 million dollars.
The Precision Optics segment contributed revenue of 101.8 million dollars. The
Legacy Instruments segment contributed revenue of 96.4 million dollars and
continues to decline.

Outlook

For the fourth quarter of fiscal 2025 the company expects revenue between 430
and 445 million dollars. Management reaffirms the full-year operating margin
target of 15 percent.

Risk factors

The board draws attention to three risks. Concentration of supply: a single
foundry in Taiwan produces 71 percent of the sensor dies. Currency exposure:
roughly 40 percent of receivables are denominated in euros. Regulatory change:
proposed export controls could restrict sales of the Precision Optics range.
"""

# ── Legal ────────────────────────────────────────────────────────────────────
LEGAL_MSA = """MASTER SERVICES AGREEMENT

This Master Services Agreement is made between Northwind Logistics Limited, a
company registered in England, and Calder Freight Services GmbH, a company
registered in Germany. Barrowfield Holdings Limited joins this agreement solely
as guarantor of the obligations of Calder Freight Services GmbH and is not a
contracting party for any other purpose.

The parties were advised by Hensleigh and Root LLP and by Draycott Vane
Rechtsanwaelte respectively. Neither firm is a party to this agreement.

Execution and term

This agreement was signed by the authorised representatives on 14 February
2026. The effective date of this agreement is 1 March 2026. The agreement
expires on 28 February 2029 unless renewed in writing. An amendment to Schedule
2 was executed on 3 June 2026 and does not alter the effective date.

Governing law and disputes

This agreement is governed by the laws of England and Wales. The parties agree
that any dispute shall be referred to arbitration, and that the seat of the
arbitration shall be New York, conducted in English under the rules of the
London Court of International Arbitration.

Obligations

Northwind Logistics Limited shall provide warehousing capacity of not less than
14,000 pallet positions and shall maintain insurance of at least 5 million
pounds. Calder Freight Services GmbH shall present a rolling twelve-week
forecast and shall pay all correctly rendered invoices within 45 days.

Liquidated damages

Where Northwind Logistics Limited fails to meet the agreed dispatch window, it
shall pay liquidated damages of 0.5 percent of the monthly service charge per
week of delay, capped at 8 percent of the annual charge. A separate service
credit of 2 percent applies to reporting failures and is not a penalty.
"""

# ── Research ─────────────────────────────────────────────────────────────────
RESEARCH_PAPER = """Thermal Drift Compensation in Low-Cost MEMS Accelerometers

Priya Raghunathan, Tomas Eklund and Miriam Osei-Bonsu
Department of Instrumentation, Ardleigh Institute of Technology

Abstract

We ask whether a per-device polynomial correction fitted at manufacture can
reduce thermal drift in low-cost MEMS accelerometers enough to make them usable
for structural monitoring without active temperature control. Our hypothesis is
that a third-order correction fitted over the range 0 to 60 degrees Celsius
removes most of the drift attributable to temperature.

Related work

Fielded correction schemes were surveyed by Nakamura and Vestergaard, whose
review remains the standard reference. Adaptive Kalman approaches were proposed
by Oyelaran, Sindhu and Marchetti. Both the CALTRANS-BRIDGE corpus and the
Helsinki Structural Archive have been used for this kind of evaluation in
earlier work.

Method

We instrumented 48 devices from three production lots and cycled each through
the temperature range in a controlled chamber. Drift was measured against a
navigation-grade reference. Corrections were fitted on half the devices and
validated on the other half. All measurements come from the Ardleigh MEMS Drift
Set, which we collected for this study and release with the paper.

Results

Mean drift fell from 41.3 milli-g to 6.8 milli-g across the validation devices.
The correction was least effective below 8 degrees Celsius, where residual
drift remained above 20 milli-g.

Limitations

The study used devices from a single manufacturer, and all three production
lots were made within one calendar quarter. We did not test humidity effects.
Results may therefore not transfer to other packaging processes.
"""

# ── Healthcare (synthetic) ───────────────────────────────────────────────────
HEALTH_NOTE = """DISCHARGE SUMMARY - SYNTHETIC RECORD FOR TESTING

Patient identifier: MRN 55-40182
Attending physician: Dr Alanna Whitcombe
Referring physician: Dr Peter Ngoma, who is not responsible for inpatient care.

Admission date: 4 May 2026. Discharge date: 11 May 2026. A follow-up clinic
appointment is booked for 2 June 2026.

Diagnoses on discharge: community-acquired pneumonia, and type 2 diabetes
mellitus. A pulmonary embolism was considered and RULED OUT by CT pulmonary
angiogram. Family history includes coronary artery disease in the patient's
father and asthma in a sibling; neither is a diagnosis for this patient.

Procedures performed: CT pulmonary angiogram on 5 May 2026, and bronchoscopy
with lavage on 6 May 2026.

Medications on discharge: amoxicillin 500 mg three times daily for 5 days, and
metformin 850 mg twice daily. Ibuprofen was administered on admission and has
been DISCONTINUED; it should not be continued at home.

Allergies: penicillin V causes a documented rash. No other known allergies.
Note that the amoxicillin above was given under specialist supervision.
"""

# ── General ──────────────────────────────────────────────────────────────────
GENERAL_OPS = """Quarterly Operations Note - Halloway Manufacturing Group

Prepared by Ines Calderon, Director of Operations, for circulation to the
management committee on 12 March 2026.

Halloway Manufacturing Group operates plants in Coventry and in Gdansk. A third
site in Porto was evaluated during the quarter and was NOT acquired; the option
lapsed on 28 February 2026. Goods move through the port of Rotterdam, which is
a transit point and not a company location.

Ines Calderon and the plant manager at Coventry, Samuel Oyelowo, jointly
reviewed the capital plan. The review cited work by the consultancy Brandt and
Fielding, whose analysts were not employees of the group.

Capital spending in the quarter was 2.4 million pounds. A further 6.1 million
pounds is the approved budget for the full year and has not yet been spent. The
Porto option that lapsed had carried an indicative price of 11.5 million pounds.

Output at Coventry rose to 18,400 units, against 16,900 units in the previous
quarter. Absence stood at 3.1 percent.
"""

DOCUMENTS = {
    "fin_quarterly":   FIN_QUARTERLY,
    "legal_msa":       LEGAL_MSA,
    "research_paper":  RESEARCH_PAPER,
    "health_note":     HEALTH_NOTE,
    "general_ops":     GENERAL_OPS,
}

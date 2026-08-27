"""Content for the large RAG-eval fixtures.

Kept separate from make_samples.py because the *design* of this text is the
experiment. A 20-page document whose pages each cover an unrelated topic makes
retrieval trivial at a larger scale — any method that reads the question at all
scores 100%. These pages are therefore built with three deliberate difficulties:

1. HEAVY VOCABULARY OVERLAP — four "Fleet Maintenance" pages share almost all
   their wording, and one distractor repeats the exact phrase a question uses
   more often than the page that actually holds the answer. Surface-word
   counting should prefer the wrong page.

2. CROSS-PAGE ANSWERS — the Tier 1 incident register is split across two pages,
   with the counts on one and the resolution times on the next. Neither page
   answers the question alone.

3. SYNONYM-ONLY PHRASING — several pages describe a fact using vocabulary that
   shares no content word with the natural question ("off-road hours" versus
   "out of service", "attrition" versus "turnover", "lead time" versus "how long
   to buy").

Numbers are arbitrary but internally consistent, and each fact appears on
exactly one page unless the question is deliberately cross-page.
"""

from __future__ import annotations

# ── 20-page PDF: (heading, body) per page ────────────────────────────────────
#
# Page numbers below are 1-based and are what the eval set refers to.

LARGE_PDF_TITLE = "Meridian Freight Systems — Annual Operations Review"

LARGE_PDF_PAGES = [
    # 1
    ("1. Executive Summary",
     "Meridian Freight Systems closed the year with revenue of 148.6 million dollars "
     "across four regional depots. The organisation operates 612 heavy goods vehicles "
     "and employs 1,840 people. This review covers fleet upkeep, personnel, safety "
     "performance, warehousing, technology and regulatory standing. Each section is "
     "self-contained and figures are not repeated between sections."),

    # 2
    ("2. Corporate Structure",
     "The group is organised into four operating divisions reporting to a central "
     "executive board. Divisional managers hold profit and loss responsibility for "
     "their depot. A shared services function covers payroll, legal and procurement. "
     "The board meets eleven times per year. The company was incorporated in 1987 and "
     "has been employee-owned since 2004."),

    # 3
    ("3. Fleet Composition Overview",
     "The fleet comprises 612 vehicles: 388 tractor units, 154 rigid trucks and 70 "
     "light vans. Average vehicle age is 4.7 years. The replacement cycle targets "
     "seven years for tractor units and nine years for rigids. Ninety-one vehicles "
     "are scheduled for replacement next year."),

    # ── Trap 1: four pages of near-identical vocabulary ──────────────────
    # 4 — the DISTRACTOR. Repeats "average maintenance cost per vehicle" and
    #     every depot name more often than any page that holds a real figure,
    #     while stating no depot-specific number at all.
    ("4. Fleet Maintenance — Policy and Standards",
     "This page defines how the average maintenance cost per vehicle is calculated "
     "for the Northern depot, the Southern depot and the Eastern depot. The average "
     "maintenance cost per vehicle is the total maintenance spend divided by the "
     "vehicle count at that depot. Average maintenance cost per vehicle is reported "
     "quarterly for the Northern depot, the Southern depot and the Eastern depot. "
     "Maintenance intervals are set at 12,000 miles for tractor units. Note that this "
     "policy page contains no depot figures; the average maintenance cost per vehicle "
     "for each depot is given on the individual depot pages that follow."),

    # 5
    ("5. Fleet Maintenance — Northern Depot",
     "The Northern depot maintained 214 vehicles this year. Total maintenance spend "
     "was 1.94 million dollars, giving an average maintenance cost per vehicle of "
     "9,065 dollars. Scheduled servicing accounted for 61 percent of that spend. The "
     "depot completed 1,284 workshop visits."),

    # 6 — the page that actually answers the Southern-depot question
    ("6. Fleet Maintenance — Southern Depot",
     "The Southern depot maintained 168 vehicles this year. Total maintenance spend "
     "was 1.71 million dollars, giving an average maintenance cost per vehicle of "
     "10,178 dollars. Scheduled servicing accounted for 54 percent of that spend. The "
     "depot completed 1,047 workshop visits."),

    # 7
    ("7. Fleet Maintenance — Eastern Depot",
     "The Eastern depot maintained 142 vehicles this year. Total maintenance spend was "
     "1.12 million dollars, giving an average maintenance cost per vehicle of 7,887 "
     "dollars. Scheduled servicing accounted for 66 percent of that spend. The depot "
     "completed 903 workshop visits."),

    # 8
    ("8. Fuel Procurement",
     "Bulk diesel was purchased under a fixed-price agreement covering 71 percent of "
     "consumption. Total diesel volume was 9.8 million litres at an average of 1.42 "
     "dollars per litre. The remaining volume was bought on the spot market at an "
     "average of 1.58 dollars per litre."),

    # 9
    ("9. Driver Recruitment",
     "The group hired 236 drivers during the year against a target of 210. Median time "
     "from application to first shift was 34 days. Agency cover accounted for 8 percent "
     "of driving hours. Recruitment spend was 640,000 dollars."),

    # ── Trap 3a: synonym-only. Question will say "staff turnover" / "left". ──
    # This page avoids "turnover", "left", "quit", "resign" entirely.
    ("10. Driver Attrition",
     "Attrition among the driving workforce reached 14.2 percent for the year, against "
     "11.8 percent in the prior period. Voluntary departures numbered 187, of which 71 "
     "occurred within the first six months of service. Exit interviews cited shift "
     "patterns as the most common factor, followed by depot commute distance."),

    # 11
    ("11. Safety Incidents — Classification",
     "Incidents are graded Tier 1 through Tier 4. Tier 1 denotes an event causing "
     "injury requiring hospital treatment or vehicle immobilisation. Tier 2 covers "
     "reportable damage without injury. Tiers 3 and 4 cover minor damage and near "
     "misses respectively. Grading is assigned by the depot safety officer within 24 "
     "hours."),

    # ── Trap 2: the answer spans pages 12 and 13 ─────────────────────────
    # 12 has the counts, 13 has the resolution times. Neither alone suffices.
    ("12. Safety Incidents — Tier 1 Register, Part One",
     "Nineteen Tier 1 incidents were recorded across the group this year. By depot: "
     "Northern seven, Southern six, Eastern four, Western two. Fourteen involved "
     "tractor units and five involved rigids. Eleven occurred during night operations. "
     "Resolution timings for these incidents are tabulated in the following section."),

    # 13
    ("13. Safety Incidents — Tier 1 Register, Part Two",
     "Mean time to closure for the incidents listed in the preceding section was 23.4 "
     "days. The longest single case remained open for 96 days pending an external "
     "engineering report. Eight cases closed within a fortnight. No case was closed "
     "without a documented root cause."),

    # 14 / 15 — a second overlapping pair, warehouses
    ("14. Warehouse Operations — Riverside",
     "The Riverside warehouse handled 412,000 pallet movements this year across 18,600 "
     "square metres. Pick accuracy was 99.31 percent. Agency labour covered 22 percent "
     "of hours worked. The site operates two shifts."),

    ("15. Warehouse Operations — Kingsford",
     "The Kingsford warehouse handled 287,000 pallet movements this year across 12,400 "
     "square metres. Pick accuracy was 99.62 percent. Agency labour covered 15 percent "
     "of hours worked. The site operates three shifts."),

    # 16
    ("16. Technology and Telematics",
     "Telematics units are fitted to 598 of 612 vehicles. The platform records braking "
     "events, idling and route adherence. A driver scorecard was introduced in the "
     "second half of the year. Integration with the maintenance scheduler is planned "
     "but not yet delivered."),

    # ── Trap 3b: synonym-only. Question will say "trucks out of service". ──
    # This page avoids "out of service", "unavailable", "truck".
    ("17. Vehicle Downtime Analysis",
     "Aggregate downtime across the fleet totalled 41,300 off-road hours. Unplanned "
     "events accounted for 63 percent of that figure. The median off-road duration per "
     "event was 19 hours. Parts availability was the largest single contributor, "
     "followed by workshop capacity."),

    # ── Trap 3c: synonym-only. Question: "how long does it take to buy". ──
    ("18. Procurement Lead Times",
     "Lead time from requisition to delivery averaged 94 days for tractor units and 61 "
     "days for trailers. Tyres and consumables averaged 9 days. The longest recorded "
     "lead time was 212 days for a specialist refrigeration unit."),

    # 19
    ("19. Regulatory Compliance",
     "The group held an unblemished operator licence throughout the year. Two roadside "
     "prohibitions were issued, both for lighting defects, and both were cleared within "
     "48 hours. Tachograph infringements fell to 0.4 per thousand driving hours."),

    # 20
    ("20. Outlook and Risks",
     "The principal risk for the coming year is exposure to a single supplier for "
     "refrigeration units, where the recorded lead time is materially longer than for "
     "any other component. A second supplier is being qualified. Fuel price volatility "
     "and driver availability remain the other two significant exposures."),
]


# ── Multi-sheet Excel: sheet name -> (header row, data rows) ─────────────────
#
# Same three difficulties. The four quarterly revenue sheets are near-identical
# in vocabulary; "Cost Definitions" is the distractor that repeats the phrasing
# of a question without holding any figure; H2 revenue spans two sheets; and
# "Energy Spend" is described in vocabulary a natural question will not use.

LARGE_XLSX_SHEETS = {
    # The distractor: repeats "revenue per consignment" for every quarter and
    # every region, with no actual value anywhere on the sheet.
    "Cost Definitions": (
        ["Term", "Definition"],
        [
            ["Revenue per consignment",
             "Revenue per consignment is quarterly revenue divided by consignments, "
             "reported for Q1, Q2, Q3 and Q4 for North, South, East and West"],
            ["Quarterly revenue", "Total invoiced value for the quarter, per region"],
            ["Consignments", "Count of completed deliveries in the quarter, per region"],
            ["Note", "This sheet defines terms only and contains no revenue figures"],
        ],
    ),
    "Q1 Revenue": (
        ["Region", "Consignments", "Revenue USD", "Revenue per consignment"],
        [["North", 8120, 9_140_000, 1126], ["South", 6440, 7_020_000, 1090],
         ["East", 5310, 5_880_000, 1108], ["West", 3105, 3_260_000, 1050]],
    ),
    "Q2 Revenue": (
        ["Region", "Consignments", "Revenue USD", "Revenue per consignment"],
        [["North", 8640, 9_910_000, 1147], ["South", 6710, 7_450_000, 1110],
         ["East", 5590, 6_240_000, 1116], ["West", 3240, 3_410_000, 1052]],
    ),
    "Q3 Revenue": (
        ["Region", "Consignments", "Revenue USD", "Revenue per consignment"],
        [["North", 9010, 10_480_000, 1163], ["South", 7030, 7_980_000, 1135],
         ["East", 5820, 6_610_000, 1136], ["West", 3390, 3_600_000, 1062]],
    ),
    "Q4 Revenue": (
        ["Region", "Consignments", "Revenue USD", "Revenue per consignment"],
        [["North", 9370, 11_020_000, 1176], ["South", 7280, 8_390_000, 1152],
         ["East", 6040, 6_950_000, 1151], ["West", 3520, 3_780_000, 1074]],
    ),
    "Headcount": (
        ["Division", "Drivers", "Warehouse", "Admin"],
        [["North", 402, 181, 64], ["South", 318, 143, 51],
         ["East", 265, 119, 44], ["West", 151, 68, 34]],
    ),
    # Synonym target: a question about "fuel" or "diesel" should land here,
    # but the sheet says "energy" and "propulsion".
    "Energy Spend": (
        ["Category", "Litres", "Unit cost USD", "Total USD"],
        [["Propulsion, contracted", 6_958_000, 1.42, 9_880_360],
         ["Propulsion, spot market", 2_842_000, 1.58, 4_490_360],
         ["Depot heating", 118_000, 0.94, 110_920]],
    ),
    "Incident Log": (
        ["Tier", "Count", "Mean days to close"],
        [["Tier 1", 19, 23.4], ["Tier 2", 64, 9.1],
         ["Tier 3", 211, 3.6], ["Tier 4", 587, 1.2]],
    ),
}


# ── Dense multi-topic PDF: (heading, body) per page ──────────────────────────
#
# WHY THIS FIXTURE EXISTS
#
# The 20-page fixture above averages 46 words per page, one topic each. At that
# size a page already IS a passage, so splitting it into smaller passages can
# only be a no-op — which makes it structurally incapable of showing whether
# sub-chunking helps or hurts. It scores 18/18 and cannot move.
#
# These pages are the opposite: ~214 words each (4.6x the fixture above), and
# every page carries six unrelated policy topics. That is the condition the
# sub-chunking
# claim is actually about — "a page can hold several unrelated facts, and
# retrieving the whole page dilutes the match". If the claim is right it should
# show up here; if it does not show up here, it does not show up anywhere.
#
# Three deliberate difficulties, mirroring the fixture above:
#
# 1. DILUTION — the answer to each question is one or two sentences inside a
#    page of six unrelated topics, so whole-page embedding averages the answer
#    together with several hundred words that have nothing to do with it.
#
# 2. TOPICAL SIBLINGS — expenses appear on three pages (account setup, travel
#    caps, purchase thresholds). All three are plausibly "about expenses" and
#    only one carries any given figure.
#
# 3. CROSS-PAGE — the equipment loss policy opens at the foot of page 3 and its
#    excess figures land on page 4, so neither page answers on its own.
#
# Every figure appears exactly once in the document, so a retrieval hit is
# unambiguous.

DENSE_PDF_TITLE = "Ardwick Logistics — Staff Operations Manual"

DENSE_PDF_PAGES = [
    # 1
    ("Section A — Joining the Company",
     "New starter registration. Every new employee completes registration with the "
     "people team before their first shift. Registration requires proof of right to "
     "work, a bank mandate and an emergency contact. "
     "Site access badges. Access badges are issued by reception on the first morning "
     "and must be worn visibly inside operational areas. A lost badge carries a "
     "replacement charge of 15 pounds. Badges deactivate after ninety days of non-use. "
     "Vehicle parking. Staff parking is allocated by depot and is not transferable "
     "between sites. The Ardwick site holds 240 marked bays, of which 26 are reserved "
     "for visitors and 12 for accessible parking. Overnight parking requires written "
     "approval from the depot manager. "
     "Canteen and refreshments. The canteen operates from 06:00 to 19:00 on weekdays "
     "and 07:00 to 14:00 on Saturdays. Hot food service stops thirty minutes before "
     "closing. Vending machines in the transport office run continuously. "
     "Network accounts. IT accounts are created from the registration record and are "
     "usually active within one working day. Initial passwords are issued verbally by "
     "the service desk and changed at first sign-in. Accounts dormant for sixty days "
     "are suspended pending manager confirmation. "
     "Expense account setup. An expense account is opened alongside the network "
     "account. Opening the account does not itself authorise any spending, and the "
     "authorisation limits are set out later in this manual. The first expense claim "
     "made by a new starter is reviewed manually."),

    # 2
    ("Section B — Travel and Subsistence",
     "Booking channel. All business travel is booked through the nominated travel "
     "desk. Bookings made outside that channel are reimbursed only where the traveller "
     "can show the desk was unavailable and the journey could not be deferred. Rail is "
     "the default mode for domestic journeys under four hours. "
     "Subsistence allowance. The daily subsistence allowance is 38 pounds for domestic "
     "travel and 61 pounds for international travel. It is paid for each full day away "
     "and halved for a day of departure or return. "
     "Private mileage. Where a private vehicle is used for a business journey the "
     "mileage reimbursement rate is 45 pence per mile for the first ten thousand miles "
     "in a year and 25 pence per mile thereafter. A passenger supplement of 5 pence "
     "per mile applies for each colleague carried. Journeys between home and the "
     "normal place of work are not reimbursable. "
     "Accommodation ceilings. Hotel spend is capped at 140 pounds per night in London "
     "and 95 pounds per night elsewhere in the United Kingdom. Rates above the ceiling "
     "require prior written approval. "
     "Travel documentation. Employees travelling internationally hold a valid passport "
     "with at least six months remaining. The company meets the cost of business visas "
     "but not of passport renewal. "
     "Travel insurance. Corporate travel insurance covers booked business travel "
     "automatically. Cover does not extend to leisure days added either side of a trip "
     "unless declared to the travel desk in advance."),

    # 3
    ("Section C — Equipment and Systems",
     "Standard issue. Office-based staff are issued a laptop and a docking station. "
     "Operational staff are issued a handheld terminal. Equipment remains company "
     "property and is returned on the final day of employment. "
     "Refresh cycle. Laptops are replaced on a four year cycle and handheld terminals "
     "on a three year cycle. Replacement outside the cycle requires a fault report "
     "from the service desk. Withdrawn devices are wiped and either resold or recycled "
     "through an accredited contractor. "
     "Mobile telephones. A company mobile is provided to roles with an on-call "
     "obligation. Personal use is permitted within reason. The monthly data allowance "
     "is 40 gigabytes, and usage beyond that is recharged to the department rather "
     "than to the individual. "
     "Software requests. Requests for software outside the standard build go to the "
     "service desk and require line manager approval. Licences are assigned to the "
     "individual and reclaimed when the individual leaves. "
     "Printing. Printing is charged to departmental cost centres at 4 pence per mono "
     "page and 19 pence per colour page. Jobs left unclaimed for 24 hours are deleted. "
     "Loss and damage. Where company equipment is lost or damaged the employee reports "
     "it to the service desk within one working day. The company may recover a "
     "contribution towards replacement where the loss resulted from negligence rather "
     "than ordinary wear. The contribution is not automatic and depends on the age of "
     "the device; the amounts recovered are set out in the following section."),

    # 4
    ("Section D — Recovery Amounts and Working Time",
     "Equipment loss excess. Continuing from the previous section, the contribution "
     "recovered for a negligent loss is 120 pounds for a laptop, 75 pounds for a "
     "handheld terminal and 45 pounds for a mobile telephone. Nothing is recovered "
     "where the device is more than three years old, and nothing is recovered for a "
     "first incident in any rolling three year period. "
     "Standard hours. Full time hours are 37.5 per week for office roles and 42 per "
     "week for operational roles, exclusive of unpaid breaks. The reference period for "
     "averaging working time is seventeen weeks. "
     "Overtime. Overtime is paid at time and a quarter on weekdays and time and a half "
     "on Sundays and public holidays. Overtime is authorised in advance by the depot "
     "manager, and unauthorised hours are not paid. "
     "Rest breaks. A rest break of thirty minutes applies to any shift exceeding six "
     "hours. Drivers are additionally subject to statutory tachograph rules, which "
     "take precedence wherever they are more restrictive than this manual. "
     "Shift swaps. Swaps between colleagues are permitted where both hold the relevant "
     "competence and the swap is recorded in the rota system before the shift begins. "
     "Time recording. Hours are recorded through the depot terminal at the start and "
     "end of each shift. A missed clocking is corrected by the supervisor and "
     "confirmed by the employee within five working days."),

    # 5
    ("Section E — Absence and Leave",
     "Annual leave entitlement. The standard entitlement is 25 days per year plus "
     "public holidays, rising to 28 days after five years of continuous service. "
     "Entitlement is pro-rated for part time staff and the leave year runs from the "
     "first of April. "
     "Carry over. A maximum of five days may be carried into the following leave year "
     "and must be taken by the thirtieth of June. Carry over beyond five days is "
     "granted only where leave was refused for operational reasons. "
     "Sickness absence. Absence is reported to the line manager by telephone before "
     "the start of the shift. Self certification covers the first seven calendar days "
     "and beyond that a fit note is required. Company sick pay is paid at full rate "
     "for up to twelve weeks in a rolling year, subject to length of service. "
     "Parental leave. Maternity, paternity, adoption and shared parental leave follow "
     "statutory entitlement, with an enhanced company element of twelve weeks at full "
     "pay for the primary carer after two years of service. "
     "Bereavement. Paid bereavement leave of five days is available on the death of an "
     "immediate family member and one day for a wider relative. "
     "Unpaid sabbatical. A sabbatical of between one and six months may be requested "
     "after four years of service. Sabbaticals are unpaid, preserve continuity of "
     "service, and are granted at the discretion of the divisional director."),

    # 6
    ("Section F — Performance and Development",
     "Review cycle. Formal performance reviews are held twice a year, in April and "
     "October. Each review records objectives, evidence against them and a development "
     "plan. Interim conversations are expected monthly but are not recorded centrally. "
     "Rating scale. Performance is rated on a four point scale: exceptional, strong, "
     "effective and developing. Ratings are moderated across each division before "
     "release so that standards are comparable between depots. "
     "Promotion. Promotion is considered once a year following the April cycle. A case "
     "requires two consecutive ratings of strong or above and a vacancy at the target "
     "grade. Acting-up arrangements are capped at nine months. "
     "Training budget. The annual training budget is 900 pounds per employee for "
     "professional development, held at departmental level. Statutory and safety "
     "training is funded separately and does not draw on this budget. "
     "Performance improvement. Where performance is rated developing for two "
     "consecutive cycles a formal improvement plan of up to twelve weeks is opened. "
     "The plan sets measurable outcomes and is reviewed fortnightly. "
     "Appeals. An employee may appeal a rating within ten working days of release. "
     "Appeals are heard by a manager one level above the reviewer and outside the "
     "immediate reporting line."),

    # 7
    ("Section G — Security and Information",
     "Password standards. Passwords are at least fourteen characters and are not "
     "rotated on a schedule, in line with current guidance. Reuse of a password across "
     "company and personal services is prohibited. "
     "Multi-factor authentication. Multi-factor authentication is mandatory for all "
     "remote access and for any administrative account. Hardware tokens are issued to "
     "roles handling payment data. "
     "Data classification. Information is classified as public, internal, confidential "
     "or restricted. Restricted material may not be removed from company premises or "
     "systems under any circumstances. Confidential material may be taken off site "
     "only on encrypted company equipment. "
     "Incident reporting. Suspected security incidents are reported to the service "
     "desk immediately and in any case within one hour of discovery. Reporting in good "
     "faith is never itself a disciplinary matter. "
     "Clear desk. Desks are clear of confidential material at the end of each day. "
     "Printed confidential material is disposed of in the secure bins provided rather "
     "than in general waste. "
     "Visitors. Visitors sign in at reception, are issued a temporary badge and are "
     "escorted at all times within operational areas. Visitor badges expire at the end "
     "of the day of issue."),

    # 8
    ("Section H — Purchasing and Suppliers",
     "Authorisation thresholds. Purchase orders up to 2,500 pounds are authorised by "
     "the line manager. Between 2,500 and 25,000 pounds authorisation rests with the "
     "divisional director. Above 25,000 pounds board approval is required. Splitting a "
     "purchase to stay below a threshold is treated as a disciplinary matter. "
     "Supplier onboarding. New suppliers are onboarded by procurement following "
     "financial and compliance checks. Onboarding takes fifteen working days on "
     "average. No order may be placed with a supplier that has not completed "
     "onboarding. "
     "Contract terms. Standard payment terms are sixty days from invoice date. "
     "Deviation from standard terms requires procurement approval and is recorded "
     "against the supplier record. "
     "Invoice handling. Invoices are matched against the purchase order and the goods "
     "receipt before payment. A mismatch of more than two percent is referred back to "
     "the raising department. "
     "Disputes. A disputed invoice is placed on hold and escalated to procurement "
     "within five working days. Payment of the undisputed portion continues in the "
     "meantime. "
     "Single supplier risk. Where a category has only one qualified supplier, "
     "procurement records the exposure on the risk register and reviews it quarterly. "
     "The register is presented to the board twice a year."),
]


# ── Questionnaire fixture: the case the LLM blend exists for ─────────────────
#
# Every other fixture is a normal document, so `question_extraction` and the
# `0.7 * llm_score + 0.3 * heuristic` blend had never run end to end. That gap
# is why an 80-token budget could disable LLM classification for a whole model
# migration without a single number moving.
#
# The design constraint is precise: this must be a form that the HEURISTIC
# under-scores. If the regex patterns caught it, the LLM term would be
# redundant and the fixture would pass whether or not the blend worked.
#
# So it deliberately avoids every strong cue in _Q_SIGNALS: no "Question 1:",
# no "survey" / "questionnaire" / "form" keyword, no Likert wording, no "check
# all that apply", no underscore fill-ins, no "please select", no yes/no pairs.
# It reads as prose instructions, which is how a great many real intake forms
# are actually written.
#
# Measured: heuristic scores 0.000 (normal_document, below the 0.4 threshold),
# the LLM returns p(questionnaire) 0.96, and the blend lands at 0.672
# (questionnaire). Discard the LLM result and this document silently stops
# being a questionnaire, which is exactly what the eval needs to catch.

QUESTIONNAIRE_PDF_TITLE = "Ardwick Logistics — Supplier Onboarding Record"

QUESTIONNAIRE_PDF_PAGES = [
    # 1
    ("Supplier Onboarding Record",
     "Complete every section before returning this record to the procurement team. "
     "Entries that are left blank will delay onboarding, which takes fifteen working "
     "days once a complete record is received.\n\n"
     "Section 1 — Organisation\n"
     "State the registered legal name of your organisation.\n"
     "State the company registration number and the country of incorporation.\n"
     "Give the trading address, and the registered address where it differs.\n"
     "Name the individual who will act as the primary commercial contact.\n\n"
     "Section 2 — Financial standing\n"
     "State your annual turnover for the two most recent financial years.\n"
     "Describe any material change in ownership during that period.\n"
     "Confirm the payment terms your organisation is able to accept. Standard terms "
     "are sixty days from invoice date."),

    # 2
    ("Supplier Onboarding Record (continued)",
     "Section 3 — Capability\n"
     "Describe the goods or services your organisation would supply under this "
     "agreement, and the volumes you can sustain in a normal quarter.\n"
     "Identify the sites from which those goods or services would be delivered.\n"
     "State the lead time you would commit to for a standard order.\n"
     "Describe your escalation route when a committed lead time cannot be met.\n\n"
     "Section 4 — Compliance\n"
     "Confirm that your organisation holds current employers liability cover, and "
     "state the insured amount.\n"
     "Describe how your organisation verifies the right to work of its staff.\n"
     "Summarise your policy on subcontracting, and name any subcontractor you would "
     "expect to use on this account.\n"
     "State whether your organisation has been subject to an enforcement action by a "
     "regulator in the last five years, and describe the circumstances."),
]


# ── Dilution probe: does topic mixing cost a passage its rank? ───────────────
#
# Built BLIND, before any fix existed and without knowing what fix might apply,
# so the fixture cannot be tuned toward a conclusion.
#
# A 3x3 factorial plus controls. Every page is ~130 words and carries exactly
# one queryable fact, unique in the document:
#
#            fact LEADS      fact MIDDLE     fact FINAL
#   1 topic  dp-01 control   dp-02 control   dp-03 control
#   2 topics dp-04           dp-05           dp-06
#   6 topics dp-07           dp-08           dp-09
#
# THE CONTROLS ARE THE POINT. If a single-topic page of the same length also
# loses rank when its fact sits in the final clause, the problem is depth or
# length and heterogeneity is a red herring. Without them a mixed-topic page
# that ranks badly proves nothing, because two variables moved.
#
# Written to read like real estates reporting rather than as traps: an annual
# report genuinely does put six subjects in an executive summary, and a
# combined "Grounds and Security" page is how small teams actually write. No
# page repeats another page's vocabulary to bait it.

MIXED_PDF_TITLE = "Calderwood Estates — Annual Facilities Report"

MIXED_PDF_PAGES = [
    # ── 1 topic (controls) ───────────────────────────────────────────────
    # 1 — fact LEADS
    ("1. Boiler Plant Replacement",
     "The boiler plant replacement programme cost 4.7 million pounds and "
     "completed in November. Three gas-fired units were removed from the "
     "basement plant room and replaced with condensing equivalents. The "
     "contractor worked outside teaching hours throughout, so no sessions were "
     "lost to the works. Flue routing was rebuilt to discharge above the "
     "parapet, which required a scaffold licence for eleven weeks. "
     "Commissioning ran across two weekends and the system was rebalanced "
     "afterwards. The old units were removed by a licensed recycler and their "
     "copper recovered. Warranty runs for seven years from handover. Plant "
     "room lighting and ventilation were renewed at the same time because the "
     "space was already stripped, which the surveyor recommended."),

    # 2 — fact MIDDLE
    ("2. Lift Maintenance",
     "Eleven passenger lifts and two goods lifts are covered by a single "
     "maintenance contract renewed each April. Engineers attend monthly for "
     "planned servicing and are called out on demand. Mean time between "
     "callouts across the estate was 214 days, which the contract sets as the "
     "reliability floor. Door sensors account for the largest share of faults, "
     "followed by controller boards. Two lifts in the east wing are approaching "
     "the end of their design life and a condition survey is scheduled. Spares "
     "are held on site for the two most common failures to avoid waiting on "
     "delivery. Out-of-hours attendance is guaranteed within four hours and "
     "has been met on every occasion this year."),

    # 3 — fact FINAL
    ("3. Window Replacement Programme",
     "The window replacement programme continued across the residential "
     "blocks this year. Single-glazed timber frames are being replaced with "
     "double-glazed aluminium throughout, block by block, so that residents "
     "move out for one week rather than a whole term. Acoustic performance was "
     "specified alongside thermal, because the north elevation faces the "
     "bypass. Scaffolding is shared between adjacent blocks to reduce hire "
     "costs. Waste frames are separated on site and the glass recycled "
     "separately from the timber. The programme is funded from the capital "
     "reserve rather than from operating budgets. Across the year 1,340 window "
     "units were replaced."),

    # ── 2 topics ─────────────────────────────────────────────────────────
    # 4 — fact LEADS
    ("4. Grounds and Security",
     "Grounds maintenance contracts total 890,000 pounds annually, covering "
     "mowing, hedging, tree surveys and winter gritting across the whole "
     "estate. The contractor holds an arboricultural qualification, which the "
     "tree stock requires. Gritting is triggered by a forecast threshold "
     "rather than by observation, so the routes run before dawn. Security is "
     "provided by a separate contractor under its own agreement. The gatehouse "
     "is staffed continuously and mobile patrols cover the outer car parks "
     "between dusk and dawn. Camera coverage was extended to the western "
     "boundary after a spate of thefts from vehicles. Access cards are issued "
     "by the estates office and deactivate automatically when a contract ends."),

    # 5 — fact MIDDLE
    ("5. Catering and Waste",
     "The catering contract was retendered this year and awarded for five "
     "years with a break at three. Three outlets operate daily during term and "
     "one continues through vacations. Menu costing is reviewed quarterly "
     "against commodity indices. Waste is collected under a separate "
     "arrangement, and the estate diverted 71 percent of its waste from "
     "landfill. Food waste is separated at source in all three kitchens and "
     "goes to anaerobic digestion. Dry mixed recycling is collected twice "
     "weekly and general waste once. Confidential paper is shredded on site "
     "under a witnessed process. The waste contractor reports tonnages monthly "
     "and these feed the annual sustainability return."),

    # 6 — fact FINAL
    ("6. Parking and Signage",
     "Parking on the estate is managed by permit, with allocation weighted "
     "toward staff who work irregular hours or carry equipment. Enforcement is "
     "contracted out and appeals are heard by an independent panel. The two "
     "largest car parks were resurfaced this year and their drainage renewed "
     "at the same time. Wayfinding signage was replaced across the whole "
     "estate to a single specification, which took most of the summer "
     "vacation. Fingerposts at the three main entrances now carry building "
     "numbers rather than department names, because departments move and "
     "buildings do not. The signage scheme cost 156,000 pounds."),

    # ── 6 topics (executive-summary shape) ───────────────────────────────
    # 7 — fact LEADS
    ("7. Estates Overview",
     "The estates directorate employs 268 people across maintenance, grounds, "
     "security, catering, portering and administration. The estate comprises "
     "44 buildings over 31 hectares, with a gross internal area of 186,000 "
     "square metres. Operating expenditure was broadly flat against the prior "
     "year once energy is excluded. Capital projects completed on schedule in "
     "all but one case, where a planning condition delayed a start. Statutory "
     "compliance was maintained across all disciplines with no enforcement "
     "action. Staff turnover in the directorate fell for the second successive "
     "year. The service desk logged 41,200 requests, of which 94 percent were "
     "closed within target. Tenant satisfaction was surveyed in the spring."),

    # 8 — fact MIDDLE
    ("8. Year in Summary",
     "Capital projects completed on time and within the approved envelope. "
     "Energy consumption fell after the boiler works and the lighting retrofit "
     "landed together. The service desk handled a higher volume than last year "
     "with the same establishment. The estate's insured reinstatement value is "
     "assessed at 412 million pounds, revalued this year for the first time "
     "since the pandemic. Compliance inspections were completed across all "
     "disciplines. Grounds and security contracts were both retendered without "
     "dispute. Portering absorbed the additional load from the two building "
     "moves without agency cover. The directorate closed the year within "
     "budget on both pay and non-pay."),

    # 9 — fact FINAL
    ("9. Director's Statement",
     "This has been a year of consolidation rather than expansion. The "
     "directorate absorbed two significant capital programmes while keeping "
     "the routine service running, which is a credit to the teams involved. "
     "Recruitment remains difficult in the trades, and we continue to rely on "
     "a small number of long-serving staff whose knowledge is not written "
     "down. The compliance position is sound and was independently verified. "
     "Energy remains the largest single exposure and the hedge expires next "
     "year. Relationships with the two principal contractors are constructive. "
     "Looking ahead, the condition survey will set the capital programme for "
     "the next cycle. Deferred maintenance across the estate now stands at "
     "23.8 million pounds."),
]

# ── Competitor pages ─────────────────────────────────────────────────────────
#
# Added after the first nine measured 9/9 rank-1, because that probe was missing
# a variable md-10 has. In md-10 the answering page does not merely hold the
# fact among others — it competes with a page DEDICATED to the question's
# subject: "how many people does the company employ" is fought over by an
# executive summary that states the number and a Corporate Structure page that
# is entirely about the organisation and states none.
#
# Each page below is topically dedicated to one probe question and deliberately
# does NOT contain its answer. Depth is held at "lead" across the three so only
# the answer page's topic count varies:
#
#   dp-10  answer on a 1-topic page   competitor: page 10
#   dp-11  answer on a 2-topic page   competitor: page 11
#   dp-12  answer on a 6-topic page   competitor: page 12   <- the md-10 shape
#
# Realistic: a facilities report really would carry both a plant-room page and a
# boiler-replacement page, and both a workforce page and an overview that counts
# heads.

MIXED_PDF_COMPETITORS = [
    # 10 — competes with dp-01 (boiler replacement cost, 1-topic answer page)
    ("10. Plant Room Condition",
     "The plant rooms were surveyed for condition and access this year. Boiler "
     "housings, pipework lagging and pump mountings were inspected in each of "
     "the four rooms. Several runs of lagging were found damaged where cabling "
     "had been pulled through, and these were made good. Access to the west "
     "plant room remains poor and a permanent ladder is specified for next "
     "year. Ventilation grilles were cleared and the louvre actuators "
     "serviced. Water treatment dosing was recalibrated on all circuits. "
     "Expenditure on plant room works sits within the routine maintenance "
     "budget and is not separately reported. The survey did not price any "
     "replacement of the boilers themselves."),

    # 11 — competes with dp-04 (grounds contract value, 2-topic answer page)
    ("11. Landscape and Planting Strategy",
     "The landscape strategy sets out how the grounds are managed over a ten "
     "year horizon. Mowing regimes were relaxed on three verges to encourage "
     "wildflowers, and the resulting reduction in cutting frequency was agreed "
     "with the contractor. Tree stock is surveyed on a three year cycle and "
     "high-risk specimens annually. Replacement planting favours native "
     "species and follows the loss of eleven mature trees to disease. Hedging "
     "along the northern boundary is being restored by laying rather than "
     "flailing. The strategy does not set contract values, which are handled "
     "separately through procurement."),

    # 12 — competes with dp-07 (directorate headcount, 6-topic answer page)
    ("12. Workforce and Recruitment",
     "Recruitment across the directorate remains difficult in the trades, "
     "particularly for mechanical and electrical craftspeople. Vacancies are "
     "advertised continuously rather than in campaigns. Agency use rose "
     "slightly in portering and fell in cleaning. An apprenticeship scheme "
     "started with four places across maintenance and grounds. Retention "
     "improved following the pay review, and exit interviews now cite commute "
     "and shift pattern more often than pay. Long service is a feature of the "
     "workforce and a succession risk. Training days were taken up more fully "
     "than last year. Establishment figures are held by human resources and "
     "are not reproduced in this section."),
]

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

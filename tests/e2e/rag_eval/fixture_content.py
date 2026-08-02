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

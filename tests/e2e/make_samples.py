"""Regenerate the e2e sample files under tests/e2e/samples/.

The samples are committed to the repo, so a fresh clone does NOT need to run
this. It exists so the fixtures can be rebuilt or adjusted deterministically.

Usage:
    python tests/e2e/make_samples.py

Notes:
    - sample_report.pdf and sample_sales.xlsx are built from reportlab/openpyxl
      and are fully cross-platform.
    - sample_audio.wav is synthesised with the Windows SAPI speech engine, so it
      can only be regenerated on Windows. The committed copy is used everywhere
      else; this script leaves it untouched if it cannot be rebuilt.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SAMPLES = Path(__file__).resolve().parent / "samples"
SAMPLES.mkdir(parents=True, exist_ok=True)


# ── PDF ───────────────────────────────────────────────────────────────────────

def build_pdf() -> Path:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import (PageBreak, Paragraph, SimpleDocTemplate,
                                    Spacer, Table, TableStyle)

    out = SAMPLES / "sample_report.pdf"
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(out), pagesize=LETTER)
    story = [
        Paragraph("Acme Robotics — Q3 Engineering Report", styles["Title"]),
        Spacer(1, 12),
        Paragraph("1. Executive Summary", styles["Heading1"]),
        Paragraph(
            "Acme Robotics completed the third quarter with the Orion control platform reaching "
            "general availability. Total engineering headcount grew from 42 to 51. The platform now "
            "serves 1,280 industrial units across 14 customer sites. Mean time between failures "
            "improved from 410 hours to 690 hours following the firmware 4.2 rollout. "
            "Cloud infrastructure spend was 312,000 dollars, under the 350,000 dollar budget.",
            styles["BodyText"]),
        Spacer(1, 12),
        Paragraph("2. Reliability Metrics", styles["Heading1"]),
        Paragraph(
            "The firmware 4.2 release addressed the servo calibration drift reported by three "
            "customers in Q2. Post-deployment telemetry shows drift incidents fell by 87 percent. "
            "The remaining incidents cluster in units manufactured before serial number A-8800.",
            styles["BodyText"]),
        Spacer(1, 12),
    ]

    table = Table([
        ["Metric", "Q2", "Q3", "Change"],
        ["MTBF (hours)", "410", "690", "+68%"],
        ["Active units", "940", "1280", "+36%"],
        ["Drift incidents", "31", "4", "-87%"],
        ["Cloud spend (USD)", "298,000", "312,000", "+4.7%"],
        ["Headcount", "42", "51", "+21%"],
    ], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ]))
    story.append(table)
    story.append(PageBreak())
    story.append(Paragraph("3. Risks and Next Quarter", styles["Heading1"]))
    story.append(Paragraph(
        "The principal risk for Q4 is the single-source supplier for the LIDAR module. "
        "A qualification programme for a second supplier began in September and is expected "
        "to complete in January. Until then a 6-week buffer stock is held. "
        "Engineering will prioritise the multi-tenant scheduler and the on-premise deployment "
        "option requested by two enterprise customers.",
        styles["BodyText"]))
    doc.build(story)
    return out


# ── Excel ─────────────────────────────────────────────────────────────────────

def build_excel() -> Path:
    from openpyxl import Workbook

    out = SAMPLES / "sample_sales.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Q3 Sales"
    ws.append(["Region", "Product", "Units", "Unit Price", "Revenue"])
    for row in [
        ("North", "Orion Controller", 320, 1250, 400000),
        ("North", "LIDAR Module", 180, 890, 160200),
        ("South", "Orion Controller", 210, 1250, 262500),
        ("South", "Service Contract", 95, 4200, 399000),
        ("EMEA", "Orion Controller", 410, 1180, 483800),
        ("EMEA", "LIDAR Module", 260, 850, 221000),
        ("APAC", "Orion Controller", 140, 1310, 183400),
        ("APAC", "Service Contract", 60, 4500, 270000),
    ]:
        ws.append(list(row))

    ws2 = wb.create_sheet("Headcount")
    ws2.append(["Department", "Q2", "Q3", "Open Roles"])
    for row in [("Engineering", 42, 51, 6), ("Sales", 18, 21, 3),
                ("Support", 12, 15, 2), ("Operations", 9, 9, 0)]:
        ws2.append(list(row))

    wb.save(out)
    return out


# ── Audio (Windows only) ──────────────────────────────────────────────────────

_SPEECH = (
    "Quarterly operations briefing. Revenue for the third quarter reached four point two "
    "million dollars, an increase of eighteen percent over the prior quarter. The engineering "
    "team shipped the new authentication service and reduced average API latency from three "
    "hundred milliseconds to ninety milliseconds. Customer churn fell to two point one percent. "
    "The primary risk for the next quarter is supply chain delay affecting hardware deliveries "
    "in November."
)


def build_audio() -> Path | None:
    out = SAMPLES / "sample_audio.wav"
    if sys.platform != "win32":
        print(f"  skip  {out.name} (Windows SAPI only; committed copy retained)")
        return None

    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SetOutputToWaveFile('{out}'); "
        "$s.Rate = -1; "
        f"$s.Speak('{_SPEECH}'); "
        "$s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True)
    return out


if __name__ == "__main__":
    for path in (build_pdf(), build_excel(), build_audio()):
        if path is not None:
            print(f"  wrote {path.name:24s} {path.stat().st_size / 1024:8.1f} KB")
    print(f"\nSamples in {SAMPLES}")

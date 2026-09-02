"""
Results view component for DocAgent Streamlit UI.

Renders the full PipelineResult in a tabbed interface:
    Tab 1 — Summary    (structured sections, key findings)
    Tab 2 — Questions  (searchable list with count badge)
    Tab 3 — Chat       (multi-turn Q&A over the document)
    Tab 4 — Edit       (restructure / fill the document)
    Tab 5 — Document   (extracted text, tables, run detail)

Also provides download buttons: PDF, Markdown, JSON, and CSV (questions).
"""

from __future__ import annotations

import io
import json
import re
from html import escape as _escape
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st
import streamlit.components.v1 as components

from core.models import CLASSIFIED_DOC_TYPES
from ui.components.pdf_fonts import register_pdf_fonts, sanitise_for_pdf
from core.pipeline_result import PipelineResult


# ── Helper: render HTML block ─────────────────────────────────────────────────

def _html(content: str) -> None:
    st.markdown(content, unsafe_allow_html=True)


# ── PDF generator ─────────────────────────────────────────────────────────────

def generate_pdf_bytes(result: PipelineResult, font_size: int = 11, margin: float = 0.9) -> bytes:
    """
    Convert a PipelineResult to a styled PDF document using reportlab.
    Returns raw PDF bytes suitable for st.download_button.
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT, TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.lib.colors import HexColor
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, HRFlowable,
            Table, TableStyle, KeepTogether,
        )
    except ImportError:
        return result.to_markdown().encode("utf-8")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        rightMargin=margin * inch, leftMargin=margin * inch,
        topMargin=margin * inch,  bottomMargin=margin * inch,
    )

    NAVY = HexColor("#1E3A5F")
    TEAL = HexColor("#0891b2")
    DARK = HexColor("#1a1a2a")
    GRAY = HexColor("#64748b")

    # Register a Unicode font before any style is built. The default is base-14
    # Helvetica, whose WinAnsi encoding is 224 characters, and everything
    # outside it rendered as a filled box — see ui/components/pdf_fonts.py.
    FONT, FONT_B, FONT_I, _FONT_BI = register_pdf_fonts()

    sw_s  = getSampleStyleSheet()
    title_s = ParagraphStyle("DA_Title",   parent=sw_s["Title"],
                              fontName=FONT_B,
                              fontSize=22, textColor=NAVY, spaceAfter=6, leading=26)
    h1_s    = ParagraphStyle("DA_H1",      parent=sw_s["Heading1"],
                              fontName=FONT_B,
                              fontSize=13, textColor=HexColor("#111827"), spaceBefore=16, spaceAfter=4)
    h2_s    = ParagraphStyle("DA_H2",      parent=sw_s["Heading2"],
                              fontName=FONT_B,
                              fontSize=11, textColor=TEAL, spaceBefore=10, spaceAfter=3)
    body_s  = ParagraphStyle("DA_Body",    parent=sw_s["Normal"],
                              fontName=FONT,
                              fontSize=font_size, leading=16, spaceAfter=4, textColor=DARK)
    meta_k  = ParagraphStyle("DA_MetaKey", parent=sw_s["Normal"],
                              fontSize=9, textColor=HexColor("#374151"), fontName=FONT_B)
    meta_v  = ParagraphStyle("DA_MetaVal", parent=sw_s["Normal"],
                              fontName=FONT,
                              fontSize=9, textColor=GRAY)
    note_s  = ParagraphStyle("DA_Note",    parent=sw_s["Normal"],
                              fontName=FONT,
                              fontSize=8, textColor=GRAY, spaceAfter=2)
    cell_s  = ParagraphStyle("DA_Cell",    parent=sw_s["Normal"],
                              fontName=FONT, fontSize=8.5, leading=11,
                              textColor=DARK)
    cellh_s = ParagraphStyle("DA_CellHead", parent=sw_s["Normal"],
                              fontName=FONT_B, fontSize=8.5, leading=11,
                              textColor=colors.white)

    def md_to_rl(text: str) -> str:
        """Markdown to ReportLab tag converter (**bold**, *italic*, bullets)."""
        if not text:
            return ""
        # Emoji survive the font swap as boxes because no text font carries
        # them; this is the only class DejaVu cannot draw. See pdf_fonts.py.
        text = sanitise_for_pdf(text)
        safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        safe = re.sub(r"(\*\*|__)(.*?)\1", r"<b>\2</b>", safe)
        safe = re.sub(r"(?<![a-zA-Z0-9])(\*|_)([^\*_]+)\1(?![a-zA-Z0-9])", r"<i>\2</i>", safe)
        safe = re.sub(r"`([^`]+)`", r'<font name="Courier" color="#333333" size="9">\1</font>', safe)
        lines = []
        for line in safe.split("\n"):
            processed = line.strip()
            if processed.startswith(("* ", "- ")):
                processed = f"  &bull;  {processed[2:]}"
            lines.append(processed)
        return "\n".join(lines)

    def p(text: str, style=None, is_md: bool = False) -> Paragraph:
        if is_md:
            safe = md_to_rl(text)
        else:
            safe = sanitise_for_pdf(text or "").replace("&", "&amp;")
        return Paragraph(safe, style or body_s)

    def md_table_rows(block: str):
        """Parse a markdown table block into rows, or return None.

        Requires a pipe row FOLLOWED BY a separator row (|---|---|), which is
        what distinguishes a table from a line of prose that happens to contain
        a pipe. Without that check, "Revenue | up 4%" in a sentence would be
        silently restructured into a one-cell table.
        """
        lines = [ln.strip() for ln in block.strip().split("\n") if ln.strip()]
        if len(lines) < 2:
            return None
        if not all(ln.startswith("|") or "|" in ln for ln in lines[:2]):
            return None
        sep = lines[1].replace("|", "").replace(":", "").replace("-", "").strip()
        if sep or "-" not in lines[1] or "|" not in lines[1]:
            return None

        rows = []
        for i, ln in enumerate(lines):
            if i == 1:
                continue                      # the |---|---| separator
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if cells:
                rows.append(cells)
        if len(rows) < 2:
            return None
        width = max(len(r) for r in rows)
        return [r + [""] * (width - len(r)) for r in rows]

    def make_table(rows):
        """A real ReportLab table, styled to match the metadata tables below.

        WHY A TABLE AND NOT REFLOWED TEXT. The model emits these constantly and
        they arrived as one run-on paragraph of pipes — the worst legibility
        problem in the export. Converting to bullets or key/value pairs was the
        alternative and it loses the thing a table is for: a cell is addressed
        by BOTH its row and its column, and prose can only carry one of those.
        A real table also fixes the reading artefact where adjacent cells ran
        together ("15 msClassify") when the PDF text was copied, because each
        cell becomes its own text object rather than a run in one paragraph.
        """
        header, body = rows[0], rows[1:]
        avail = A4[0] - 2 * margin * inch
        ncols = len(header)
        data = [[Paragraph(md_to_rl(c), cellh_s) for c in header]] + [
            [Paragraph(md_to_rl(c), cell_s) for c in r] for r in body
        ]
        tbl = Table(data, colWidths=[avail / ncols] * ncols, repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND",     (0, 0), (-1, 0), HexColor("#1E3A5F")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [HexColor("#f8f9fa"), colors.white]),
            ("GRID",           (0, 0), (-1, -1), 0.25, HexColor("#d1d5db")),
            ("VALIGN",         (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING",    (0, 0), (-1, -1), 5),
            ("RIGHTPADDING",   (0, 0), (-1, -1), 5),
            ("TOPPADDING",     (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING",  (0, 0), (-1, -1), 4),
        ]))
        return tbl

    def hr():
        return HRFlowable(width="100%", thickness=0.5, color=HexColor("#d1d5db"), spaceAfter=8)

    # Extract the LLM-generated title from the first # heading in the summary
    raw_summary = result.summary or ""
    pdf_title = "Analysis Report"
    summary_body = raw_summary
    first_line = raw_summary.strip().split("\n")[0].strip()
    if first_line.startswith("# "):
        pdf_title = first_line[2:].strip()
        summary_body = raw_summary[raw_summary.find("\n"):].strip()

    story = []
    story.append(p(pdf_title, title_s))
    story.append(hr())

    doc_label = result.doc_type.replace("_", " ").title()
    story.append(p(
        f"**File:** {result.file_name}   |   "
        f"**Format:** {result.file_type.upper()}   |   "
        f"**Document Type:** {doc_label}   |   "
        f"**Domain:** {result.domain}",
        meta_k, is_md=True
    ))
    story.append(p(
        f"**Words:** {result.word_count:,}   |   "
        f"**Pages/Sheets:** {result.page_count}   |   "
        f"**Classification:** {_confidence_text(result)} ({result.classification_method})   |   "
        f"**Processing:** {result.processing_time_ms / 1000:.2f}s",
        meta_k, is_md=True
    ))
    story.append(Spacer(1, 14))

    story.append(p("Summary", h1_s))
    story.append(hr())
    if not summary_body:
        story.append(p("No summary generated.", body_s))
    else:
        for block in summary_body.split("\n\n"):
            block = block.strip()
            if not block:
                continue
            lines_in_block = block.split("\n")
            first = lines_in_block[0].strip()
            rest  = "\n".join(lines_in_block[1:]).strip()
            def emit(text: str) -> None:
                """Body text, as a table when it is one."""
                rows = md_table_rows(text)
                if rows:
                    story.append(Spacer(1, 4))
                    story.append(make_table(rows))
                    story.append(Spacer(1, 6))
                else:
                    story.append(p(text, is_md=True))

            if first.startswith("## "):
                story.append(p(first[3:].strip(), h1_s))
                if rest:
                    emit(rest)
            elif first.startswith("### "):
                story.append(p(first[4:].strip(), h2_s))
                if rest:
                    emit(rest)
            else:
                emit(block)
    story.append(Spacer(1, 10))

    if result.questions:
        story.append(p(f"Extracted Questions  ({len(result.questions)} found)", h1_s))
        story.append(hr())
        for i, q in enumerate(result.questions, 1):
            story.append(p(f"{i}.   {q}"))
        story.append(Spacer(1, 10))

    clean_meta = {k: v for k, v in result.metadata.items() if v and k not in ("engine",)}
    if clean_meta:
        story.append(p("Document Metadata", h1_s))
        story.append(hr())
        table_data = [["Key", "Value"]] + [
            [Paragraph(str(k), meta_k), Paragraph(str(v)[:120], meta_v)]
            for k, v in list(clean_meta.items())[:20]
        ]
        tbl = Table(table_data, colWidths=[2.3 * inch, 4.5 * inch])
        tbl.setStyle(TableStyle([
            ("BACKGROUND",  (0, 0), (-1, 0),  HexColor("#1E3A5F")),
            ("TEXTCOLOR",   (0, 0), (-1, 0),  colors.white),
            ("FONTNAME",    (0, 0), (-1, 0),  FONT_B),
            ("FONTSIZE",    (0, 0), (-1, 0),  9),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [HexColor("#f8f9fa"), colors.white]),
            ("GRID",        (0, 0), (-1, -1),  0.25, HexColor("#d1d5db")),
            ("VALIGN",      (0, 0), (-1, -1),  "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1),  6),
            ("RIGHTPADDING",(0, 0), (-1, -1),  6),
            ("TOPPADDING",  (0, 0), (-1, -1),  4),
            ("BOTTOMPADDING",(0,0), (-1, -1),  4),
        ]))
        story.append(tbl)
        story.append(Spacer(1, 10))

    if result.skill_timings:
        story.append(p("Skill Timing Breakdown", h1_s))
        story.append(hr())
        timing_data = [["Skill Component", "Duration (ms)"]]
        for skill, ms in result.skill_timings.items():
            timing_data.append([
                Paragraph(skill.replace("_", " ").title(), body_s),
                Paragraph(f"{ms:,.0f} ms", body_s)
            ])
        t_tbl = Table(timing_data, colWidths=[3.5 * inch, 3.3 * inch])
        t_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), HexColor("#f3f4f6")),
            ("TEXTCOLOR",  (0, 0), (-1, 0), NAVY),
            ("FONTNAME",   (0, 0), (-1, 0), FONT_B),
            ("FONTSIZE",   (0, 0), (-1, 0), 10),
            ("GRID",       (0, 0), (-1, -1), 0.5, HexColor("#e5e7eb")),
            ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ]))
        story.append(t_tbl)
        story.append(Spacer(1, 10))

    if result.errors:
        story.append(p("⚠️  Errors", h2_s))
        for err in result.errors:
            story.append(p(f"  ✘ {err}", note_s))

    story.append(Spacer(1, 30))
    story.append(hr())
    story.append(p(
        f"Generated by DocAgent v1.0.0  ·  {result.processing_time_ms / 1000:.2f}s  ·  "
        f"Summary via {result.summary_method}",
        note_s
    ))

    doc.build(story)
    return buf.getvalue()


def generate_filled_pdf(markdown_text: str, margin: float = 0.9) -> bytes:
    """Generate a clean PDF from unstructured markdown for the Form Filler."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    except ImportError:
        return markdown_text.encode("utf-8")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        rightMargin=margin * inch, leftMargin=margin * inch,
        topMargin=margin * inch,  bottomMargin=margin * inch,
    )

    # Same Unicode font as the analysis export. This generator had the same
    # base-14 Helvetica defect, and it is the one that renders a FILLED FORM —
    # where a dropped character is a wrong answer, not just an ugly one.
    FONT, FONT_B, _FI, _FBI = register_pdf_fonts()

    sw_s  = getSampleStyleSheet()
    from reportlab.lib.colors import HexColor
    body_s = ParagraphStyle("Body", parent=sw_s["Normal"], fontName=FONT, fontSize=11, leading=16, spaceAfter=8, textColor=HexColor("#1e1e2e"))
    h1_s   = ParagraphStyle("H1",   parent=sw_s["Heading1"], fontName=FONT_B, fontSize=16, leading=20, spaceAfter=8, spaceBefore=12, textColor=HexColor("#1e1e2e"))
    h2_s   = ParagraphStyle("H2",   parent=sw_s["Heading2"], fontName=FONT_B, fontSize=14, leading=18, spaceAfter=6, spaceBefore=10, textColor=HexColor("#1e1e2e"))
    h3_s   = ParagraphStyle("H3",   parent=sw_s["Heading3"], fontName=FONT_B, fontSize=12, leading=16, spaceAfter=6, spaceBefore=8,  textColor=HexColor("#1e1e2e"))

    def md_to_rl(text: str) -> str:
        text = sanitise_for_pdf(text)
        safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        safe = re.sub(r"(\*\*|__)(.*?)\1", r"<b>\2</b>", safe)
        safe = re.sub(r"(?<![a-zA-Z0-9])(\*|_)([^\*_]+)\1(?![a-zA-Z0-9])", r"<i>\2</i>", safe)
        lines = []
        for line in safe.split("\n"):
            processed = line.strip()
            if processed.startswith(("* ", "- ")):
                processed = f"  &bull;  {processed[2:]}"
            lines.append(processed)
        return "<br/>".join(lines)

    story = []
    for para in markdown_text.split("\n\n"):
        if not para.strip():
            continue
        if para.startswith("# "):
            story.append(Paragraph(md_to_rl(para[2:].strip()), h1_s))
        elif para.startswith("## "):
            story.append(Paragraph(md_to_rl(para[3:].strip()), h2_s))
        elif para.startswith("### "):
            story.append(Paragraph(md_to_rl(para[4:].strip()), h3_s))
        else:
            story.append(Paragraph(md_to_rl(para.strip()), body_s))

    doc.build(story)
    return buf.getvalue()


# ── Summary rendering helpers ─────────────────────────────────────────────────

def _render_summary_structured(summary: str) -> None:
    """
    Render a summary string with rich formatting.
    Detects markdown headings, bullets, and key-value lines,
    and renders each with appropriate Streamlit components.
    """
    if not summary:
        _html("""
        <div class="empty-state">
          <h3>No summary generated</h3>
          <p>Check above for errors.</p>
        </div>""")
        return

    lines = summary.split("\n")
    buffer: list[str] = []

    def flush():
        if buffer:
            chunk = "\n".join(buffer).strip()
            if chunk:
                _html(f'<div class="summary-para fade-in">{chunk}</div>')
            buffer.clear()

    for line in lines:
        stripped = line.strip()

        # Markdown H1 → document title (rendered once, prominently)
        if stripped.startswith("# "):
            flush()
            title = stripped[2:].strip()
            _html(f'<div class="summary-title">{title}</div>')
        # Markdown H2/H3 headings → section dividers
        elif stripped.startswith("## "):
            flush()
            heading = stripped[3:].strip()
            _html(f'<div class="summary-section-heading">{heading}</div>')
        elif stripped.startswith("### "):
            flush()
            heading = stripped[4:].strip()
            _html(f'<div class="summary-subsection-heading">{heading}</div>')
        # Bullet points → render as styled list items
        elif stripped.startswith(("- ", "• ", "* ")):
            text = stripped[2:].strip()
            # Bold leading label (e.g. "**Key Finding:** …")
            text = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", text)
            buffer.append(f'<div class="summary-bullet">◆ {text}</div>')
        # Numbered list
        elif re.match(r"^\d+\.\s", stripped):
            text = re.sub(r"^\d+\.\s*", "", stripped)
            text = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", text)
            buffer.append(f'<div class="summary-bullet">◆ {text}</div>')
        # Blank line → flush paragraph
        elif not stripped:
            flush()
        else:
            # Inline bold
            text = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", stripped)
            # Feature 5: render citation tags as styled badges
            text = re.sub(
                r'\[Source: (Pages? [^\]]+)\]',
                r'<span class="citation-tag">[\1]</span>',
                text,
            )
            buffer.append(text)

    flush()


# ── Table rendering helpers ───────────────────────────────────────────────────

def _render_tables_tab(tables: list) -> None:
    """Render extracted tables as interactive dataframes."""
    import pandas as pd

    if not tables:
        _html("""
        <div class="empty-state">
          <span class="icon">📊</span>
          <h3>No tables extracted</h3>
          <p>Tables are extracted from PDFs with detectable grid structures and Excel sheets.</p>
        </div>""")
        return

    _html(f'<p style="color:var(--text-muted);font-size:.85rem;margin-bottom:1rem">Found <strong style="color:var(--accent)">{len(tables)}</strong> table(s) in this document.</p>')

    for idx, tbl in enumerate(tables):
        # Tables can be stored as dicts with "data" (list of rows) or "text" (string repr)
        label = tbl.get("label") or tbl.get("title") or tbl.get("sheet_name") or f"Table {idx + 1}"
        page_info = tbl.get("page") or tbl.get("page_number") or tbl.get("sheet")

        header = f"**{label}**"
        if page_info:
            header += f" — page/sheet {page_info}"

        with st.expander(header, expanded=(idx == 0)):
            # Try structured rows first
            rows = tbl.get("data") or tbl.get("rows")
            if rows and isinstance(rows, list) and len(rows) > 0:
                try:
                    # If first row is a header list, use it as columns
                    if isinstance(rows[0], list):
                        df = pd.DataFrame(rows[1:], columns=rows[0]) if len(rows) > 1 else pd.DataFrame(rows)
                    elif isinstance(rows[0], dict):
                        df = pd.DataFrame(rows)
                    else:
                        df = pd.DataFrame(rows)
                    st.dataframe(df, use_container_width=True)
                except Exception:
                    # Fallback to text
                    text = tbl.get("text", str(rows))
                    st.text(text[:3000])
            elif "text" in tbl and tbl["text"]:
                # Plain text table repr — try to parse as CSV-like
                text = tbl["text"]
                # Attempt pandas read from string
                try:
                    df = pd.read_csv(io.StringIO(text), sep=None, engine="python")
                    st.dataframe(df, use_container_width=True)
                except Exception:
                    st.text(text[:3000])
            else:
                st.caption("Empty table.")


# ── Confidence meter ─────────────────────────────────────────────────────────

def display_confidence(result: Any) -> Optional[float]:
    """How confident the classifier is *in the answer it gave*, 0.0–1.0.

    `PipelineResult.classification_confidence` is NOT that number. It is the
    questionnaire-likelihood score: how strongly the document looked like a
    form. For a document classified `normal_document`, a score of 0.035 means
    the classifier was ~96.5% sure it was a normal document — but the UI showed
    "Classification Confidence 3%" in red, which reads as a failed
    classification when it is in fact a confident, correct one.

    So the score is inverted here for display when the winning class is
    "normal_document". The stored field is deliberately left untouched: it is
    persisted in history.db, asserted on in tests, and consumed by the pipeline,
    and changing its meaning would be a data change rather than a display fix.
    """
    from core.models import confidence_in_verdict
    return confidence_in_verdict(
        getattr(result, "classification_confidence", 0.0),
        getattr(result, "doc_type", ""))


def _banner_for(doc_type: str) -> tuple:
    """The banner label and CSS class for a doc_type. THREE states, not two.

    The `else` branch used to read "Normal Document", so a document that was
    never classified — a failed download, an empty parse — was announced as a
    normal document, and with the old confidence maths it came with "100%
    confidence" in green. Nothing checked whether there had been anything to
    classify.

    This is a plain function rather than an inline `if` so it can be tested
    without a Streamlit runtime; `render_results` is otherwise unreachable from
    a unit test, which is how the two-state version shipped.

    Normalised the same way `confidence_in_verdict` normalises, and against the
    same constant. The two must agree on what counts as a verdict: if the banner
    were case-sensitive and the confidence maths were not, "NORMAL_DOCUMENT"
    would print "Not classified" directly above a 97% meter.
    """
    normalised = (doc_type or "").lower()
    if normalised not in CLASSIFIED_DOC_TYPES:
        return "Not classified", "normal"
    if normalised == "questionnaire":
        return "Questionnaire / Form", "questionnaire"
    return "Normal Document", "normal"


def _confidence_text(result: Any) -> str:
    """The confidence as a human reads it, or why there is none."""
    confidence = display_confidence(result)
    return "not classified" if confidence is None else f"{confidence:.0%}"


def _render_confidence_meter(confidence, label: str = "Classification confidence") -> None:
    # A None confidence means nothing was classified. Drawing a bar at all
    # would put a number on screen where there is no verdict, and the old code
    # drew it full and green.
    if confidence is None:
        _html(
            '<div class="confidence-meter-wrap">'
            '<div class="confidence-meter-label">'
            f'<span>{label}</span><span>not classified</span></div>'
            '<div style="font-size:0.75rem;opacity:.65">'
            'No document content was extracted, so nothing was classified.'
            '</div></div>'
        )
        return

    # round(), not int(). int() truncates, so 0.965 rendered as 96 here while
    # the banner's `:.0%` rounded it to 97 — the two disagreed by a step.
    pct = round(confidence * 100)
    color = "#34d399" if pct >= 70 else "#fbbf24" if pct >= 40 else "#f87171"
    _html(f"""
    <div class="confidence-meter-wrap">
      <div class="confidence-meter-label">
        <span>{label}</span>
        <span style="color:{color};font-weight:700">{pct}%</span>
      </div>
      <div class="confidence-meter-bg">
        <div class="confidence-meter-fill" style="width:{pct}%;background:{color}"></div>
      </div>
    </div>
    """)


# ── TTS segment preparation ──────────────────────────────────────────────────

def _prepare_tts_segments(summary: str) -> list[dict]:
    """
    Parse the summary markdown into typed segments for modulated TTS playback.
    Strips source citations, code, markdown syntax, and tags each line with
    a content type (heading / subheading / bullet / body) so the JS engine
    can apply appropriate rate/pitch per segment.
    """
    # Drop source citation tags  e.g. [Source: Pages 1-3]
    text = re.sub(r'\[Source:[^\]]*\]', '', summary)
    # Drop inline code spans
    text = re.sub(r'`[^`]*`', '', text)
    # Unwrap markdown links  [label](url) → label
    text = re.sub(r'\[([^\]]*)\]\([^\)]*\)', r'\1', text)
    # Drop citation-style tags like [1], [2]
    text = re.sub(r'\[\d+\]', '', text)

    segments: list[dict] = []
    for line in text.split('\n'):
        s = line.strip()
        if not s:
            continue

        seg_type = 'body'
        if re.match(r'^#{1,2}\s', s):
            seg_type = 'heading'
            s = re.sub(r'^#{1,2}\s+', '', s)
        elif re.match(r'^#{3,6}\s', s):
            seg_type = 'subheading'
            s = re.sub(r'^#{3,6}\s+', '', s)
        elif re.match(r'^[•◆◇▪▸→◈\-\*]\s', s):
            seg_type = 'bullet'
            s = re.sub(r'^[•◆◇▪▸→◈\-\*]\s+', '', s)
        elif re.match(r'^\d+\.\s', s):
            seg_type = 'numbered'
            s = re.sub(r'^\d+\.\s+', '', s)

        # Strip remaining inline markdown
        s = re.sub(r'\*{1,3}(.*?)\*{1,3}', r'\1', s)
        s = re.sub(r'_{1,2}(.*?)_{1,2}', r'\1', s)
        s = re.sub(r'[~>]', '', s).strip()

        if s:
            segments.append({'text': s, 'type': seg_type})

    return segments


# ── Main render function ──────────────────────────────────────────────────────

def render_results(result: PipelineResult, export_cfg: Optional[Any] = None) -> None:
    """Render the full PipelineResult in a tabbed Streamlit UI.

    Ordered so the page answers, top to bottom: what you uploaded, what we
    made of it, what we found, and then what you can do with it. Exports used
    to sit above the summary, which offered a download of something the reader
    had not been shown yet; they are now below the content.
    """

    # ── What you uploaded ──────────────────────────────────────────────
    # The results block never named the file it was describing. With history
    # in the sidebar it is genuinely ambiguous which document is on screen.
    page_word = "Sheet" if result.file_type in ("xlsx", "xls", "csv") else "Page"
    page_label = f"{result.page_count} {page_word}{'s' if result.page_count != 1 else ''}"
    _html(f"""
    <div class="doc-header fade-in">
      <div class="doc-header-name">{_escape(result.file_name)}</div>
      <div class="doc-header-meta">{_escape(result.file_type.upper())} · {page_label}
        · {result.word_count:,} words</div>
    </div>
    """)

    # ── Document type banner ───────────────────────────────────────────
    confidence = display_confidence(result)
    label, css_cls = _banner_for(result.doc_type)

    conf_pct = ("no document content" if confidence is None
                else f"{confidence:.0%} confidence")
    _html(f"""
    <div class="doc-type-banner {css_cls} fade-in">
      <div style="flex:1">
        <div style="font-size:0.95rem;font-weight:700">{label}
          <span style="font-weight:400;opacity:.7;font-size:0.85rem;margin-left:.5rem">Domain: {result.domain}</span>
        </div>
        <div style="font-size:0.75rem;opacity:.65;font-weight:400;margin-top:2px">
          {conf_pct} · {result.classification_method}
        </div>
      </div>
    </div>
    """)

    # ── Partial result banner ──────────────────────────────────────────
    if getattr(result, "partial", False):
        completed = list(result.skill_timings.keys())
        st.error(
            "**Partial result** — one or more pipeline steps failed. "
            f"Completed steps: {', '.join(completed)}. "
            "See warnings below for details.",
            icon="🔴",
        )

    # ── Processing warnings and errors ────────────────────────────────
    # These were previously rendered twice — once here and once again below
    # the stats row — so every warning appeared as two identical banners.
    if result.errors:
        for e in result.errors:
            st.error(e)
    if result.warnings:
        for w in result.warnings:
            st.warning(w)

    # ── Confidence meter ───────────────────────────────────────────────
    _render_confidence_meter(display_confidence(result))


    # ── Stats row ──────────────────────────────────────────────────────
    char_count = len(result.raw_text)
    char_label = f"{char_count / 1000:.1f}k" if char_count >= 1000 else str(char_count)
    _html(f"""
    <div class="stat-grid fade-in">
      <div class="stat-card">
        <div class="stat-value">{result.word_count:,}</div>
        <div class="stat-label">Words</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{result.page_count}</div>
        <div class="stat-label">Pages / Sheets</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{len(result.questions)}</div>
        <div class="stat-label">Questions</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{len(result.tables)}</div>
        <div class="stat-label">Tables</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{char_label}</div>
        <div class="stat-label">Characters</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{result.processing_time_ms / 1000:.1f}s</div>
        <div class="stat-label">Processing</div>
      </div>
    </div>
    """)

    # ── Exports ────────────────────────────────────────────────────────
    # Built here (the PDF render is the slow part) but drawn after the tabs,
    # so the reader sees the findings before being offered a download of them.
    md_bytes   = result.to_markdown().encode("utf-8")
    json_bytes = json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str).encode("utf-8")
    fname      = Path(result.file_name).stem

    font_size = getattr(export_cfg, "pdf_font_size", 11) if export_cfg else 11
    margin    = getattr(export_cfg, "pdf_margin_inch", 0.9) if export_cfg else 0.9

    with st.spinner("Preparing downloads…"):
        try:
            pdf_bytes = generate_pdf_bytes(result, font_size=font_size, margin=margin)
        except Exception:
            pdf_bytes = md_bytes

    st.divider()

    # ── Tabs ───────────────────────────────────────────────────────────
    # "Document" is secondary navigation for the source material — the raw
    # extracted text, the tables and the run metadata. Those are reference,
    # not findings, so they sit behind the last tab rather than competing with
    # the summary. (The tables and metadata renderers already existed but were
    # unreachable: nothing had called them since the tab list was trimmed.)
    tab_labels = ["Summary", "Questions", "Chat", "Edit", "Document"]
    tabs = st.tabs(tab_labels)

    # ── Tab 1: Summary ─────────────────────────────────────────────────
    with tabs[0]:
        if result.summary:
            method_label = result.summary_method.replace("_", " ").title()
            _html(f'<span class="badge badge-blue" style="margin-bottom:.75rem">{method_label}</span>')
            _render_summary_structured(result.summary)

            # ── Read-aloud speaker button (modulated Web Speech API) ──────
            tts_segments = _prepare_tts_segments(result.summary)
            tts_segments_json = json.dumps(tts_segments)
            components.html(f"""
<!DOCTYPE html>
<html>
<head>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: transparent;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
    padding: 6px 2px 4px;
  }}

  /* ── Row 1: speaker + progress ── */
  #row-top {{
    display: flex; align-items: center; justify-content: flex-end; gap: 8px;
    margin-bottom: 8px;
  }}
  #tts-progress {{
    font-size: .68rem; color: rgba(148,163,184,.6); letter-spacing: .03em;
  }}
  #tts-btn {{
    background: rgba(99,102,241,.14);
    border: 1.5px solid rgba(99,102,241,.32);
    border-radius: 50%;
    width: 2.2rem; height: 2.2rem; font-size: 1rem;
    cursor: pointer; outline: none; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center;
    transition: background .18s, transform .13s;
  }}
  #tts-btn:hover  {{ background: rgba(99,102,241,.26); transform: scale(1.07); }}
  @keyframes pulse {{
    0%   {{ box-shadow: 0 0 0 0   rgba(239,68,68,.5); }}
    65%  {{ box-shadow: 0 0 0 6px rgba(239,68,68,0);  }}
    100% {{ box-shadow: 0 0 0 0   rgba(239,68,68,0);  }}
  }}
  #tts-btn.active {{
    background: rgba(239,68,68,.16); border-color: rgba(239,68,68,.45);
    animation: pulse 1.6s ease infinite;
  }}

  /* ── Row 2: speed label (centered) ── */
  #speed-display {{
    text-align: center; font-size: .95rem; font-weight: 700;
    color: rgba(203,213,225,.85); margin-bottom: 5px; letter-spacing: .01em;
  }}

  /* ── Row 3: − slider + ── */
  #row-slider {{
    display: flex; align-items: center; gap: 8px; margin-bottom: 7px;
  }}
  .step-btn {{
    width: 1.9rem; height: 1.9rem; border-radius: 50%; flex-shrink: 0;
    background: rgba(203,213,225,.08); border: 1px solid rgba(203,213,225,.16);
    color: rgba(203,213,225,.8); font-size: 1rem; font-weight: 400;
    cursor: pointer; outline: none;
    display: flex; align-items: center; justify-content: center;
    transition: background .15s;
  }}
  .step-btn:hover  {{ background: rgba(203,213,225,.18); }}
  .step-btn:active {{ transform: scale(.9); }}

  #speed-slider {{
    flex: 1; -webkit-appearance: none; appearance: none;
    height: 3px; border-radius: 2px; outline: none; cursor: pointer;
    background: linear-gradient(
      to right,
      rgba(99,102,241,.9) 0%,
      rgba(99,102,241,.9) var(--pct,28.5%),
      rgba(203,213,225,.18) var(--pct,28.5%),
      rgba(203,213,225,.18) 100%
    );
  }}
  #speed-slider::-webkit-slider-thumb {{
    -webkit-appearance: none;
    width: 15px; height: 15px; border-radius: 50%;
    background: #e2e8f0; box-shadow: 0 1px 4px rgba(0,0,0,.5);
    cursor: pointer; transition: transform .13s;
  }}
  #speed-slider::-webkit-slider-thumb:hover {{ transform: scale(1.2); }}
  #speed-slider::-moz-range-thumb {{
    width: 15px; height: 15px; border-radius: 50%;
    background: #e2e8f0; border: none; box-shadow: 0 1px 4px rgba(0,0,0,.5);
  }}

  /* ── Row 4: preset chips ── */
  #row-presets {{
    display: flex; gap: 4px; justify-content: space-between;
  }}
  .preset {{
    flex: 1; border-radius: 20px; padding: 3px 0 2px;
    background: rgba(203,213,225,.06); border: 1px solid rgba(203,213,225,.13);
    cursor: pointer; outline: none;
    display: flex; flex-direction: column; align-items: center;
    transition: background .14s, border-color .14s;
  }}
  .preset:hover    {{ background: rgba(203,213,225,.14); }}
  .preset.selected {{ background: rgba(99,102,241,.22); border-color: rgba(99,102,241,.5); }}
  .p-val {{ font-size: .68rem; font-weight: 600; color: rgba(203,213,225,.85); }}
  .p-lbl {{ font-size: .55rem; color: rgba(148,163,184,.6); margin-top: 1px; line-height: 1; }}
</style>
</head>
<body>

<!-- Row 1 -->
<div id="row-top">
  <span id="tts-progress"></span>
  <button id="tts-btn" title="Read summary aloud" onclick="toggleSpeech()">🔊</button>
</div>

<!-- Row 2 -->
<div id="speed-display">0.85x</div>

<!-- Row 3 -->
<div id="row-slider">
  <button class="step-btn" onclick="stepSpeed(-1)" title="Slower">−</button>
  <input id="speed-slider" type="range" min="0" max="7" step="1" value="2"
         oninput="onSlider(this.value)">
  <button class="step-btn" onclick="stepSpeed(1)" title="Faster">+</button>
</div>

<!-- Row 4 -->
<div id="row-presets">
  <button class="preset" data-idx="0" onclick="setPreset(0)"><span class="p-val">0.5</span><span class="p-lbl">&nbsp;</span></button>
  <button class="preset" data-idx="1" onclick="setPreset(1)"><span class="p-val">0.75</span><span class="p-lbl">&nbsp;</span></button>
  <button class="preset selected" data-idx="2" onclick="setPreset(2)"><span class="p-val">0.85</span><span class="p-lbl">Default</span></button>
  <button class="preset" data-idx="3" onclick="setPreset(3)"><span class="p-val">1.0</span><span class="p-lbl">Normal</span></button>
  <button class="preset" data-idx="4" onclick="setPreset(4)"><span class="p-val">1.25</span><span class="p-lbl">&nbsp;</span></button>
  <button class="preset" data-idx="5" onclick="setPreset(5)"><span class="p-val">1.5</span><span class="p-lbl">&nbsp;</span></button>
  <button class="preset" data-idx="6" onclick="setPreset(6)"><span class="p-val">1.75</span><span class="p-lbl">&nbsp;</span></button>
  <button class="preset" data-idx="7" onclick="setPreset(7)"><span class="p-val">2.0</span><span class="p-lbl">&nbsp;</span></button>
</div>

<script>
const _segments  = {tts_segments_json};
const _STEPS     = [0.5, 0.75, 0.85, 1.0, 1.25, 1.5, 1.75, 2.0];
let _speedMult   = 0.85;
let _sliderIdx   = 2;
let _idx         = 0;       // index of NEXT segment to speak
let _speaking    = false;
let _voice       = null;
let _gen         = 0;       // bumped on every cancel — makes old callbacks no-ops
let _charOffset  = 0;       // char index of last boundary event within current utterance
let _resumeText  = null;    // when set, _makeAndSpeak uses this text instead of full segment

/* ── Voice selection ── */
function _pickVoice() {{
  const voices = window.speechSynthesis.getVoices();
  if (!voices.length) return null;
  const named = ['Google US English','Google UK English Female',
    'Microsoft Aria Online (Natural)','Microsoft Jenny Online (Natural)',
    'Microsoft Guy Online (Natural)','Samantha','Karen','Moira','Tessa','Daniel','Alex'];
  for (const n of named) {{ const v = voices.find(v => v.name === n); if (v) return v; }}
  const hi = voices.filter(v => v.lang.startsWith('en') &&
    /(neural|online|enhanced|premium|natural)/i.test(v.name));
  if (hi.length) return hi[0];
  const goog = voices.filter(v => v.lang.startsWith('en') && /google/i.test(v.name));
  if (goog.length) return goog[0];
  return voices.find(v => v.lang === 'en-US') || voices.find(v => v.lang.startsWith('en')) || voices[0];
}}

/* ── Build one utterance for a given text + segment type + generation ── */
function _makeUtterance(text, segType, gen) {{
  if (!_voice) _voice = _pickVoice();
  const u = new SpeechSynthesisUtterance(text);
  if (_voice) u.voice = _voice;
  u.lang = 'en-US';
  let base, pitch, vol;
  switch (segType) {{
    case 'heading':    base=0.82; pitch=1.18; vol=1.00; break;
    case 'subheading': base=0.86; pitch=1.10; vol=1.00; break;
    case 'bullet': case 'numbered': base=0.90; pitch=0.97; vol=0.95; break;
    default:           base=0.92; pitch=1.00; vol=1.00;
  }}
  u.rate   = Math.max(0.1, Math.min(10, base * _speedMult));
  u.pitch  = pitch;
  u.volume = vol;

  /* Track exact character position so speed-change can resume mid-sentence.
     'boundary' fires at every word/sentence boundary with charIndex pointing
     to the start of the next word — at most we repeat the current word. */
  u.onboundary = (e) => {{ if (_gen === gen) _charOffset = e.charIndex; }};

  /* Advance to next segment when this one finishes naturally */
  u.onend = () => {{
    if (_gen !== gen) return;
    _charOffset = 0;
    _resumeText = null;
    _next(gen);
  }};

  /* Only reset on genuine errors, not on programmatic cancel() */
  u.onerror = (e) => {{
    if (_gen === gen && e.error !== 'canceled') _reset();
  }};

  return u;
}}

/* ── Speak next segment (or _resumeText for the current one) ── */
function _next(gen) {{
  if (_idx >= _segments.length) {{ _reset(); return; }}
  const seg  = _segments[_idx++];
  const text = (_resumeText !== null) ? _resumeText : seg.text;
  _resumeText = null;
  _updateProgress();
  window.speechSynthesis.speak(_makeUtterance(text, seg.type, gen));
}}

/* ── UI helpers ── */
function _updateProgress() {{
  const el = document.getElementById('tts-progress');
  if (el) el.textContent = _segments.length > 1 ? `${{_idx}}/${{_segments.length}}` : '';
}}
function _reset() {{
  _speaking = false; _idx = 0; _charOffset = 0; _resumeText = null;
  const btn = document.getElementById('tts-btn');
  if (btn) {{ btn.textContent='🔊'; btn.title='Read summary aloud'; btn.classList.remove('active'); }}
  const prg = document.getElementById('tts-progress');
  if (prg) prg.textContent = '';
}}

/* ── Play / stop toggle ── */
function toggleSpeech() {{
  const btn = document.getElementById('tts-btn');
  if (_speaking) {{
    _gen++;
    window.speechSynthesis.cancel();
    _reset();
  }} else {{
    _gen++;
    window.speechSynthesis.cancel();
    _idx = 0; _charOffset = 0; _resumeText = null; _speaking = true;
    btn.textContent='🔇'; btn.title='Stop reading'; btn.classList.add('active');
    _next(_gen);
  }}
}}

/* ── Speed change: resume from exact char position within current sentence ── */
function _applySpeed(stepIdx) {{
  _sliderIdx = Math.max(0, Math.min(_STEPS.length-1, stepIdx));
  _speedMult = _STEPS[_sliderIdx];

  // Update UI
  const disp = document.getElementById('speed-display');
  if (disp) disp.textContent = _speedMult + 'x';
  const sl = document.getElementById('speed-slider');
  if (sl) {{
    sl.value = _sliderIdx;
    sl.style.setProperty('--pct', (_sliderIdx / (_STEPS.length-1) * 100) + '%');
  }}
  document.querySelectorAll('.preset').forEach(el =>
    el.classList.toggle('selected', +el.dataset.idx === _sliderIdx));

  if (!_speaking) return;

  /* _idx was already incremented when _next() last ran, so
     _idx-1 is the segment currently being spoken. */
  const currentSegIdx = Math.max(0, _idx - 1);
  const currentSeg    = _segments[currentSegIdx];

  /* Slice from the last word boundary — at most repeats the current word */
  const remaining = currentSeg.text.slice(_charOffset).trim();

  _gen++;                               // invalidate current utterance's callbacks
  window.speechSynthesis.cancel();

  _charOffset = 0;
  if (remaining.length > 0) {{
    /* Resume mid-segment: set _idx back so _next reads the same segment,
       but override its text with just the remaining portion */
    _idx        = currentSegIdx;        // _next() will do _segments[_idx++]
    _resumeText = remaining;
  }} else {{
    /* Segment was essentially finished; move on to the next one */
    _idx        = currentSegIdx + 1;
    _resumeText = null;
  }}

  _next(_gen);
}}

function onSlider(val)    {{ _applySpeed(+val); }}
function setPreset(idx)   {{ _applySpeed(idx); }}
function stepSpeed(delta) {{ _applySpeed(_sliderIdx + delta); }}

window.addEventListener('load', () => {{
  const sl = document.getElementById('speed-slider');
  if (sl) sl.style.setProperty('--pct', (_sliderIdx/(_STEPS.length-1)*100)+'%');
}});
if (typeof speechSynthesis !== 'undefined')
  speechSynthesis.onvoiceschanged = () => {{ _voice = null; }};
</script>
</body>
</html>
""", height=165)

            citations = getattr(result, "summary_citations", [])
            if citations:
                with st.expander(f"Citation Index ({len(citations)} references)", expanded=False):
                    for ref in citations:
                        pages = ", ".join(str(p) for p in ref["pages"])
                        st.markdown(f"- Paragraph {ref['paragraph_index'] + 1}: **Pages/Sheets {pages}** — _{ref['section']}…_")
        else:
            _html("""
            <div class="empty-state">
              <h3>No summary generated</h3>
              <p>Check above for errors.</p>
            </div>""")

    # ── Tab 2: Questions ───────────────────────────────────────────────
    with tabs[1]:
        if result.questions:
            q_method    = result.question_extraction_method.upper()
            q_badge_cls = "badge-cyan" if "llm" in q_method.lower() else "badge-purple"
            _html(f'<span class="badge {q_badge_cls}" style="margin-bottom:.75rem">{q_method}</span>')
            _html(f'<span class="badge badge-green" style="margin-bottom:.75rem;margin-left:.5rem">{len(result.questions)} questions</span>')

            # Search / filter
            search = st.text_input(
                "Filter questions",
                placeholder="Type to filter…",
                key=f"q_search_{fname}",
                label_visibility="collapsed",
            )
            filtered = [q for q in result.questions if not search or search.lower() in q.lower()]
            if search and not filtered:
                st.caption("No questions match your filter.")

            _html('<div class="fade-in">')
            for i, q in enumerate(filtered, 1):
                # Highlight search term
                display_q = q
                if search:
                    display_q = re.sub(
                        f"({re.escape(search)})",
                        r'<mark style="background:rgba(37,99,235,.25);color:var(--text-primary);border-radius:3px">\1</mark>',
                        q, flags=re.IGNORECASE
                    )
                # Map back to original index for the number badge
                orig_idx = result.questions.index(q) + 1
                _html(f"""
                <div class="question-item">
                  <div class="question-num">{orig_idx}</div>
                  <div class="question-text">{display_q}</div>
                </div>""")
            _html("</div>")

            # ── Auto-Fill Form ───────────────────────────────────────
            st.divider()
            st.markdown("### Auto-Fill Application Form")
            st.caption("Provide your answers here. They will be injected back into the document framework.")

            fill_state_key = f"form_fill_{result.file_name}"
            gen_state_key  = f"form_gen_{result.file_name}"

            if fill_state_key not in st.session_state:
                with st.form(key=f"fill_form_{result.file_name}"):
                    answers = {}
                    for i, q in enumerate(result.questions, 1):
                        _html(f"<div style='margin-top:1rem;color:var(--text-primary);font-size:.875rem;font-weight:500;line-height:1.5'>{i}. {q}</div>")
                        answers[q] = st.text_area(f"Answer {i}", key=f"ans_{i}", label_visibility="collapsed")
                    if st.form_submit_button("Submit & Generate Document", use_container_width=True):
                        st.session_state[fill_state_key] = answers
                        st.rerun()
            else:
                answers = st.session_state[fill_state_key]
                st.success("Form inputs confirmed")

                if gen_state_key not in st.session_state:
                    with st.spinner("Generating Restructured Document via LLM…"):
                        from skills.form_filling_skill import FormFillingSkill
                        from core.models import SkillInput
                        from utils.config import load_config

                        cfg   = load_config()
                        skill = FormFillingSkill(config=cfg.to_dict())
                        out   = skill.safe_execute(SkillInput(data={
                            "raw_text":     result.raw_text,
                            "questions":    result.questions,
                            "user_answers": answers,
                            "model":        cfg.groq.model
                        }))
                        if out.success:
                            st.session_state[gen_state_key] = out.data["filled_markdown"]
                            st.rerun()
                        else:
                            st.error(f"Generation failed: {out.error}")
                            if st.button("Retry", key="retry_fill"):
                                del st.session_state[fill_state_key]
                                st.rerun()

                if gen_state_key in st.session_state:
                    filled_md = st.session_state[gen_state_key]
                    st.divider()
                    st.markdown("#### Generated File")
                    with st.expander("Preview Restructured Text", expanded=True):
                        st.markdown(filled_md)

                    pdf_bytes_ff = generate_filled_pdf(filled_md)
                    ff_cols = st.columns(2)
                    with ff_cols[0]:
                        _html('<div class="btn-pdf">')
                        st.download_button(
                            label="Download Restructured Document (PDF)",
                            data=pdf_bytes_ff,
                            file_name=f"FILLED_{Path(result.file_name).stem}.pdf",
                            mime="application/pdf",
                            use_container_width=True,
                            key="btn_filled_pdf"
                        )
                        _html('</div>')
                    with ff_cols[1]:
                        _html('<div class="btn-md">')
                        st.download_button(
                            label="Download Restructured Document (Markdown)",
                            data=filled_md.encode("utf-8"),
                            file_name=f"FILLED_{Path(result.file_name).stem}.md",
                            mime="text/markdown",
                            use_container_width=True,
                            key="btn_filled_md"
                        )
                        _html('</div>')

                    if st.button("Reset Form", use_container_width=True):
                        del st.session_state[fill_state_key]
                        del st.session_state[gen_state_key]
                        st.rerun()

        elif result.doc_type == "questionnaire":
            _html("""
            <div class="empty-state">
              <h3>No questions found</h3>
              <p>The document was classified as a questionnaire but no questions could be extracted.</p>
            </div>""")
        else:
            _html(f"""
            <div class="empty-state">
              <h3>Not a questionnaire</h3>
              <p>This document was classified as a normal document
              ({_confidence_text(result)} confidence).
              Question extraction only runs on questionnaires.</p>
            </div>""")

    # ── Tab 3: Chat ────────────────────────────────────────────────────
    with tabs[2]:
        _render_chat_tab(result, fname)

    # ── Tab 4: Edit ────────────────────────────────────────────────────
    with tabs[3]:
        _render_edit_tab(result, fname)

    # ── Tab 5: Document (source material and run detail) ───────────────
    with tabs[4]:
        doc_sections = st.tabs(["Extracted text", "Tables", "Run detail"])

        with doc_sections[0]:
            if result.raw_text:
                st.caption(
                    f"{len(result.raw_text):,} characters as extracted, before "
                    "any summarisation."
                )
                st.code(result.raw_text, language="text")
            else:
                _html("""
                <div class="empty-state">
                  <span class="icon">📄</span>
                  <h3>No extracted text</h3>
                  <p>Text is not retained for documents restored from history.</p>
                </div>""")

        with doc_sections[1]:
            _render_tables_tab(result.tables)

        with doc_sections[2]:
            _render_metadata(result)

    # ── Exports ────────────────────────────────────────────────────────
    st.divider()
    st.caption("Export")

    dl_cols = st.columns(3)
    with dl_cols[0]:
        _html('<div class="btn-pdf">')
        st.download_button(
            label="Download PDF",
            data=pdf_bytes,
            file_name=f"{fname}_docagent_report.pdf",
            mime="application/pdf",
            use_container_width=True,
            key=f"dl_pdf_{fname}",
        )
        _html("</div>")

    with dl_cols[1]:
        _html('<div class="btn-md">')
        st.download_button(
            label="Download Markdown",
            data=md_bytes,
            file_name=f"{fname}_docagent_report.md",
            mime="text/markdown",
            use_container_width=True,
            key=f"dl_md_{fname}",
        )
        _html("</div>")

    with dl_cols[2]:
        _html('<div class="btn-json">')
        st.download_button(
            label="Download JSON",
            data=json_bytes,
            file_name=f"{fname}_docagent_report.json",
            mime="application/json",
            use_container_width=True,
            key=f"dl_json_{fname}",
        )
        _html("</div>")

    # Questions CSV download (visible only if there are questions)
    if result.questions:
        import csv as _csv
        csv_buf = io.StringIO()
        w = _csv.writer(csv_buf)
        w.writerow(["#", "Question"])
        for i, q in enumerate(result.questions, 1):
            w.writerow([i, q])
        st.download_button(
            label=f"Download Questions CSV ({len(result.questions)})",
            data=csv_buf.getvalue().encode("utf-8"),
            file_name=f"{fname}_questions.csv",
            mime="text/csv",
            use_container_width=False,
            key=f"dl_csv_{fname}",
        )


def _render_chat_tab(result: PipelineResult, fname: str) -> None:
    """Feature 4: Multi-turn document chat."""
    from utils.llm_client import LLMClient
    from utils.config import load_config
    cfg = load_config()
    llm = LLMClient.from_config(cfg.to_dict())

    if not llm.available:
        _html('<div class="empty-state"><h3>Chat requires an API key</h3><p>Set GROQ_API_KEY in your .env file.</p></div>')
        return

    history_key = f"chat_history_{fname}"
    chunks_key  = f"chat_chunks_{fname}"

    if history_key not in st.session_state:
        st.session_state[history_key] = []

    # Build chunk list from parsed_doc (stored in session state by app.py)
    if chunks_key not in st.session_state:
        parsed_doc = st.session_state.get(f"parsed_doc_{fname}") or getattr(result, "parsed_document", None)
        if parsed_doc and hasattr(parsed_doc, "chunks"):
            # Passages for retrieval, same split DocumentStore indexes with, so
            # a document answers identically whether it came from this session
            # or from history. parsed_doc.chunks itself is untouched — the
            # summary above this tab is still built from whole pages.
            from utils.chunking import sub_chunk
            st.session_state[chunks_key] = sub_chunk([
                {"text": c.text, "page_or_sheet": c.page_or_sheet}
                for c in parsed_doc.chunks
            ])
        else:
            raw = result.raw_text or ""
            st.session_state[chunks_key] = [
                {"text": raw[i:i+1000], "page_or_sheet": i // 1000 + 1}
                for i in range(0, max(len(raw), 1), 1000)
            ]

    # A history entry saved before document content was persisted yields chunks
    # that are all empty. Answering from that produces confident "not in the
    # document" replies, which is worse than saying nothing is loaded.
    if not any((c.get("text") or "").strip() for c in st.session_state[chunks_key]):
        _html(
            '<div class="empty-state"><h3>No document content available</h3>'
            '<p>This result was loaded from history and was saved before document '
            'text was stored. Re-run the analysis to chat about it.</p></div>'
        )
        return

    st.markdown("### Ask questions")

    # Scope. Defaults to this document, so the tab behaves exactly as it always
    # has until someone chooses otherwise — the corpus path is opt-in, not a
    # replacement.
    scope_key = f"chat_scope_{fname}"
    scope = st.radio(
        "Search scope",
        ("This document", "All documents in history"),
        key=scope_key,
        horizontal=True,
        label_visibility="collapsed",
    )
    multi_doc = scope.startswith("All")

    active_chunks = st.session_state[chunks_key]
    if multi_doc:
        from utils.document_store import DocumentStore
        from skills.document_chat_skill import MAX_CORPUS_DOCUMENTS
        try:
            corpus = DocumentStore().load_corpus()
        except Exception as exc:                       # noqa: BLE001
            st.warning(f"Could not read the document history: {exc}")
            corpus = None

        if not corpus or not corpus["chunks"]:
            st.info(
                "No stored documents to search yet. Analyse a document and it "
                "joins the corpus automatically."
            )
            active_chunks = st.session_state[chunks_key]
            multi_doc = False
        else:
            active_chunks = corpus["chunks"]
            docs = corpus["documents"]

            # Which documents are ranked by MEANING and which are not. A
            # document whose stored vectors are unusable — different embedding
            # model, or a chunk scheme that predates passages — still answers,
            # but its gaps are embedded on demand. Left invisible, a corpus
            # could quietly be half keyword-matched.
            unsearchable = [d for d in docs if not d["searchable"]]
            st.caption(
                f"Searching **{len(docs)}** document(s), "
                f"{len(active_chunks)} passages."
                + (f" Capped at {MAX_CORPUS_DOCUMENTS}; older documents are "
                   f"outside this answer." if corpus["truncated"] else "")
            )
            with st.expander(
                f"Documents in scope ({len(docs) - len(unsearchable)}/{len(docs)} "
                f"pre-indexed)", expanded=False
            ):
                for d in docs:
                    mark = "indexed" if d["searchable"] else (
                        f"will be embedded on first use ({d['vectors']}/{d['chunks']})")
                    st.markdown(f"- `{d['document']}` — {d['chunks']} passages, {mark}")
                if corpus["skipped"]:
                    st.markdown(
                        "\nNot searchable — saved before document text was stored, "
                        "so they hold no content to answer from:\n"
                        + "\n".join(f"- `{n}`" for n in corpus["skipped"])
                    )

    # Name the retrieval method. It was logged but never shown, so a
    # deployment without sentence-transformers answered from keyword overlap
    # -- measured 27/33 on the eval set against 33/33 for embeddings -- and
    # looked identical to one that had them. Silent degradation of answer
    # quality is worse than a slower answer, so say which is running.
    from utils import embeddings as _emb
    if _emb.is_supported():
        st.caption(
            "Conversation is kept in memory while the page is open. "
            f"Retrieval: semantic similarity (`{_emb.model_name()}`)."
        )
    else:
        st.caption(
            "Conversation is kept in memory while the page is open. "
            "Retrieval: **keyword overlap** — the embedding model is not "
            "installed here, so answers are drawn from word-overlap matches "
            "rather than meaning. Scores 27/33 on the retrieval eval against "
            "33/33 for embeddings."
        )

    # Render chat history
    history = st.session_state[history_key]
    for msg in history:
        if msg["role"] == "user":
            _html(f'<div class="chat-bubble-user">{msg["content"]}</div>')
        else:
            content = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", msg["content"])
            _html(f'<div class="chat-bubble-bot">{content}</div>')

    # Input
    if hasattr(st, "chat_input"):
        user_input = st.chat_input("Ask a question about this document…", key=f"chat_input_{fname}")
    else:
        col1, col2 = st.columns([5, 1])
        with col1:
            user_input = st.text_input("Ask a question", key=f"chat_input_{fname}", label_visibility="collapsed",
                                       placeholder="Ask a question about this document…")
        with col2:
            if not st.button("Send", key=f"chat_send_{fname}", use_container_width=True):
                user_input = None

    if user_input and user_input.strip():
        history.append({"role": "user", "content": user_input.strip()})
        _html(f'<div class="chat-bubble-user">{user_input.strip()}</div>')

        with st.spinner("Thinking…"):
            from skills.document_chat_skill import DocumentChatSkill
            from core.models import SkillInput
            skill = DocumentChatSkill(config=cfg.to_dict())
            out = skill.safe_execute(SkillInput(data={
                "user_message":         user_input.strip(),
                "document_chunks":      active_chunks,
                "conversation_history": history[:-1],
                "domain":               result.domain,
            }))

        if out.success and out.data:
            reply = out.data.get("reply", "I could not find an answer.")
            # `used_sources` carries the document alongside the page, which is
            # the whole point across a corpus: "Page 3" names four different
            # files in a six-document history. Falls back to the bare list only
            # if an older skill build did not supply it.
            used_sources = out.data.get("used_sources") or []
            if used_sources:
                labels = [s["label"] for s in used_sources]
            else:
                labels = [str(p) for p in out.data.get("used_pages", [])]
            if labels:
                reply += f"\n\n*Sources: {'; '.join(labels)}*"
        else:
            reply = f"Error: {out.error or 'Unknown error'}"
        history.append({"role": "assistant", "content": reply})
        content = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", reply)
        _html(f'<div class="chat-bubble-bot">{content}</div>')

        # Chat is the cheap operation per call but the easy one to repeat, so
        # it is counted too — a visitor asking a hundred questions of one
        # document never triggers an analyse and would otherwise be invisible.
        # Imported here rather than at module scope: ui.app imports this module,
        # so a top-level import would be circular.
        try:
            from ui.app import log_usage
            log_usage("chat")
        except Exception:  # never let telemetry break the answer
            pass

    if history:
        if st.button("Clear chat", key=f"clear_chat_{fname}"):
            st.session_state[history_key] = []
            st.rerun()


def _render_edit_tab(result: PipelineResult, fname: str) -> None:
    """Feature 3: Document edit mode."""
    from utils.llm_client import LLMClient
    from utils.config import load_config
    cfg = load_config()
    llm = LLMClient.from_config(cfg.to_dict())

    st.markdown("### Document Edit Mode")
    st.caption("Describe your changes in plain English. The AI will apply them and return a modified file.")

    if not llm.available:
        st.warning("Document editing requires a Groq API key. Set GROQ_API_KEY in your .env file.")
        return

    edit_key   = f"edit_instructions_{fname}"
    output_key = f"edit_output_{fname}"

    instructions = st.text_area(
        "Edit instructions",
        placeholder='e.g. "Change the title to Q4 2025 Report", "Remove Section 3", "Update the date to 2025-01-01"',
        height=120,
        key=edit_key,
    )

    if st.button("Apply Edits", key=f"btn_edit_{fname}", use_container_width=True):
        if not (instructions or "").strip():
            st.warning("Please enter edit instructions first.")
        else:
            parsed_doc = st.session_state.get(f"parsed_doc_{fname}") or getattr(result, "parsed_document", None)
            with st.spinner("Applying edits via LLM…"):
                from skills.document_editor_skill import DocumentEditorSkill
                from core.models import SkillInput
                skill = DocumentEditorSkill(config=cfg.to_dict())
                out = skill.safe_execute(SkillInput(data={
                    "edit_instructions": instructions.strip(),
                    "raw_text":          result.raw_text,
                    "file_type":         result.file_type,
                    "parsed_document":   parsed_doc,
                }))
            if out.success and out.data:
                st.session_state[output_key] = out.data
                if out.warnings:
                    for w in out.warnings:
                        st.warning(w)
                st.rerun()
            else:
                st.error(f"Edit failed: {out.error or 'Unknown error'}")

    if output_key in st.session_state:
        edit_data     = st.session_state[output_key]
        modified_text = edit_data.get("modified_text", "")

        with st.expander("Preview Modified Document", expanded=True):
            st.markdown(modified_text)

        out_format = edit_data.get("output_format", "pdf")
        dl_cols = st.columns(2)
        with dl_cols[0]:
            _html('<div class="btn-pdf">')
            pdf_bytes_edit = generate_filled_pdf(modified_text)
            st.download_button(
                "Download Modified PDF",
                data=pdf_bytes_edit,
                file_name=f"EDITED_{fname}.pdf",
                mime="application/pdf",
                use_container_width=True,
                key=f"dl_edited_pdf_{fname}",
            )
            _html("</div>")
        with dl_cols[1]:
            if out_format == "excel" and edit_data.get("output_bytes"):
                st.download_button(
                    "Download Modified Excel",
                    data=edit_data["output_bytes"],
                    file_name=f"EDITED_{fname}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                    key=f"dl_edited_excel_{fname}",
                )
            else:
                _html('<div class="btn-md">')
                st.download_button(
                    "Download Modified Markdown",
                    data=modified_text.encode("utf-8"),
                    file_name=f"EDITED_{fname}.md",
                    mime="text/markdown",
                    use_container_width=True,
                    key=f"dl_edited_md_{fname}",
                )
                _html("</div>")

        if st.button("Clear & Retry", key=f"clear_edit_{fname}"):
            del st.session_state[output_key]
            st.rerun()


def _render_metadata(result: PipelineResult) -> None:
    """Render metadata table + timing breakdown.

    Each card is emitted as ONE complete HTML string. It used to be built from
    a series of _html() fragments — an opening <div>, then the heading, then
    rows — but every _html() is a separate st.markdown call and Streamlit
    closes unbalanced tags per call. The result was an empty bordered card
    followed by content sitting outside it. Nobody had noticed because this
    function had no caller.
    """
    clean_meta = {k: v for k, v in result.metadata.items()
                  if v and str(v).strip() and k not in ("engine",)}

    if clean_meta:
        rows = "".join(
            f"<tr><td>{_escape(str(k))}</td><td>{_escape(str(v)[:200])}</td></tr>"
            for k, v in list(clean_meta.items())[:30]
        )
        _html(
            '<div class="glass-card fade-in" style="margin-bottom:1rem">'
            '<h4 style="color:var(--accent);margin:0 0 .75rem">Document Metadata</h4>'
            f'<table class="meta-table">{rows}</table>'
            "</div>"
        )

    if result.skill_timings:
        total_ms = sum(result.skill_timings.values())
        bars = "".join(
            f'<div class="timing-bar-wrap">'
            f'<div class="timing-label"><span>{_escape(skill)}</span>'
            f"<span>{ms:.0f} ms</span></div>"
            f'<div class="timing-bar-bg">'
            f'<div class="timing-bar-fill" style="width:{ms / max(total_ms, 1) * 100:.1f}%"></div>'
            f"</div></div>"
            for skill, ms in result.skill_timings.items()
        )
        _html(
            '<div class="glass-card fade-in">'
            '<h4 style="color:var(--accent);margin:0 0 .75rem">Skill Timing</h4>'
            f"{bars}"
            f'<p style="font-size:.78rem;color:var(--text-muted);margin-top:.5rem">'
            f"Total: {total_ms:.0f} ms</p>"
            "</div>"
        )

    # Errors and warnings are rendered by render_results as real st.error /
    # st.warning banners at the top of the page, where they belong. Repeating
    # them here in hand-coloured HTML meant two sources of truth for the same
    # state — and the literal #f87171 / #fbbf24 were dark-theme values that
    # would have been unreadable on white.

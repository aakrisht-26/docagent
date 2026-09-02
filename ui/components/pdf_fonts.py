"""The font the PDF export draws with, and what it can and cannot represent.

WHY THIS EXISTS. The export used ReportLab's default, which is base-14
**Helvetica with WinAnsiEncoding** — no font was registered anywhere. WinAnsi is
224 characters. Everything outside it renders as a filled box, silently.

That was not obvious from the output, because ReportLab quietly routes a few
characters to two other base-14 fonts: `≈ ≥ ≤ → ∑ √ ∞` come out of **Symbol**
and `✓` out of **ZapfDingbats**. So the failures looked arbitrary — an en dash
worked, a non-breaking hyphen did not, `≈` worked, an emoji did not — and the
pattern was three fonts' repertoires overlaid, not one font's.

Measured on a real export before the fix:

    12–18%          U+2013 en dash          rendered correctly
    nonIbreaking    U+2011 non-breaking     BOX
    ≈ 12 ms         U+2248                  correct, via Symbol
    I Extracted     U+2753 ❓               BOX
    II Document     U+2139 U+FE0F ℹ️        BOX BOX

WHAT THIS FIXES IT WITH. DejaVu Sans, vendored in `assets/fonts/`. **5,943
codepoints against WinAnsi's 224**, measured from its cmap. It covers every class the summariser was
observed to emit and lose: the non-breaking hyphen, `₹ ₽ ₩` and the rest of the
currency block, arrows beyond `→`, Latin Extended-A (`ā ř ș`), Greek, Cyrillic,
super/subscripts, and the full maths operators block.

ReportLab bundles Bitstream Vera, which would have cost nothing to use, and it
was measured and rejected: **293 codepoints**, missing U+2011, `₹`, every arrow,
all of Latin Extended-A, Greek and Cyrillic. It is Helvetica's problem again
with a different shape.

WHAT NO FONT FIXES: EMOJI. `❓ ℹ️ ⏱ 📊` live in the emoji blocks, which text
fonts do not carry — DejaVu has `U+2139 ℹ` but not `U+23F1 ⏱`, `U+2753 ❓` or
`U+1F4CA 📊`, and a colour emoji font is not something ReportLab can draw.
So emoji are handled in two different places, deliberately:

  - **Ours** are simply gone. Three section headers hardcoded `❓`, `ℹ️` and
    `⏱`; they were decoration on a heading that already says what it is.
  - **The model's** are replaced at render time by `sanitise_for_pdf()`, which
    is a last resort rather than the fix, and is confined to characters the
    registered font genuinely lacks.

DEGRADING RATHER THAN FAILING. If the font files are missing — a partial
checkout, a packaging mistake — `register_pdf_fonts()` returns the Helvetica
names instead of raising. The export then has the old defect, which is bad, but
an export that renders imperfectly beats a download button that 500s.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional, Set, Tuple

#: assets/fonts, relative to the repository root (this file is ui/components/).
_FONT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "assets", "fonts",
)

#: (reportlab name, filename) for the four faces the exporter asks for. Bold is
#: not optional — headings and `**bold**` both need it, and ReportLab does not
#: synthesise a bold face from a regular one.
_FACES = (
    ("DejaVuSans",             "DejaVuSans.ttf"),
    ("DejaVuSans-Bold",        "DejaVuSans-Bold.ttf"),
    ("DejaVuSans-Oblique",     "DejaVuSans-Oblique.ttf"),
    ("DejaVuSans-BoldOblique", "DejaVuSans-BoldOblique.ttf"),
)

#: What a character the font cannot draw is replaced with. Nothing, rather than
#: "?" or U+FFFD: the box was noise, and a visible marker is different noise.
#: An emoji that vanishes from a heading costs the reader nothing; a row of
#: replacement characters would cost them attention.
_EMOJI_FALLBACK = ""


@lru_cache(maxsize=1)
def register_pdf_fonts() -> Tuple[str, str, str, str]:
    """Register DejaVu with ReportLab. Returns (regular, bold, italic, bold-italic).

    Falls back to the Helvetica family if the files or ReportLab are missing, so
    the caller never has to handle an exception to produce a document.

    Cached: registration is global to the ReportLab process and re-running it on
    every export would reparse ~2.7 MB of TTF for nothing.
    """
    helvetica = ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique",
                 "Helvetica-BoldOblique")
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError:
        return helvetica

    paths = [(name, os.path.join(_FONT_DIR, fn)) for name, fn in _FACES]
    if not all(os.path.exists(p) for _, p in paths):
        return helvetica

    try:
        for name, path in paths:
            pdfmetrics.registerFont(TTFont(name, path))
        # Without the family mapping, <b> and <i> inside a Paragraph do not
        # resolve to the bold and oblique faces — the markup is accepted and
        # silently rendered in the regular face.
        pdfmetrics.registerFontFamily(
            "DejaVuSans", normal="DejaVuSans", bold="DejaVuSans-Bold",
            italic="DejaVuSans-Oblique", boldItalic="DejaVuSans-BoldOblique",
        )
    except Exception:
        return helvetica

    return ("DejaVuSans", "DejaVuSans-Bold", "DejaVuSans-Oblique",
            "DejaVuSans-BoldOblique")


@lru_cache(maxsize=1)
def _covered_codepoints() -> Optional[Set[int]]:
    """Every codepoint the registered regular face can actually draw.

    Read from the font's own cmap rather than hardcoded, so the answer stays
    true if the font is ever swapped. Returns None when it cannot be determined,
    which callers must treat as "do not touch the text" — guessing at coverage
    would corrupt documents to fix a cosmetic problem.
    """
    path = os.path.join(_FONT_DIR, "DejaVuSans.ttf")
    if not os.path.exists(path):
        return None
    try:
        from fontTools.ttLib import TTFont as _FTFont
    except ImportError:
        return None
    try:
        font = _FTFont(path, lazy=True)
        covered: Set[int] = set()
        for table in font["cmap"].tables:
            covered |= set(table.cmap.keys())
        font.close()
        return covered or None
    except Exception:
        return None


def sanitise_for_pdf(text: str) -> str:
    """Drop characters the PDF font cannot draw, so they cannot become boxes.

    A LAST RESORT, not the fix. The fix is the font: after registering DejaVu
    this removes emoji and almost nothing else, where against Helvetica it would
    have had to strip the non-breaking hyphen, `₹`, arrows and every accented
    character outside Latin-1 — which is mangling the document rather than
    rendering it.

    No-ops when coverage cannot be established, and leaves every ASCII character
    alone unconditionally, so the worst case is the behaviour we already had.
    """
    if not text:
        return text
    covered = _covered_codepoints()
    if covered is None:
        return text

    out = []
    for ch in text:
        cp = ord(ch)
        if cp < 128 or cp in covered or ch in "\n\r\t":
            out.append(ch)
        elif cp == 0xFE0F:          # variation selector; invisible either way
            continue
        else:
            out.append(_EMOJI_FALLBACK)
    # Deliberately NOT collapsing whitespace afterwards. Dropping an emoji can
    # leave a double space, which nobody notices; a global collapse would eat
    # the "   |   " separators in the metadata line, which everybody would.
    return "".join(out)

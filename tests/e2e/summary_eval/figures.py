"""Classify every figure in a summary against the text it summarises.

Per figure, exactly one of:

  stated       in the source, as written or after unit scaling: "704.8 k" for
               704,800, "71%" for "71 percent", "12" for "twelve", "2026" for
               "FY26", "51" for OCR's "to.51", "50%" for "half", "£0.19" for
               "19 pence", "1.5" for "time and a half"
  rounded      a source figure shown at lower precision: "412" for 412.4
  small        an integer under 10 with no unit; counts and ordinals are too
               common to be evidence, so reported and not scored
  year         a year (1900-2099) the source never states, or a placeholder
               year such as "202X"
  computed     not stated, but equal, to the precision shown, to arithmetic on
               source figures. `basis` says which arithmetic, in the order tried:
                 total     a column, group or row sum or mean in a source
                           table, or one column or group summed across tables
                 sentence  sum/difference/ratio/product/percentage/percent
                           change of figures in the SAME summary sentence or
                           table row -- the working is shown to the reader
                 row       the same, on figures in one source table row
                 passage   ... in one source prose passage (table lines excluded)
                 between   ... between two source table totals
               subtype is `total` for the first, `derived` for the rest
  unexplained  none of the above. `near` names the closest candidate within 2%,
               same-sentence candidates first, which separates an arithmetic
               slip from an invention; both are read by hand.

`fits` counts every candidate that matched, so a coincidental fit is visible.
How far each basis can be trusted is MEASURED, not assumed: see
`analyse_trials.py --calibrate`, which jitters computed figures and counts how
many still match. Wide pools (row, passage, between) match jittered decoys
often, so a figure computed on those bases is undetermined, not verified.

Known limit: a computed figure that happens to equal a source figure is counted
`stated` ("a 15-person increase" where 15 is also a headcount), so `computed`
is a lower bound.
"""
from __future__ import annotations

import bisect
import itertools
import re
from typing import Dict, Iterable, List, Optional, Tuple

CITATION = re.compile(r"\[(?:Source:\s*)?(?:Pages?|Sheets?)\s[^\]]*\]|【[^】]*】", re.I)
# "### 4.1 Title", "## 5. Title", "1. item", "**2.** item": numbering, not figures.
# The model often follows the number with U+202F, so any whitespace but a newline.
SECTION_NUMBER = re.compile(r"^([^\S\n]*(?:#+[^\S\n]*)?(?:\*\*)?)(?:\d+(?:\.\d+)+\.?|\d+\.)(?:\*\*)?(?=\s)", re.M)
# Not figures: the "19" of "COVID-19" (letter, hyphen) and the "25" of "2024-25".
_NOT_A_FIGURE = r"(?<![\w.,])(?<![A-Za-z][-‑‐])(?<!\d{4}[-‑–/])"
NUMBER = re.compile(
    r"(?P<cur>[$£€])?\s?"
    rf"(?P<num>{_NOT_A_FIGURE}-?\d{{1,3}}(?:,\d{{3}})+(?:\.\d+)?(?![\d])|{_NOT_A_FIGURE}-?\d+(?:\.\d+)?(?![\d,]\d|\d|(?<=(?:19|20)\d)[Xx]\b))"
    r"(?P<suffix>\s?(?:%|percent\b|per cent\b|k\b|K\b|thousand\b|mn\b|m\b|M\b|million\b|bn\b|billion\b))?"
)
YEAR_PLACEHOLDER = re.compile(r"(?<![\w.,])(?:19|20)\d[Xx]\b")
# Source only: digits wherever they sit, for OCR text ("to.51", "'312, 000:").
LOOSE_NUMBER = re.compile(r"\d{1,3}(?:,\s?\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
PENCE = re.compile(r"(\d+(?:\.\d+)?)\s?(?:pence|p)\b", re.I)
HEDGE = re.compile(r"(?:≈|~|approx\.?|approximately|roughly|about|around|nearly|almost|circa|some)\s*\**\s*[$£€]?\s*$", re.I)
SENTENCE_END = re.compile(r"(?<=[.;!?])\s+(?=[A-Z*(])")
SCALES = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6,
          "bn": 1e9, "billion": 1e9}
UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
         "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
         "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
         "seventeen": 17, "eighteen": 18, "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
        "seventy": 70, "eighty": 80, "ninety": 90}
WORD_NUMBER = re.compile(
    r"\b(?P<tens>twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)"
    r"(?:[\s-](?P<unit>one|two|three|four|five|six|seven|eight|nine))?\b"
    r"|\b(?P<solo>zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen)\b"
    r"(?P<mult>\s(?:hundred|thousand|million))?", re.I)
# Values stated in words, as a summary would print them.
WORDED = {r"\b(?:a|one)\s+half\b|\bhalved\b": (50.0,), r"\b(?:a|one)\s+third\b": (33.3, 33.33, 33),
          r"\b(?:a|one)\s+quarter\b": (25.0,), r"\bdoubled\b": (100.0,), r"\btripled\b": (200.0,),
          r"\btime\s+and\s+a\s+quarter\b": (1.25,), r"\btime\s+and\s+a\s+half\b": (1.5,),
          r"\b(?:a|per|one)\s+hundred\b": (100.0,), r"\b(?:a|per|one)\s+thousand\b": (1000.0,)}


def figures_in(text: str):
    for m in NUMBER.finditer(text):
        raw = m.group("num")
        suffix = (m.group("suffix") or "").strip().lower()
        yield {
            "token": m.group(0).strip(), "value": float(raw.replace(",", "")),
            "decimals": len(raw.split(".")[1]) if "." in raw else 0,
            "scale": SCALES.get(suffix, 1.0),
            "percent": suffix in ("%", "percent", "per cent"),
            "money": bool(m.group("cur")), "comma": "," in raw,
            "start": m.start("cur") if m.group("cur") else m.start("num"), "end": m.end(),
        }


def _word_numbers(text: str) -> Iterable[float]:
    for m in WORD_NUMBER.finditer(text):
        if m.group("tens"):
            yield TENS[m.group("tens").lower()] + (UNITS[m.group("unit").lower()] if m.group("unit") else 0)
        else:
            base = UNITS[m.group("solo").lower()]
            mult = (m.group("mult") or "").strip().lower()
            yield base * {"hundred": 100, "thousand": 1e3, "million": 1e6}.get(mult, 1)


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


def _tables(text: str) -> List[Tuple[List[List[str]], List[str]]]:
    """Blocks of 3+ consecutive lines whose cells right-align into columns, as
    the reader's `to_string` dump produces. Returns (rows, raw lines)."""
    tables, block = [], []
    for line in text.splitlines() + [""]:
        if line.strip() and not line.startswith("[Sheet:") and not line.startswith("- Total Rows"):
            block.append(line.rstrip())
            continue
        if len(block) >= 3:
            width = max(len(l) for l in block)
            padded = [l.ljust(width) for l in block]
            edges = [p for p in range(width)
                     if all(l[p] != " " and (p + 1 == width or l[p + 1] == " ") for l in padded)]
            if len(edges) >= 2:
                rows = []
                for l in padded:
                    cells, start = [], 0
                    for e in edges:
                        cells.append(l[start:e + 1].strip())
                        start = e + 1
                    rows.append(cells)
                tables.append((rows, block[:]))
        block = []
    return tables


def _number(cell: str) -> Optional[float]:
    try:
        return float(cell.replace(",", "").replace("$", ""))
    except ValueError:
        return None


_OPS = (("sum", lambda a, b: a + b, "{a}+{b}"), ("difference", lambda a, b: a - b, "{a}-{b}"),
        ("ratio", lambda a, b: a / b, "{a}/{b}"), ("percentage", lambda a, b: a / b * 100, "{a}/{b}*100"),
        ("percent change", lambda a, b: (a - b) / b * 100, "({a}-{b})/{b}*100"))


def _pairwise(values: List[float], where: str) -> Iterable[Tuple[float, str]]:
    for a, b in itertools.permutations(values, 2):
        for name, fn, form in _OPS:
            if b == 0 and name in ("ratio", "percentage", "percent change"):
                continue
            yield fn(a, b), f"{form.format(a=f'{a:g}', b=f'{b:g}')} ({name}, {where})"


class _Pool:
    def __init__(self, items: Iterable[Tuple[float, str]]):
        pairs = sorted(items)
        self.values = [v for v, _ in pairs]
        self.labels = [w for _, w in pairs]

    def matches(self, value: float, tol: float) -> List[str]:
        lo = bisect.bisect_left(self.values, value - tol)
        hi = bisect.bisect_right(self.values, value + tol)
        return self.labels[lo:hi]

    def nearest(self, value: float, rel: float) -> Optional[Tuple[float, str]]:
        i = bisect.bisect_left(self.values, value)
        best = None
        for j in (i - 1, i):
            if 0 <= j < len(self.values) and value:
                off = abs(self.values[j] - value) / abs(value)
                if off <= rel and (best is None or off < best[0]):
                    best = (off, j)
        return (self.values[best[1]], self.labels[best[1]]) if best else None


class Source:
    def __init__(self, text: str):
        self.values: List[float] = []
        tables = _tables(text)
        table_lines = {line.strip() for _rows, raw in tables for line in raw}

        prose_pairs: List[Tuple[float, str]] = []
        for block in (b for b in re.split(r"\n\s*\n", text) if b.strip()):
            nums, prose_nums = [], []
            for line in block.splitlines():
                found = []
                for f in figures_in(line):
                    found.append(f["value"])
                    if f["scale"] != 1.0:
                        found.append(f["value"] * f["scale"])
                found += list(_word_numbers(line))
                found += [2000 + int(m.group(1)) for m in re.finditer(r"\bFY\s?(\d{2})\b", line)]
                nums += found
                if line.strip() not in table_lines:
                    prose_nums += found
            self.values += nums
            distinct = sorted(set(v for v in prose_nums if v))[:120]
            prose_pairs += list(_pairwise(distinct, "same passage"))
        # Stated-only extras, never used as operands: OCR-garbled numerals, pence
        # as pounds, and values written in words.
        self.values += [float(re.sub(r"[,\s]", "", m)) for m in LOOSE_NUMBER.findall(text)]
        self.values += [float(m) / 100 for m in PENCE.findall(text)]
        for pattern, worded in WORDED.items():
            if re.search(pattern, text, re.I):
                self.values += list(worded)

        totals: Dict[float, str] = {}
        row_pairs: List[Tuple[float, str]] = []
        across: Dict[str, List[List[float]]] = {}
        for rows, _raw in tables:
            header, body = rows[0], rows[1:]
            columns = list(zip(*body))
            numeric = [i for i, col in enumerate(columns)
                       if sum(_number(c) is not None for c in col) >= 0.8 * len(col)]
            labels = [i for i in range(len(header)) if i not in numeric]
            for row in body:
                nums = [v for v in (_number(c) for c in row) if v is not None]
                row_pairs += list(_pairwise(nums, "same table row"))
                if len(nums) >= 3:
                    totals.setdefault(sum(nums), f"sum of row {row[labels[0]] if labels else ''}".rstrip())
            for i in numeric:
                vals = [v for v in (_number(c) for c in columns[i]) if v is not None]
                if len(vals) >= 2:
                    totals.setdefault(sum(vals), f"sum of column {header[i]}")
                    totals.setdefault(sum(vals) / len(vals), f"mean of column {header[i]}")
                across.setdefault(header[i], []).append(vals)
                for j in labels:
                    groups: Dict[str, List[float]] = {}
                    for row in body:
                        v = _number(row[i])
                        if v is not None:
                            groups.setdefault(row[j], []).append(v)
                    for key, vs in groups.items():
                        across.setdefault(f"{header[i]} where {header[j]}={key}", []).append(vs)
                        if len(vs) >= 2:
                            totals.setdefault(sum(vs), f"sum of {header[i]} where {header[j]}={key}")
                            totals.setdefault(sum(vs) / len(vs), f"mean of {header[i]} where {header[j]}={key}")
        # One column, or one group, repeated across sheets: quarterly tables.
        for name, parts in across.items():
            if len(parts) >= 2:
                vals = [v for part in parts for v in part]
                totals.setdefault(sum(vals), f"sum of {name} across {len(parts)} tables")
                totals.setdefault(sum(vals) / len(vals), f"mean of {name} across {len(parts)} tables")
        total_values = sorted(set(v for v in totals if v))[:80]
        between = [(v, w.replace(")", ", between totals)", 1) if w.endswith(")") else w)
                   for v, w in _pairwise(total_values, "between table totals")]

        self.totals = _Pool(totals.items())
        self.pools = [("row", _Pool(row_pairs)), ("passage", _Pool(prose_pairs)), ("between", _Pool(between))]
        self.aggregates = totals

    def stated(self, value: float) -> bool:
        return any(_close(value, v) for v in self.values)


def prepare(summary: str) -> str:
    return SECTION_NUMBER.sub(lambda m: m.group(1), CITATION.sub(" ", summary))


def _span(text: str, start: int, end: int) -> Tuple[int, int]:
    """The summary sentence holding [start, end): a whole line for a table row."""
    ls = text.rfind("\n", 0, start) + 1
    le = text.find("\n", end)
    le = len(text) if le < 0 else le
    if text[ls:le].lstrip().startswith("|"):
        return ls, le
    s = ls
    for m in SENTENCE_END.finditer(text, ls, le):
        if m.start() >= start:
            return s, m.start()
        s = m.end()
    return s, le


def _local(operands: List[dict]) -> List[Tuple[float, str]]:
    values: List[float] = []
    for g in operands:
        values.append(g["value"] * g["scale"])
        if g["percent"]:
            values.append(g["value"] / 100)
    values = sorted(set(values))[:24]
    out = list(_pairwise(values, "same summary sentence"))
    out += [(a * b, f"{a:g}*{b:g} (product, same summary sentence)")
            for a, b in itertools.combinations(values, 2)]
    if len(operands) >= 3:
        out.append((sum(g["value"] * g["scale"] for g in operands), "sum of the sentence's other figures"))
    return out


def _row(text: str, token: str, value: float, start: int, end: int, **fields) -> dict:
    s0, s1 = _span(text, start, end)
    row = {"token": token, "value": value, "category": "unexplained", "subtype": "", "basis": "",
           "why": "", "fits": 0, "near": "", "start": start, "span": [s0, s1],
           "hedged": bool(HEDGE.search(text[max(0, start - 16):start])),
           "percent": False, "decimals": 0, "money": False,
           "context": text[max(0, start - 60): end + 40].replace("\n", " ")}
    row.update(fields)
    return row


def classify(summary: str, source: Source, prepared: bool = False) -> List[dict]:
    text = summary if prepared else prepare(summary)
    out = []
    all_figs = list(figures_in(text))
    for f in all_figs:
        v, scaled = f["value"], f["value"] * f["scale"]
        row = _row(text, f["token"], scaled, f["start"], f["end"],
                   percent=f["percent"], decimals=f["decimals"], money=f["money"])
        s0, s1 = row["span"]
        if source.stated(v) or source.stated(scaled):
            row["category"] = "stated"
        elif (f["decimals"] == 0 and not f["percent"] and not f["money"] and f["scale"] == 1.0
              and v.is_integer() and 0 <= v < 10):
            row["category"] = "small"
        elif (f["decimals"] == 0 and not f["percent"] and not f["comma"] and f["scale"] == 1.0
              and 1900 <= v <= 2099):
            row["category"] = "year"
        else:
            for s in source.values:
                unit = f["scale"] if f["scale"] != 1.0 else 1.0
                if s and abs(round(s / unit, f["decimals"]) - v) < 1e-9 and not _close(s / unit, v):
                    row["category"], row["why"] = "rounded", f"{s:g}"
                    break
        if row["category"] == "unexplained":
            tol = 0.5 * 10 ** -f["decimals"]
            probes = [(v, tol)] + ([(scaled, tol * f["scale"])] if f["scale"] != 1.0 else [])
            operands = [g for g in all_figs if s0 <= g["start"] < s1 and g["start"] != f["start"]]
            local = _Pool(_local(operands))
            pools = [("total", source.totals), ("sentence", local)] + source.pools
            for basis, pool in pools:
                hits = [h for value, t in probes for h in pool.matches(value, t)]
                if basis == "sentence" or f["percent"]:   # a fall written as a positive percentage
                    hits += [h for value, t in probes for h in pool.matches(-value, t)]
                if hits and row["category"] == "unexplained":
                    row["category"], row["basis"], row["why"] = "computed", basis, hits[0]
                    row["subtype"] = "total" if basis == "total" else "derived"
                row["fits"] += len(hits)
            if row["category"] == "unexplained":
                near = local.nearest(scaled, 0.02) or local.nearest(-scaled, 0.02)
                if near is None:
                    for _basis, pool in [("total", source.totals)] + source.pools:
                        cand = pool.nearest(scaled, 0.02)
                        if cand and (near is None or abs(cand[0] - scaled) < abs(near[0] - scaled)):
                            near = cand
                if near:
                    row["near"] = f"{near[0]:,.6g} = {near[1]}"
        out.append(row)
    for m in YEAR_PLACEHOLDER.finditer(text):
        out.append(_row(text, m.group(0), 0.0, m.start(), m.end(), category="year", why="placeholder year"))
    return sorted(out, key=lambda r: r["start"])

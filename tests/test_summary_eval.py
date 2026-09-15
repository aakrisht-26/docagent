"""Tests for the summary-figures eval, so its numbers cannot rot.

WHAT THE EVAL CLAIMS. 42 of 85 recorded summaries state at least one false
figure about their document -- a year it never gives, wrong arithmetic, or a
number with no basis -- and the two failures that started it, a computed total
and an invented year, have different rates and different causes. Method and
per-figure verdicts are in tests/e2e/summary_eval/RESULTS.md.

THREE THINGS ARE PINNED, and each has already been wrong once.

1. THE CASE THAT STARTED THIS. "$2,379,900 in total revenue for Q3 2024": a
   correct column sum beside a year no sheet states, and in the same summary
   two averages that are simply miscalculated.

2. THE ARTEFACTS HAND REVIEW FOUND. Each would have been reported as a
   fabrication: section numbers followed by U+202F, pence written as pounds,
   "time and a quarter", the 19 of COVID-19, the 25 of 2024-25, OCR numerals,
   a fall written as a positive percentage, totals across quarterly sheets.

3. THE ADJUDICATION AGAINST ITS OWN ARITHMETIC, AND RESULTS.md AGAINST THE
   ADJUDICATION. A `correct` verdict must equal its expression and a `wrong`
   one must not; planting either mistake must be caught. The published table
   must be what the recorded data produces.

No API calls: the summaries are recorded.

Run:
    pytest tests/test_summary_eval.py -v
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "tests" / "e2e" / "summary_eval"
TRIALS = EVAL_DIR / "trials.jsonl"


def _load(name: str, path: Path = None):
    """By path and under a unique name. Nothing is added to sys.path, so no
    module in the eval directory can shadow another eval's module of the same
    name -- the order-dependent failure test_extraction_eval.py describes."""
    spec = importlib.util.spec_from_file_location(f"summary_eval_{name}_under_test",
                                                  path or EVAL_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


figures = _load("figures")
adjudicate = _load("adjudicate")
analyse = _load("analyse")
SOURCES = json.loads((EVAL_DIR / "trials.sources.json").read_text(encoding="utf-8"))

#: From the summary that started this investigation.
KNOWN_SUMMARY = (
    "The company generated $2,379,900 in total revenue for Q3 2024, derived from eight "
    "line-items spanning four geographic regions. [Page 1]\n"
    "Orion Controller: 1,080 units sold for $1,329,700 total revenue; average price $1,231.57. [Page 1]\n"
    "LIDAR Module: 440 units sold for $381,200 total revenue; average price $866.82. [Page 1]\n"
    "Service Contract: 155 units sold for $669,000 total revenue; average price $4,316.13. [Page 1]\n"
)


def _classify(summary: str, source_text: str):
    return {r["token"]: r for r in figures.classify(summary, figures.Source(source_text))}


class TestTheCaseThatStartedThis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = _classify(KNOWN_SUMMARY, SOURCES["sample_sales.xlsx"]["full_text"])

    def test_the_revenue_figure_is_a_correct_column_sum(self):
        row = self.rows["$2,379,900"]
        self.assertEqual((row["category"], row["basis"]), ("computed", "total"))
        self.assertEqual(row["why"], "sum of column Revenue")

    def test_the_year_is_one_no_sheet_states(self):
        self.assertEqual(self.rows["2024"]["category"], "year")

    def test_a_miscalculated_average_is_unexplained_and_the_right_one_is_named(self):
        """1,329,700 / 1,080 is 1,231.20. The near-miss is what separates an
        arithmetic slip from an invention when a person reads the list."""
        row = self.rows["$1,231.57"]
        self.assertEqual(row["category"], "unexplained")
        self.assertTrue(row["near"].startswith("1,231.2 = "), row["near"])
        self.assertIn("/1080", row["near"])
        self.assertEqual(self.rows["$866.82"]["category"], "unexplained")

    def test_a_correct_average_is_derived_from_its_own_sentence(self):
        row = self.rows["$4,316.13"]
        self.assertEqual((row["category"], row["basis"]), ("computed", "sentence"))

    def test_a_figure_in_the_sheet_is_stated(self):
        rows = _classify("North sold 320 Orion Controllers at $1,250 each.",
                         SOURCES["sample_sales.xlsx"]["full_text"])
        self.assertEqual({t: r["category"] for t, r in rows.items()},
                         {"320": "stated", "$1,250": "stated"})


class TestArtefactsAreNotFabrications(unittest.TestCase):
    """Every case here was read by hand in a recorded summary and, before the
    fix, scored as a figure absent from the source."""

    def test_section_numbers_are_not_figures_even_after_a_narrow_space(self):
        self.assertEqual(figures.classify("### 4.1 Loss & Damage\n## 5. Driver Workforce\n",
                                          figures.Source("")), [])

    def test_a_disease_name_and_a_year_range_carry_one_figure(self):
        rows = figures.classify("Review (2024‑25), the first post‑COVID‑19 revaluation.",
                                figures.Source(""))
        self.assertEqual([(r["token"], r["category"]) for r in rows], [("2024", "year")])

    def test_a_placeholder_year_is_a_year_and_not_the_number_20(self):
        rows = figures.classify("Driver scorecard launched H2 202X.", figures.Source(""))
        self.assertEqual([(r["token"], r["category"]) for r in rows], [("202X", "year")])

    def test_pence_are_pounds(self):
        rows = _classify("£0.04 per mono page, £0.19 per colour page.",
                         "Printing is charged at 4 pence per mono page and 19 pence per colour page.")
        self.assertEqual({r["category"] for r in rows.values()}, {"stated"})

    def test_rates_written_in_words_are_stated(self):
        rows = _classify("Overtime: 1.25 × weekdays, 1.5 × Sundays; 0.4 per 1,000 driving hours.",
                         "Overtime is paid at time\nand a quarter on weekdays and time and a half on "
                         "Sundays. Infringements fell to 0.4 per thousand driving hours.")
        self.assertEqual({t: r["category"] for t, r in rows.items()},
                         {"1.25": "stated", "1.5": "stated", "0.4": "stated", "1,000": "stated"})

    def test_ocr_garbled_source_numerals_still_count(self):
        rows = _classify("Headcount rose from 42 to 51 and cloud spend was $312,000.",
                         "Total engineering headcount grew from 42 to.51. Cloud spend was'312, 000: dollars.")
        self.assertEqual({r["category"] for r in rows.values()}, {"stated"})

    def test_a_fall_written_as_a_positive_percentage_is_computed(self):
        rows = _classify("an ≈84 % reduction in drift",
                         "Mean drift fell from 41.3 milli-g to 6.8 milli-g across the validation devices.")
        self.assertEqual(rows["84 %"]["category"], "computed")

    def test_a_column_summed_across_quarterly_sheets_is_a_total(self):
        source = ("[Sheet: Q1]\n- Total Rows: 2\n\nRegion Revenue\n North     100\n South     200\n\n"
                  "[Sheet: Q2]\n- Total Rows: 2\n\nRegion Revenue\n North     150\n South     250\n")
        row = _classify("Full-year revenue was 700.", source)["700"]
        self.assertEqual((row["category"], row["basis"]), ("computed", "total"))
        self.assertIn("across 2 tables", row["why"])


class TestTheCalibrationClaim(unittest.TestCase):
    """RESULTS.md trusts the total and sentence bases and distrusts the rest.
    That is a measured claim: jitter each computed figure and see whether it
    still matches."""

    @classmethod
    def setUpClass(cls):
        table = analyse.calibrate(*analyse.load(TRIALS))
        cls.pooled = Counter()
        cls.decoys = Counter()
        for (basis, _kind), counts in table.items():
            hit, total = analyse.coincidence(counts, basis)
            group = basis if basis in ("total", "sentence") else "wide"
            cls.pooled[group] += hit
            cls.decoys[group] += total

    def test_the_trusted_bases_rarely_match_a_wrong_figure(self):
        for basis in ("total", "sentence"):
            with self.subTest(basis=basis):
                self.assertGreater(self.decoys[basis], 500)
                self.assertLess(self.pooled[basis] / self.decoys[basis], 0.10)

    def test_the_wide_bases_match_a_wrong_figure_more_often_than_not(self):
        self.assertGreater(self.pooled["wide"] / self.decoys["wide"], 0.50)


class TestTheAdjudicationHoldsToItsArithmetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.per_summary, cls.problems = adjudicate.adjudicate(TRIALS)
        cls.rates = adjudicate.rates(cls.per_summary)
        cls.rules = json.loads((EVAL_DIR / "adjudication.json").read_text(encoding="utf-8"))

    def _with_rules(self, edit):
        data = json.loads(json.dumps(self.rules))
        edit(data["rules"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "adjudication.json"
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return adjudicate.adjudicate(TRIALS, path)[1]

    @staticmethod
    def _rule(rules, fixture, token):
        return next(r for r in rules if r["fixture"] == fixture and token in r.get("tokens", []))

    def test_every_figure_has_a_verdict_that_agrees_with_its_arithmetic(self):
        self.assertEqual(self.problems, [])

    def test_the_headline(self):
        expected = {
            "invented year": (21, 85), "computed total, correct": (19, 85),
            "computed total, wrong": (8, 85), "derived figure, correct": (61, 85),
            "wrong figure": (27, 85), "  gross error": (16, 85), "  slips only": (11, 85),
            "invented figure": (8, 85), "outside figure": (24, 85), "restatement": (7, 85),
            "year, wrong or invented": (42, 85), "any figure not in source": (74, 85),
        }
        self.assertEqual(self.rates["all"], expected)

    def test_invented_years_happen_only_where_the_source_has_none(self):
        self.assertEqual(self.rates["e2e"]["invented year"], (21, 45))
        self.assertEqual(self.rates["eval"]["invented year"], (0, 40))

    def test_figure_counts(self):
        self.assertEqual(adjudicate.figure_counts(self.per_summary), Counter({
            "correct:derived": 340, "correct:total": 228, "outside": 74, "wrong:gross": 71,
            "wrong:slip": 45, "year": 39, "invented": 16, "restated": 8}))

    def test_results_md_publishes_what_the_data_produces(self):
        lines = (EVAL_DIR / "RESULTS.md").read_text(encoding="utf-8").splitlines()
        for flag in adjudicate.FLAGS:
            label = flag.strip()
            row = next((l for l in lines if l.replace("*", "").startswith(f"| {label} |")), None)
            self.assertIsNotNone(row, f"no headline row for {label!r}")
            cells = [c.strip().strip("*") for c in row.strip("|").split("|")]
            for group, cell in zip(("all", "e2e", "eval"), cells[1:]):
                k, n = self.rates[group][flag]
                with self.subTest(flag=label, group=group):
                    self.assertTrue(cell.startswith(f"{k}/{n} "), cell)

    def test_a_right_figure_marked_wrong_is_caught(self):
        def plant(rules):
            self._rule(rules, "fin_quarterly", "43.7%")["verdict"] = "wrong"
        self.assertTrue(any(p.startswith("ACTUALLY RIGHT") and "43.7" in p for p in self._with_rules(plant)))

    def test_a_wrong_figure_marked_right_is_caught(self):
        def plant(rules):
            self._rule(rules, "fin_quarterly", "23.3%")["verdict"] = "correct"
        self.assertTrue(any(p.startswith("NOT EQUAL") and "23.3" in p for p in self._with_rules(plant)))

    def test_a_figure_with_no_verdict_is_caught(self):
        def plant(rules):
            rules.remove(self._rule(rules, "sample_sales.xlsx", "$1,247,300"))
        self.assertTrue(any(p.startswith("NO RULE") and "1,247,300" in p for p in self._with_rules(plant)))

    def test_a_rule_that_matches_nothing_is_caught(self):
        def plant(rules):
            rules.insert(0, {"fixture": "fin_quarterly", "tokens": ["999.9%"], "verdict": "invented", "note": "x"})
        self.assertTrue(any(p.startswith("UNUSED RULE") and "999.9%" in p for p in self._with_rules(plant)))

    def test_a_hedge_buys_one_digit_not_one_percent(self):
        """The first version allowed 1% on percentages and so passed
        '≈ 99.5 %' for a share of 99.23%."""
        self.assertFalse(adjudicate.shown_equal("99.5%", True, 99.2341))
        self.assertTrue(adjudicate.shown_equal("20%", True, 19.13))
        self.assertFalse(adjudicate.shown_equal("20%", False, 19.13))
        self.assertTrue(adjudicate.shown_equal("$1,232", True, 1231.2))
        self.assertFalse(adjudicate.shown_equal("$384,000", False, 384888))
        self.assertTrue(adjudicate.shown_equal("$242,800", False, 242810))


class TestTheChoiceOfResponse(unittest.TestCase):
    """RESULTS.md recommends flagging only unstated years. That rests on four
    candidate checks and regeneration measured on the recording; if those
    numbers move, the recommendation has to be argued again."""

    @classmethod
    def setUpClass(cls):
        options = _load("options")
        cls.options = options
        cls.per_summary, _problems = adjudicate.adjudicate(TRIALS)
        trials = {(t["fixture"], t["round"]): t for t in map(json.loads, open(TRIALS, encoding="utf-8"))}
        cls.scores = {name: options.score(flags, cls.per_summary)
                      for name, flags in options.candidate_flags(cls.per_summary, trials, SOURCES).items()}

    def test_each_candidate_check(self):
        # (summaries flagged, of which state a false figure, figures flagged, false statements among them)
        expected = {
            "years the source never states": (22, 21, 40, 39),
            "years and figures no arithmetic explains": (56, 39, 170, 85),
            "every figure absent from the source": (74, 42, 821, 171),
            "extraction's unverified_numbers": (43, 31, 261, 55),
        }
        got = {name: (s["summaries flagged"], s["flagged with a false figure"], s["flags"], s["false flags"])
               for name, s in self.scores.items()}
        self.assertEqual(got, expected)
        self.assertEqual({s["false-figure summaries"] for s in self.scores.values()}, {42})

    def test_only_the_year_check_flags_mostly_false_statements(self):
        precision = {name: s["false flags"] / s["flags"] for name, s in self.scores.items()}
        self.assertEqual(max(precision, key=precision.get), "years the source never states")
        self.assertGreater(precision["years the source never states"], 0.9)
        self.assertTrue(all(p <= 0.5 for name, p in precision.items() if name != "years the source never states"))

    def test_regeneration_would_mostly_repeat_the_failure(self):
        again, now = self.options.regeneration(self.per_summary)
        self.assertEqual((round(again, 1), now), (34.8, 42))
        again, now = self.options.regeneration(self.per_summary, ("year",))
        self.assertEqual((round(again, 1), now), (15.4, 21))

    def test_results_md_states_these_numbers(self):
        text = (EVAL_DIR / "RESULTS.md").read_text(encoding="utf-8")
        for phrase in ("40 flags over 22 summaries", "170 flags over 56 summaries", "821 flags over 74 summaries",
                       "261 flags over 43 summaries", "34.8 of the 42", "15.4 of 21"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)


class TestTheRecordingIsOfTheFixtures(unittest.TestCase):
    def test_every_summary_came_from_the_model_five_times_per_fixture(self):
        trials = [json.loads(line) for line in open(TRIALS, encoding="utf-8")]
        self.assertEqual(len(trials), 85)
        self.assertTrue(all((t["method"] or "").startswith("llm_") for t in trials))
        self.assertEqual(set(Counter(t["fixture"] for t in trials).values()), {5})

    def test_the_dated_sources_are_the_extraction_eval_documents_verbatim(self):
        content = _load("fixture_content", ROOT / "tests" / "e2e" / "extraction_eval" / "fixture_content.py")
        recorded = {k: v["full_text"] for k, v in SOURCES.items() if v["kind"] == "eval"}
        self.assertEqual(recorded, dict(content.DOCUMENTS))


if __name__ == "__main__":
    unittest.main(verbosity=2)

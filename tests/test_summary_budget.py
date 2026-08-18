"""Tests for the reasoning allowance on summary length presets.

The presets are CONTENT budgets — what a "Standard" summary is allowed to say.
`max_tokens` is not that: it caps content plus the reasoning tokens a model
spends before writing anything. Under `openai/gpt-oss-120b` every preset
therefore delivered less than it was designed to, silently.

The allowance is added at the call rather than folded into the preset numbers,
because those numbers encode intent. Raising them would change what "Standard"
means; adding room restores what it always meant.

Sizing, measured rather than guessed:

  reasoning vs INPUT     prompt 448 -> 45, prompt 2460 -> 42, prompt 1575 -> 192
                         A 5.5x range of input barely moves it. Not input-scaled.
  reasoning vs OUTPUT    Concise 199, Standard 34, Detailed 126, Exhaustive 902
                         The directive moves it, by an order of magnitude.

So it is preset-sensitive in principle, and a flat constant in practice: the
same measurement gave Standard 34 in one run and 192 in another on identical
input, and fitting a curve to four noisy points would imply precision the data
does not support. 1024 clears the worst observation (902).

Run:
    pytest tests/test_summary_budget.py -v
"""

from __future__ import annotations

import unittest

from skills.summarization_skill import (
    _LENGTH_CONFIGS, _MAP_CONTENT_TOKENS, _PROVIDER_REQUEST_CEILING,
    _REASONING_ALLOWANCE, _with_reasoning_room,
)


class TestTheAllowanceIsApplied(unittest.TestCase):
    def test_it_clears_the_worst_measured_reasoning(self):
        """902 tokens on Exhaustive was the largest observed."""
        self.assertGreater(_REASONING_ALLOWANCE, 902)

    def test_presets_that_fit_get_the_full_allowance(self):
        for name in ("Concise", "Standard", "Detailed"):
            with self.subTest(preset=name):
                content = _LENGTH_CONFIGS[name]["max_tokens"]
                self.assertEqual(_with_reasoning_room(content),
                                 content + _REASONING_ALLOWANCE)

    def test_the_map_step_gets_it_too(self):
        """A truncated map chunk loses a section of the summary silently."""
        self.assertEqual(_with_reasoning_room(_MAP_CONTENT_TOKENS),
                         _MAP_CONTENT_TOKENS + _REASONING_ALLOWANCE)


class TestTheClampProtectsWhatAlreadyWorked(unittest.TestCase):
    """A fix for one preset must not break another.

    Groq counts prompt + max_tokens against the per-minute limit and REFUSES
    the request (413) rather than truncating. Measured across the eight
    configured keys: 5000 accepted by seven, 8000 refused by six, 9024 refused
    by all eight. Exhaustive was already failing on most keys; the allowance
    would have made it fail on every one.
    """

    def test_a_preset_at_the_ceiling_is_not_pushed_over_it(self):
        content = _LENGTH_CONFIGS["Exhaustive"]["max_tokens"]
        self.assertEqual(_with_reasoning_room(content), content,
                         "the allowance must not turn 'sometimes works' into "
                         "'never works'")

    def test_nothing_ever_asks_for_more_than_the_ceiling(self):
        for name, cfg in _LENGTH_CONFIGS.items():
            with self.subTest(preset=name):
                self.assertLessEqual(
                    _with_reasoning_room(cfg["max_tokens"]),
                    max(cfg["max_tokens"], _PROVIDER_REQUEST_CEILING))

    def test_a_preset_already_above_the_ceiling_is_left_alone(self):
        """Clamping DOWN would silently shorten it, which is a product change."""
        self.assertEqual(_with_reasoning_room(12000), 12000)

    def test_the_allowance_never_reduces_a_budget(self):
        for content in (100, 800, 3000, 5000, 8000, 12000):
            with self.subTest(content=content):
                self.assertGreaterEqual(_with_reasoning_room(content), content)


class TestPresetsStayDistinct(unittest.TestCase):
    """The allowance must not flatten the presets into each other."""

    def test_the_ordering_is_preserved(self):
        order = ["Concise", "Standard", "Detailed", "Exhaustive"]
        budgets = [_with_reasoning_room(_LENGTH_CONFIGS[n]["max_tokens"])
                   for n in order]
        self.assertEqual(budgets, sorted(budgets))

    def test_the_extremes_remain_far_apart(self):
        """Measured end to end on a 20-page report: Concise produced ~561
        tokens of summary against Exhaustive's ~2738, a 4.9x spread. On a
        2-page report, ~221 against ~1842, an 8.3x spread. The presets are
        visibly different documents, which is what matters to a reader.
        """
        concise = _with_reasoning_room(_LENGTH_CONFIGS["Concise"]["max_tokens"])
        exhaustive = _with_reasoning_room(_LENGTH_CONFIGS["Exhaustive"]["max_tokens"])
        self.assertGreater(exhaustive, concise * 3)

    def test_a_constant_allowance_cannot_invert_two_presets(self):
        """Adding the same number to each preserves every gap between them."""
        raw = sorted(c["max_tokens"] for c in _LENGTH_CONFIGS.values())
        for smaller, larger in zip(raw, raw[1:]):
            with self.subTest(pair=(smaller, larger)):
                self.assertLess(_with_reasoning_room(smaller),
                                _with_reasoning_room(larger))


if __name__ == "__main__":
    unittest.main(verbosity=2)

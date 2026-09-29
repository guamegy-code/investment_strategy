"""Noise-review comparisons preserve the deployed baseline and episode accounting."""

import sys
import unittest
from pathlib import Path

import pandas as pd

legacy = Path(__file__).resolve().parents[1] / "src" / "legacy-python"
sys.path.insert(0, str(legacy))
sys.path.insert(0, str(legacy / "validation"))

from strategy30_price_noise_review import (  # noqa: E402
    candidate_definition, shock_episodes,
)
from strategy30_signal_combination_review import STRATEGY30  # noqa: E402
from strategy_dsl import load_strategy_definition  # noqa: E402


class PriceNoiseTests(unittest.TestCase):
    def test_baseline_and_only_confirmation_rules_change(self):
        base = candidate_definition("PRICE70")
        self.assertEqual(candidate_definition("BASE30"),
                         load_strategy_definition(STRATEGY30))
        for name, entry_confirm, exit_confirm in (
            ("PERSIST2", 2, 3),
            ("QUICK_RELEASE", None, 1),
            ("PERSIST2_QUICK", 2, 1),
        ):
            with self.subTest(name=name):
                changed = candidate_definition(name)
                self.assertEqual(changed["target"], base["target"])
                self.assertEqual(changed["rebalance"], base["rebalance"])
                self.assertEqual(changed["state"]["credit_guard"],
                                 base["state"]["credit_guard"])
                rules = changed["state"]["shock_guard"]["rules"]
                self.assertEqual(rules[0].get("confirm"), entry_confirm)
                self.assertEqual(rules[1]["confirm"], exit_confirm)

    def test_episode_return_decomposition(self):
        dates = pd.bdate_range("2024-01-01", periods=5)
        states = ["FALSE", "TRUE", "TRUE", "FALSE", "FALSE"]
        history = pd.DataFrame({
            "Portfolio": [100, 100, 105, 105, 105],
            "NotificationContext": [
                {"state_values": {"shock_guard": state}} for state in states
            ],
        }, index=dates)
        baseline = pd.DataFrame({"Portfolio": [100, 100, 100, 100, 100]}, index=dates)
        qqq = pd.Series([100, 95, 90, 100, 100], index=dates)
        episodes = shock_episodes(history, baseline, qqq,
                                  start=str(dates[0].date()),
                                  end=str(dates[-1].date()))
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["Days"], 2)
        self.assertAlmostEqual(episodes[0]["AdvantageTotal"], .05)


if __name__ == "__main__":
    unittest.main()

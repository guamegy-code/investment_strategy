import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from experimental_strategies import StagedBearRetirementStrategy  # noqa: E402
from strategy import AllocationState  # noqa: E402


class StablePortfolio:
    def weights(self, prices):
        return {"QQQ": 0.30, "BND": 0.70, "BIL": 0.0}


def market(structural=True):
    if structural:
        qqq = {
            "Close": 85.0,
            "EMA20": 90.0,
            "EMA55": 95.0,
            "EMA200": 100.0,
            "ROC5": -4.0,
            "ROC20": -10.0,
            "EMA20_SLOPE5": -2.0,
            "ROC60": -12.0,
            "EMA200_SLOPE20": -1.0,
            "DRAWDOWN120": -0.15,
        }
    else:
        qqq = {
            "Close": 105.0,
            "EMA20": 103.0,
            "EMA55": 100.0,
            "EMA200": 98.0,
            "ROC5": 3.0,
            "ROC20": 5.0,
            "EMA20_SLOPE5": 2.0,
            "ROC60": 8.0,
            "EMA200_SLOPE20": 1.0,
            "DRAWDOWN120": 0.0,
        }
    return {
        "QQQ": qqq,
        "BND": {"Close": 100.0, "ROC40": 1.0},
        "BIL": {"Close": 100.0, "ROC40": 0.0},
    }


class StagedBearRetirementTests(unittest.TestCase):
    def setUp(self):
        self.date = pd.Timestamp("2024-01-02")
        self.strategy = StagedBearRetirementStrategy(5)
        self.strategy.state = AllocationState.BEAR
        self.strategy.safe_asset = "BND"
        self.strategy.last_safe_selection_month = self.date.to_period("M")
        self.strategy.last_rebalance_month = self.date.to_period("M")
        self.strategy.target = self.strategy._target_for_state()
        self.portfolio = StablePortfolio()

    def test_first_bear_stage_retains_thirty_percent_risk(self):
        self.assertEqual(
            self.strategy.target,
            {"QQQ": 0.30, "BND": 0.70, "BIL": 0.0},
        )

    def test_continued_structural_bear_activates_full_exit(self):
        signals = []
        for offset in range(5):
            signals.append(
                self.strategy.evaluate(
                    self.date + pd.Timedelta(days=offset),
                    market(structural=True),
                    self.portfolio,
                )
            )

        self.assertFalse(signals[3]["rebalance"])
        self.assertTrue(signals[4]["rebalance"])
        self.assertEqual(signals[4]["target"]["QQQ"], 0.0)
        self.assertEqual(signals[4]["reason"], "BEAR_STAGE2_0(continued=5)")

    def test_none_confirmation_keeps_thirty_percent_floor(self):
        strategy = StagedBearRetirementStrategy(None)
        strategy.state = AllocationState.BEAR
        strategy.safe_asset = "BND"
        strategy.last_safe_selection_month = self.date.to_period("M")
        strategy.last_rebalance_month = self.date.to_period("M")
        strategy.target = strategy._target_for_state()

        for offset in range(20):
            signal = strategy.evaluate(
                self.date + pd.Timedelta(days=offset),
                market(structural=True),
                self.portfolio,
            )

        self.assertEqual(signal["target"]["QQQ"], 0.30)
        self.assertFalse(strategy._full_bear_defense)


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from experimental_strategies import (  # noqa: E402
    StateOnlyTransitionRetirementStrategy,
)
from strategy import (  # noqa: E402
    AllocationState,
    RetirementAllocationSelectiveRebalanceStrategy,
)


class WeightedPortfolio:
    def __init__(self, qqq_weight):
        self.qqq_weight = qqq_weight

    def weights(self, prices):
        return {
            "QQQ": self.qqq_weight,
            "BND": 1.0 - self.qqq_weight,
            "BIL": 0.0,
        }


def caution_market():
    return {
        "QQQ": {
            "Close": 85.0,
            "EMA20": 90.0,
            "EMA55": 95.0,
            "EMA200": 100.0,
            "ROC5": -4.0,
            "ROC20": -10.0,
            "EMA20_SLOPE5": -2.0,
            "ROC60": 1.0,
            "EMA200_SLOPE20": 1.0,
            "DRAWDOWN120": -0.05,
        },
        "BND": {"Close": 100.0, "ROC40": 1.0},
        "BIL": {"Close": 100.0, "ROC40": 0.0},
    }


def recovery_market():
    return {
        "QQQ": {
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
        },
        "BND": {"Close": 100.0, "ROC40": 1.0},
        "BIL": {"Close": 100.0, "ROC40": 0.0},
    }


class StateOnlyTransitionRetirementTests(unittest.TestCase):
    def _strategy(self):
        date = pd.Timestamp("2024-01-02")
        strategy = StateOnlyTransitionRetirementStrategy()
        strategy.state = AllocationState.BULL
        strategy.safe_asset = "BND"
        strategy.target = {"QQQ": 0.70, "BND": 0.30, "BIL": 0.0}
        strategy.last_safe_selection_month = date.to_period("M")
        strategy.last_rebalance_month = date.to_period("M")
        return date, strategy

    def test_equal_target_state_transition_does_not_trade_inside_band(self):
        date, strategy = self._strategy()
        portfolio = WeightedPortfolio(0.70)

        signals = [
            strategy.evaluate(
                date + pd.Timedelta(days=offset), caution_market(), portfolio
            )
            for offset in range(3)
        ]

        self.assertEqual(strategy.state, AllocationState.CAUTION)
        self.assertFalse(signals[-1]["rebalance"])
        self.assertEqual(signals[-1]["reason"], "STATE_ONLY_BULL->CAUTION")

    def test_equal_target_transition_still_trades_outside_band(self):
        date, strategy = self._strategy()
        portfolio = WeightedPortfolio(0.76)

        signals = [
            strategy.evaluate(
                date + pd.Timedelta(days=offset), caution_market(), portfolio
            )
            for offset in range(3)
        ]

        self.assertTrue(signals[-1]["rebalance"])
        self.assertIn("MONTHLY_5PCT_BAND", signals[-1]["reason"])


class SelectiveRebalanceProductionTests(unittest.TestCase):
    def _strategy(self):
        date = pd.Timestamp("2024-01-02")
        strategy = RetirementAllocationSelectiveRebalanceStrategy()
        strategy.state = AllocationState.CAUTION
        strategy.safe_asset = "BND"
        strategy.target = {"QQQ": 0.70, "BND": 0.30, "BIL": 0.0}
        strategy.last_safe_selection_month = date.to_period("M")
        strategy.last_rebalance_month = date.to_period("M")
        return date, strategy

    def test_caution_to_bull_updates_state_without_inside_band_order(self):
        date, strategy = self._strategy()
        portfolio = WeightedPortfolio(0.70)

        signals = [
            strategy.evaluate(
                date + pd.Timedelta(days=offset), recovery_market(), portfolio
            )
            for offset in range(3)
        ]

        self.assertEqual(strategy.state, AllocationState.BULL)
        self.assertFalse(signals[-1]["rebalance"])
        self.assertEqual(signals[-1]["reason"], "STATE_ONLY_CAUTION->BULL")

    def test_caution_to_bull_keeps_order_when_outside_band(self):
        date, strategy = self._strategy()
        portfolio = WeightedPortfolio(0.76)

        signals = [
            strategy.evaluate(
                date + pd.Timedelta(days=offset), recovery_market(), portfolio
            )
            for offset in range(3)
        ]

        self.assertTrue(signals[-1]["rebalance"])
        self.assertIn("MONTHLY_5PCT_BAND", signals[-1]["reason"])


if __name__ == "__main__":
    unittest.main()

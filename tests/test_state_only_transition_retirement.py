import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from experimental_strategies import (  # noqa: E402
    RetirementAllocationAsymmetricProfitBandStrategy,
    RetirementAllocationProfitBandStrategy,
    RetirementAllocationProfitBandVXUSStrategy,
    StateOnlyTransitionRetirementStrategy,
)
from strategy import (  # noqa: E402
    AllocationState,
    RetirementAllocationStrategy,
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


class FixedWeightsPortfolio:
    def __init__(self, weights):
        self._weights = weights

    def weights(self, prices):
        return {
            ticker: self._weights.get(ticker, 0.0) for ticker in prices
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
        strategy = RetirementAllocationStrategy()
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


class AsymmetricProfitBandTests(unittest.TestCase):
    def _strategy(self, qqq_weight=0.74):
        date = pd.Timestamp("2024-01-02")
        strategy = RetirementAllocationAsymmetricProfitBandStrategy()
        strategy.state = AllocationState.BULL
        strategy.safe_asset = "BND"
        strategy.target = {"QQQ": 0.70, "BND": 0.30, "BIL": 0.0}
        strategy.last_safe_selection_month = date.to_period("M")
        strategy.last_rebalance_month = date.to_period("M")
        return date, strategy, WeightedPortfolio(qqq_weight)

    def test_winning_qqq_is_preserved_inside_upper_band(self):
        date, strategy, portfolio = self._strategy(0.74)

        signal = strategy.evaluate(date, recovery_market(), portfolio)

        self.assertFalse(signal["rebalance"])
        self.assertAlmostEqual(signal["target"]["QQQ"], 0.74)
        self.assertAlmostEqual(signal["target"]["BND"], 0.26)

    def test_upper_band_restores_canonical_target(self):
        date, strategy, portfolio = self._strategy(0.81)

        signal = strategy.evaluate(date, recovery_market(), portfolio)

        self.assertTrue(signal["rebalance"])
        self.assertEqual(signal["target"], {"QQQ": 0.70, "BND": 0.30, "BIL": 0.0})
        self.assertIn("UPPER_QQQ_BAND_80.0%", signal["reason"])

    def test_state_transition_preserves_profit_inside_upper_band(self):
        date, strategy, portfolio = self._strategy(0.74)

        signals = [
            strategy.evaluate(
                date + pd.Timedelta(days=offset), caution_market(), portfolio
            )
            for offset in range(3)
        ]

        self.assertEqual(strategy.state, AllocationState.CAUTION)
        self.assertFalse(signals[-1]["rebalance"])
        self.assertAlmostEqual(signals[-1]["target"]["QQQ"], 0.74)

    def test_state_only_transition_can_keep_profit_drift(self):
        date, strategy, portfolio = self._strategy(0.74)
        strategy.state = AllocationState.CAUTION

        signals = [
            strategy.evaluate(
                date + pd.Timedelta(days=offset), recovery_market(), portfolio
            )
            for offset in range(3)
        ]

        self.assertEqual(strategy.state, AllocationState.BULL)
        self.assertFalse(signals[-1]["rebalance"])
        self.assertAlmostEqual(signals[-1]["target"]["QQQ"], 0.74)

    def test_safe_rotation_preserves_qqq_inside_upper_band(self):
        date, strategy, portfolio = self._strategy(0.74)
        strategy.last_safe_selection_month = date.to_period("M") - 1
        market = recovery_market()
        market["BND"]["ROC40"] = 0.0
        market["BIL"]["ROC40"] = 1.0

        signal = strategy.evaluate(date, market, portfolio)

        self.assertTrue(signal["rebalance"])
        self.assertAlmostEqual(signal["target"]["QQQ"], 0.74)
        self.assertAlmostEqual(signal["target"]["BIL"], 0.26)
        self.assertEqual(signal["target"]["BND"], 0.0)
        self.assertIn("SAFE_SLEEVE_ONLY_PRESERVE_QQQ", signal["reason"])


class SafeSleeveOnlyStrategyTests(unittest.TestCase):
    def _strategy(self, qqq_weight=0.74):
        date = pd.Timestamp("2024-01-02")
        strategy = RetirementAllocationProfitBandStrategy()
        strategy.state = AllocationState.BULL
        strategy.safe_asset = "BND"
        strategy.target = {"QQQ": 0.70, "BND": 0.30, "BIL": 0.0}
        strategy.last_safe_selection_month = date.to_period("M") - 1
        strategy.last_rebalance_month = date.to_period("M")
        return date, strategy, WeightedPortfolio(qqq_weight)

    def test_safe_rotation_preserves_qqq_and_rotates_only_safe_sleeve(self):
        date, strategy, portfolio = self._strategy(0.74)
        market = recovery_market()
        market["BND"]["ROC40"] = 0.0
        market["BIL"]["ROC40"] = 1.0

        signal = strategy.evaluate(date, market, portfolio)

        self.assertTrue(signal["rebalance"])
        self.assertAlmostEqual(signal["target"]["QQQ"], 0.74)
        self.assertEqual(signal["target"]["BND"], 0.0)
        self.assertAlmostEqual(signal["target"]["BIL"], 0.26)
        self.assertIn("SAFE_SLEEVE_ONLY_PRESERVE_QQQ", signal["reason"])

    def test_state_transition_preserves_profit_inside_upper_band(self):
        date, strategy, portfolio = self._strategy(0.74)
        strategy.last_safe_selection_month = date.to_period("M")

        signals = [
            strategy.evaluate(
                date + pd.Timedelta(days=offset), caution_market(), portfolio
            )
            for offset in range(3)
        ]

        self.assertFalse(signals[-1]["rebalance"])
        self.assertAlmostEqual(signals[-1]["target"]["QQQ"], 0.74)

    def test_safe_rotation_below_70_keeps_parent_restore_order(self):
        date, strategy, portfolio = self._strategy(0.68)
        market = recovery_market()
        market["BND"]["ROC40"] = 0.0
        market["BIL"]["ROC40"] = 1.0

        signal = strategy.evaluate(date, market, portfolio)

        self.assertTrue(signal["rebalance"])
        self.assertAlmostEqual(signal["target"]["QQQ"], 0.70)
        self.assertAlmostEqual(signal["target"]["BIL"], 0.30)

    def test_upper_band_restores_full_target(self):
        date, strategy, portfolio = self._strategy(0.81)
        strategy.last_safe_selection_month = date.to_period("M")

        signal = strategy.evaluate(date, recovery_market(), portfolio)

        self.assertTrue(signal["rebalance"])
        self.assertEqual(
            signal["target"],
            {"QQQ": 0.70, "BND": 0.30, "BIL": 0.0},
        )
        self.assertIn("UPPER_QQQ_BAND_80.0%", signal["reason"])


class SafeSleeveOnlyVXUSStrategyTests(unittest.TestCase):
    def _strategy(self, safe_asset, state=AllocationState.RECOVERY):
        date = pd.Timestamp("2024-01-02")
        strategy = RetirementAllocationProfitBandVXUSStrategy()
        strategy.state = state
        strategy.safe_asset = safe_asset
        strategy.target = strategy._target_for_state()
        strategy.last_safe_selection_month = date.to_period("M") - 1
        strategy.last_rebalance_month = date.to_period("M")
        market = recovery_market()
        market["VXUS"] = {"Close": 100.0}
        return date, strategy, market

    def test_recovery_bnd_to_bil_applies_full_vxus_target_change(self):
        date, strategy, market = self._strategy("BND")
        market["BND"]["ROC40"] = 0.0
        market["BIL"]["ROC40"] = 1.0
        portfolio = FixedWeightsPortfolio({
            "QQQ": 0.54,
            "VXUS": 0.18,
            "BND": 0.28,
        })

        signal = strategy.evaluate(date, market, portfolio)

        self.assertTrue(signal["rebalance"])
        self.assertAlmostEqual(signal["target"]["QQQ"], 0.50)
        self.assertEqual(signal["target"]["VXUS"], 0.0)
        self.assertAlmostEqual(signal["target"]["BIL"], 0.50)

    def test_recovery_bil_to_bnd_applies_full_vxus_target_change(self):
        date, strategy, market = self._strategy("BIL")
        market["BND"]["ROC40"] = 1.0
        market["BIL"]["ROC40"] = 0.0
        portfolio = FixedWeightsPortfolio({"QQQ": 0.54, "BIL": 0.46})

        signal = strategy.evaluate(date, market, portfolio)

        self.assertTrue(signal["rebalance"])
        self.assertAlmostEqual(signal["target"]["QQQ"], 0.50)
        self.assertAlmostEqual(signal["target"]["VXUS"], 0.20)
        self.assertAlmostEqual(signal["target"]["BND"], 0.30)
        self.assertEqual(signal["target"]["BIL"], 0.0)

    def test_bull_safe_rotation_preserves_qqq_profit(self):
        date, strategy, market = self._strategy(
            "BND", state=AllocationState.BULL
        )
        market["BND"]["ROC40"] = 0.0
        market["BIL"]["ROC40"] = 1.0
        portfolio = FixedWeightsPortfolio({"QQQ": 0.74, "BND": 0.26})

        signal = strategy.evaluate(date, market, portfolio)

        self.assertTrue(signal["rebalance"])
        self.assertAlmostEqual(signal["target"]["QQQ"], 0.74)
        self.assertEqual(signal["target"]["VXUS"], 0.0)
        self.assertAlmostEqual(signal["target"]["BIL"], 0.26)


if __name__ == "__main__":
    unittest.main()

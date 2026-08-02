import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy import (
    ASYMMETRIC_TREND_BAND,
    AllocationState,
    BASIC_BANG_DIV,
    DynamicRiskAllocationStrategy,
    RETIREMENT_7030_BAND,
    STATIC_703010_BAND,
    STATIC_70_BND10_BIL10_GLD10,
)


class StablePortfolio:
    def weights(self, prices):
        return {ticker: 0.0 for ticker in prices}


def market(
    close, ema20, ema55, ema200, roc5, roc20, slope5,
    roc60=0.0, slope200=0.0, drawdown120=0.0,
    bnd_roc=1.0, bil_roc=0.0,
):
    return {
        "QQQ": {
            "Close": close,
            "EMA20": ema20,
            "EMA55": ema55,
            "EMA200": ema200,
            "ROC5": roc5,
            "ROC20": roc20,
            "EMA20_SLOPE5": slope5,
            "ROC60": roc60,
            "EMA200_SLOPE20": slope200,
            "DRAWDOWN120": drawdown120,
            "RSI14": 50.0,
        },
        "BND": {"Close": 100.0, "ROC40": bnd_roc},
        "BIL": {"Close": 100.0, "ROC40": bil_roc},
        "GLD": {"Close": 100.0},
    }


BULL = market(110, 105, 100, 95, 2, 5, 1, roc60=5)
CAUTION = market(99, 101, 100, 95, -1, -2, -1, roc60=-1)
STRUCTURAL_BEAR = market(
    85, 90, 95, 100, -4, -10, -2,
    roc60=-12, slope200=-1, drawdown120=-0.15,
)
RECOVERY = market(101, 100, 102, 98, 2, 1, 1, roc60=1)


class DynamicAllocationTests(unittest.TestCase):
    def setUp(self):
        self.strategy = DynamicRiskAllocationStrategy()
        self.portfolio = StablePortfolio()
        self.date = pd.Timestamp("2024-01-02")

    def evaluate(self, data):
        result = self.strategy.evaluate(self.date, data, self.portfolio)
        self.date += pd.Timedelta(days=1)
        return result

    def test_final_configuration(self):
        self.assertEqual(self.strategy.SAFE_MOMENTUM_PERIOD, 40)
        self.assertEqual(self.strategy.SAFE_SWITCH_BUFFER, 0.25)
        self.assertEqual(
            self.strategy.STATE_WEIGHTS[AllocationState.BEAR], (0.00, 0.20)
        )
        self.assertEqual(
            self.strategy.STATE_WEIGHTS[AllocationState.RECOVERY], (0.50, 0.15)
        )

    def test_complete_state_cycle(self):
        self.evaluate(BULL)
        self.assertEqual(self.strategy.state, AllocationState.BULL)
        self.assertEqual(self.strategy.target["QQQ"], 0.70)

        for _ in range(3):
            caution_signal = self.evaluate(CAUTION)
        self.assertTrue(caution_signal["rebalance"])
        self.assertEqual(self.strategy.state, AllocationState.CAUTION)
        self.assertEqual(self.strategy.target["QQQ"], 0.70)

        for _ in range(10):
            bear_signal = self.evaluate(STRUCTURAL_BEAR)
        self.assertTrue(bear_signal["rebalance"])
        self.assertEqual(self.strategy.state, AllocationState.BEAR)
        self.assertEqual(self.strategy.target["QQQ"], 0.0)

        self.assertFalse(self.evaluate(RECOVERY)["rebalance"])
        self.assertTrue(self.evaluate(RECOVERY)["rebalance"])
        self.assertEqual(self.strategy.state, AllocationState.RECOVERY)
        self.assertEqual(self.strategy.target["QQQ"], 0.50)

        for _ in range(3):
            bull_signal = self.evaluate(BULL)
        self.assertTrue(bull_signal["rebalance"])
        self.assertEqual(self.strategy.state, AllocationState.BULL)

    def test_shallow_breakdown_does_not_enter_bear(self):
        self.evaluate(BULL)
        for _ in range(12):
            self.evaluate(CAUTION)
        self.assertNotEqual(self.strategy.state, AllocationState.BEAR)

    def test_safe_asset_rotates_monthly_with_buffer(self):
        weak_bonds = market(
            110, 105, 100, 95, 2, 5, 1,
            roc60=5, bnd_roc=-2, bil_roc=1,
        )
        self.evaluate(weak_bonds)
        self.assertEqual(self.strategy.safe_asset, "BIL")
        self.assertAlmostEqual(self.strategy.target["BIL"], 0.20)

        strong_bonds = market(
            110, 105, 100, 95, 2, 5, 1,
            roc60=5, bnd_roc=2, bil_roc=1,
        )
        self.date = pd.Timestamp("2024-02-01")
        result = self.evaluate(strong_bonds)
        self.assertTrue(result["rebalance"])
        self.assertEqual(self.strategy.safe_asset, "BND")
        self.assertAlmostEqual(self.strategy.target["BND"], 0.20)
        self.assertEqual(self.strategy.target["BIL"], 0.0)

    def test_retained_static_benchmarks_sum_to_one(self):
        for strategy_class in (
            STATIC_703010_BAND,
            STATIC_70_BND10_BIL10_GLD10,
        ):
            target = strategy_class().target
            self.assertAlmostEqual(sum(target.values()), 1.0)
            self.assertEqual(target["QQQ"], 0.70)

    def test_static_benchmark_tracks_dynamic_market_state(self):
        strategy = STATIC_70_BND10_BIL10_GLD10()

        strategy.evaluate(self.date, BULL, self.portfolio)
        self.assertEqual(strategy.state, AllocationState.BULL)
        for _ in range(3):
            strategy.evaluate(self.date, CAUTION, self.portfolio)

        self.assertEqual(strategy.state, AllocationState.CAUTION)
        self.assertEqual(
            strategy.target,
            {"QQQ": 0.70, "BND": 0.10, "BIL": 0.10, "GLD": 0.10},
        )

    def test_basic_bang_div_uses_qqq_rsi(self):
        strategy = BASIC_BANG_DIV()
        test_market = market(110, 105, 100, 95, 2, 5, 1, roc60=5)
        test_market["QQQ"]["RSI14"] = 85.0
        signal = strategy.evaluate(self.date, test_market, self.portfolio)
        self.assertTrue(signal["rebalance"])
        self.assertEqual(strategy.state, "BEAR")
        self.assertEqual(signal["target"]["QQQ"], 0.30)

    def test_retirement_band_uses_qqq_and_bnd(self):
        strategy = RETIREMENT_7030_BAND()
        signal = strategy.evaluate(self.date, BULL, self.portfolio)
        self.assertTrue(signal["rebalance"])
        self.assertEqual(signal["target"], {"QQQ": 0.70, "BND": 0.30})

    def test_asymmetric_trend_band_uses_qld_ema55(self):
        strategy = ASYMMETRIC_TREND_BAND()
        test_market = {**BULL, "QLD": {"Close": 100.0, "EMA55": 90.0}}
        signal = strategy.evaluate(self.date, test_market, self.portfolio)
        self.assertTrue(signal["rebalance"])
        self.assertIn("UPTREND_BUY_DIP_QLD", signal["reason"])


if __name__ == "__main__":
    unittest.main()

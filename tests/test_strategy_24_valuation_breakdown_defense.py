import sys
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "legacy-python"))

from strategy_dsl import DeclarativeStrategy  # noqa: E402


class PortfolioStub:
    def __init__(self, weights=None):
        self.current_weights = weights or {"QQQ": 1.0, "BIL": 0.0}

    def weights(self, prices):
        return {
            ticker: self.current_weights.get(ticker, 0.0)
            for ticker in prices
        }


def observations(valuation_score, *, regime="bull"):
    if regime == "risk4":
        qqq = {
            "Close": 90.0,
            "EMA20": 95.0,
            "EMA55": 96.0,
            "EMA200": 94.0,
            "ROC5": -3.0,
            "ROC20": 1.0,
            "ROC60": 2.0,
            "EMA20_SLOPE5": 1.0,
            "EMA200_SLOPE20": 1.0,
        }
    elif regime == "structural_bear":
        qqq = {
            "Close": 80.0,
            "EMA20": 90.0,
            "EMA55": 95.0,
            "EMA200": 100.0,
            "ROC5": -4.0,
            "ROC20": -10.0,
            "ROC60": -12.0,
            "EMA20_SLOPE5": -2.0,
            "EMA200_SLOPE20": -1.0,
        }
    else:
        qqq = {
            "Close": 105.0,
            "EMA20": 103.0,
            "EMA55": 100.0,
            "EMA200": 98.0,
            "ROC5": 3.0,
            "ROC20": 5.0,
            "ROC60": 8.0,
            "EMA20_SLOPE5": 2.0,
            "EMA200_SLOPE20": 1.0,
        }
    qqq["VALUATION_SCORE"] = valuation_score
    return {
        "QQQ": qqq,
        "SPY": {"Close": 105.0, "EMA20": 103.0, "ROC5": 1.0},
        "BIL": {"Close": 100.0},
    }


class Strategy24ValuationBreakdownDefenseTests(unittest.TestCase):
    def strategy(self):
        return DeclarativeStrategy.from_yaml(
            ROOT / "strategies" / "24_qqq_valuation_breakdown_defense.yaml"
        )

    def test_peak_breakdown_warns_then_six_risk_days_enter_defense(self):
        strategy = self.strategy()
        portfolio = PortfolioStub()
        start = pd.Timestamp("2025-01-02")

        strategy.evaluate(start, observations(70), portfolio)
        self.assertEqual(strategy.defense_mode, "NORMAL")
        self.assertEqual(strategy.valuation_peak, 70)

        warning = strategy.evaluate(
            start + pd.offsets.BDay(1), observations(45, regime="risk4"), portfolio
        )
        self.assertEqual(strategy.defense_mode, "WARNING")
        self.assertEqual(warning["target"], {"QQQ": 1.0, "BIL": 0.0})

        for day in range(2, 7):
            signal = strategy.evaluate(
                start + pd.offsets.BDay(day),
                observations(45, regime="risk4"),
                portfolio,
            )
            self.assertEqual(strategy.defense_mode, "WARNING")
            self.assertEqual(signal["target"], {"QQQ": 1.0, "BIL": 0.0})

        signal = strategy.evaluate(
            start + pd.offsets.BDay(7),
            observations(45, regime="risk4"),
            portfolio,
        )
        self.assertEqual(strategy.defense_mode, "DEFENSE")
        self.assertEqual(signal["target"], {"QQQ": 0.5, "BIL": 0.5})
        self.assertTrue(signal["rebalance"])
        self.assertEqual(signal["days"], 1)

    def test_three_recovery_days_reset_cycle_peak_and_restore_qqq(self):
        strategy = self.strategy()
        portfolio = PortfolioStub()
        start = pd.Timestamp("2025-01-02")
        strategy.evaluate(start, observations(70), portfolio)
        strategy.evaluate(
            start + pd.offsets.BDay(1), observations(45, regime="risk4"), portfolio
        )
        for day in range(2, 8):
            strategy.evaluate(
                start + pd.offsets.BDay(day),
                observations(45, regime="risk4"),
                portfolio,
            )

        portfolio.current_weights = {"QQQ": 0.5, "BIL": 0.5}
        for day in range(8, 10):
            signal = strategy.evaluate(
                start + pd.offsets.BDay(day), observations(48), portfolio
            )
            self.assertEqual(strategy.defense_mode, "DEFENSE")
            self.assertEqual(signal["target"], {"QQQ": 0.5, "BIL": 0.5})

        signal = strategy.evaluate(
            start + pd.offsets.BDay(10), observations(48), portfolio
        )
        self.assertEqual(strategy.defense_mode, "NORMAL")
        self.assertEqual(strategy.valuation_peak, 48)
        self.assertEqual(signal["target"], {"QQQ": 1.0, "BIL": 0.0})
        self.assertTrue(signal["rebalance"])

    def test_new_high_cancels_warning_and_resets_peak(self):
        strategy = self.strategy()
        portfolio = PortfolioStub()
        start = pd.Timestamp("2025-01-02")
        strategy.evaluate(start, observations(70), portfolio)
        strategy.evaluate(
            start + pd.offsets.BDay(1), observations(45, regime="risk4"), portfolio
        )

        signal = strategy.evaluate(
            start + pd.offsets.BDay(2), observations(72), portfolio
        )

        self.assertEqual(strategy.defense_mode, "NORMAL")
        self.assertEqual(strategy.valuation_peak, 72)
        self.assertEqual(signal["target"], {"QQQ": 1.0, "BIL": 0.0})

    def test_confirmed_trend_bear_overrides_valuation_target(self):
        strategy = self.strategy()
        signal = strategy.evaluate(
            pd.Timestamp("2025-01-02"),
            observations(45, regime="structural_bear"),
            PortfolioStub(),
        )

        self.assertEqual(strategy.trend_mode, "BEAR")
        self.assertEqual(signal["target"], {"QQQ": 0.0, "BIL": 1.0})


if __name__ == "__main__":
    unittest.main()

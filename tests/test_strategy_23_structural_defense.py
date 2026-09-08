import sys
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "legacy-python"))

from strategy_dsl import DeclarativeStrategy  # noqa: E402


class PortfolioStub:
    def __init__(self, weights):
        self.current_weights = weights

    def weights(self, prices):
        return {
            ticker: self.current_weights.get(ticker, 0.0)
            for ticker in prices
        }


def observations(*, structural_bear):
    if structural_bear:
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
    return {
        "QQQ": qqq,
        "SPY": {"Close": 105.0, "EMA20": 103.0, "ROC5": 1.0},
        "BIL": {"Close": 100.0},
    }


class Strategy23StructuralDefenseTests(unittest.TestCase):
    def strategy(self):
        return DeclarativeStrategy.from_yaml(
            ROOT / "strategies" / "23_qqq_structural_defense_balanced.yaml"
        )

    def test_structural_warning_halves_qqq_before_confirmed_bear(self):
        strategy = self.strategy()
        portfolio = PortfolioStub({"QQQ": 1.0, "BIL": 0.0})
        start = pd.Timestamp("2025-01-02")
        strategy.evaluate(start, observations(structural_bear=False), portfolio)

        for day in range(1, 10):
            signal = strategy.evaluate(
                start + pd.offsets.BDay(day),
                observations(structural_bear=True),
                portfolio,
            )
            self.assertEqual(strategy.state, "BULL")
            self.assertEqual(signal["target"], {"QQQ": 0.5, "BIL": 0.5})
            self.assertTrue(signal["rebalance"])

    def test_tenth_structural_bear_day_moves_fully_to_bil(self):
        strategy = self.strategy()
        portfolio = PortfolioStub({"QQQ": 1.0, "BIL": 0.0})
        start = pd.Timestamp("2025-01-02")
        strategy.evaluate(start, observations(structural_bear=False), portfolio)
        for day in range(1, 10):
            strategy.evaluate(
                start + pd.offsets.BDay(day),
                observations(structural_bear=True),
                portfolio,
            )

        portfolio.current_weights = {"QQQ": 0.5, "BIL": 0.5}
        signal = strategy.evaluate(
            start + pd.offsets.BDay(10),
            observations(structural_bear=True),
            portfolio,
        )

        self.assertEqual(strategy.state, "BEAR")
        self.assertEqual(signal["target"], {"QQQ": 0.0, "BIL": 1.0})
        self.assertTrue(signal["rebalance"])
        self.assertEqual(signal["days"], 1)

    def test_false_warning_restores_full_qqq(self):
        strategy = self.strategy()
        portfolio = PortfolioStub({"QQQ": 1.0, "BIL": 0.0})
        start = pd.Timestamp("2025-01-02")
        strategy.evaluate(start, observations(structural_bear=False), portfolio)
        strategy.evaluate(
            start + pd.offsets.BDay(1),
            observations(structural_bear=True),
            portfolio,
        )

        portfolio.current_weights = {"QQQ": 0.5, "BIL": 0.5}
        signal = strategy.evaluate(
            start + pd.offsets.BDay(2),
            observations(structural_bear=False),
            portfolio,
        )

        self.assertEqual(strategy.state, "BULL")
        self.assertEqual(signal["target"], {"QQQ": 1.0, "BIL": 0.0})
        self.assertTrue(signal["rebalance"])


if __name__ == "__main__":
    unittest.main()

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
        "TDF2050_PROXY": {"Close": 100.0},
        "BIL": {"Close": 100.0},
    }


class Strategy15PreBearTests(unittest.TestCase):
    def strategy(self):
        return DeclarativeStrategy.from_yaml(
            ROOT / "strategies" / "15_band_7030_tdf_state_bil.yaml"
        )

    def test_warning_moves_to_bil_before_bear_confirmation(self):
        strategy = self.strategy()
        portfolio = PortfolioStub({
            "QQQ": 0.70,
            "TDF2050_PROXY": 0.30,
            "BIL": 0.0,
        })
        start = pd.Timestamp("2025-01-02")
        strategy.evaluate(start, observations(structural_bear=False), portfolio)

        for day in range(1, 10):
            signal = strategy.evaluate(
                start + pd.offsets.BDay(day),
                observations(structural_bear=True),
                portfolio,
            )
            self.assertEqual(strategy.state, "BULL")
            self.assertEqual(signal["target"], {
                "QQQ": 0.0,
                "TDF2050_PROXY": 0.0,
                "BIL": 1.0,
            })
            self.assertEqual(signal["days"], 1)

        portfolio.current_weights = {
            "QQQ": 0.0,
            "TDF2050_PROXY": 0.0,
            "BIL": 1.0,
        }
        signal = strategy.evaluate(
            start + pd.offsets.BDay(10),
            observations(structural_bear=True),
            portfolio,
        )

        self.assertEqual(strategy.state, "BEAR")
        self.assertEqual(signal["target"], {
            "QQQ": 0.0,
            "TDF2050_PROXY": 0.20,
            "BIL": 0.80,
        })
        self.assertTrue(signal["rebalance"])
        self.assertEqual(signal["days"], 1)

    def test_false_warning_restores_original_allocation(self):
        strategy = self.strategy()
        portfolio = PortfolioStub({
            "QQQ": 0.70,
            "TDF2050_PROXY": 0.30,
            "BIL": 0.0,
        })
        start = pd.Timestamp("2025-01-02")
        strategy.evaluate(start, observations(structural_bear=False), portfolio)

        for day in range(1, 5):
            strategy.evaluate(
                start + pd.offsets.BDay(day),
                observations(structural_bear=True),
                portfolio,
            )

        portfolio.current_weights = {
            "QQQ": 0.0,
            "TDF2050_PROXY": 0.0,
            "BIL": 1.0,
        }
        signal = strategy.evaluate(
            start + pd.offsets.BDay(5),
            observations(structural_bear=False),
            portfolio,
        )

        self.assertEqual(strategy.state, "BULL")
        self.assertEqual(signal["target"], {
            "QQQ": 0.70,
            "TDF2050_PROXY": 0.30,
            "BIL": 0.0,
        })
        self.assertTrue(signal["rebalance"])
        self.assertEqual(signal["days"], 1)

    def test_normal_rebalance_uses_seven_point_five_percent_band(self):
        strategy = self.strategy()
        portfolio = PortfolioStub({
            "QQQ": 0.70,
            "TDF2050_PROXY": 0.30,
            "BIL": 0.0,
        })
        start = pd.Timestamp("2025-01-02")
        strategy.evaluate(start, observations(structural_bear=False), portfolio)

        portfolio.current_weights = {
            "QQQ": 0.76,
            "TDF2050_PROXY": 0.24,
            "BIL": 0.0,
        }
        inside_band = strategy.evaluate(
            start + pd.offsets.BDay(1),
            observations(structural_bear=False),
            portfolio,
        )
        self.assertFalse(inside_band["rebalance"])

        portfolio.current_weights = {
            "QQQ": 0.78,
            "TDF2050_PROXY": 0.22,
            "BIL": 0.0,
        }
        outside_band = strategy.evaluate(
            start + pd.offsets.BDay(2),
            observations(structural_bear=False),
            portfolio,
        )
        self.assertTrue(outside_band["rebalance"])
        self.assertEqual(outside_band["days"], 1)


if __name__ == "__main__":
    unittest.main()

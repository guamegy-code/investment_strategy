import sys
from pathlib import Path
import unittest

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "src" / "legacy-python"
if str(LEGACY) not in sys.path:
    sys.path.insert(0, str(LEGACY))

from strategy_dsl import DeclarativeStrategy  # noqa: E402


class EmptyPortfolio:
    def weights(self, prices):
        return {ticker: 0.0 for ticker in prices}


class BuyThreeDipStrategyTests(unittest.TestCase):
    def setUp(self):
        self.original = DeclarativeStrategy.from_yaml(
            ROOT / "strategies" / "19_buy_3dip_buyer.yaml"
        )
        self.strategy20 = DeclarativeStrategy.from_yaml(
            ROOT / "strategies" / "20_buy_3dip_buyer_optimized.yaml"
        )
        self.strategy21 = DeclarativeStrategy.from_yaml(
            ROOT / "strategies" / "21_buy_3dip_buyer_delayed_recovery.yaml"
        )
        self.strategy22 = DeclarativeStrategy.from_yaml(
            ROOT / "strategies" / "22_buy_3dip_composite_valuation.yaml"
        )
        self.portfolio = EmptyPortfolio()

    def evaluate_path(self, strategy, prices):
        date = pd.Timestamp("2024-01-02")
        results = []
        for qqq_close in prices:
            results.append(strategy.evaluate(
            date,
            {"QQQ": {"Close": qqq_close}, "BIL": {"Close": 100.0}},
            self.portfolio,
            ))
            date += pd.Timedelta(days=1)
        return results

    def test_strategy_20_remains_the_original_optimized_baseline(self):
        self.assertEqual(self.strategy20.STRATEGY_VERSION, "1")
        self.assertEqual(
            self.strategy20.parameters,
            {"drop_b": -0.10, "drop_c": -0.18, "drop_d": -0.32},
        )
        results = self.evaluate_path(self.strategy20, (100.0, 90.0, 82.0, 68.0, 82.0, 90.0, 100.0))
        self.assertEqual([result["target"] for result in results], [
            {"QQQ": 0.70, "BIL": 0.30},
            {"QQQ": 0.87, "BIL": 0.13},
            {"QQQ": 0.90, "BIL": 0.10},
            {"QQQ": 1.00, "BIL": 0.00},
            {"QQQ": 0.90, "BIL": 0.10},
            {"QQQ": 0.87, "BIL": 0.13},
            {"QQQ": 0.70, "BIL": 0.30},
        ])

    def test_strategy_21_uses_jointly_optimized_entries_and_recoveries(self):
        self.assertEqual(self.strategy21.STRATEGY_VERSION, "1")
        self.assertEqual(
            self.strategy21.parameters,
            {
                "drop_b": -0.10, "drop_c": -0.20, "drop_d": -0.325,
                "recovery_stage_1": 0.075, "recovery_stage_2": -0.085,
                "recovery_stage_3": -0.175,
            },
        )
        results = self.evaluate_path(self.strategy21, (100.0, 90.0, 80.0, 67.5, 82.5, 91.5, 107.5))
        self.assertEqual([result["target"] for result in results], [
            {"QQQ": 0.70, "BIL": 0.30},
            {"QQQ": 0.87, "BIL": 0.13},
            {"QQQ": 0.90, "BIL": 0.10},
            {"QQQ": 1.00, "BIL": 0.00},
            {"QQQ": 0.90, "BIL": 0.10},
            {"QQQ": 0.87, "BIL": 0.13},
            {"QQQ": 0.70, "BIL": 0.30},
        ])

    def test_original_strategy_remains_unchanged(self):
        self.assertEqual(self.original.STRATEGY_VERSION, "8")
        self.assertEqual(
            self.original.parameters,
            {"drop_b": -0.10, "drop_c": -0.20, "drop_d": -0.30},
        )

    def test_strategy_22_uses_composite_valuation_overlay(self):
        self.assertEqual(self.strategy22.strategy_id, "dsl:buy-3dip-bil-composite-valuation")
        self.assertEqual(self.strategy22.STRATEGY_VERSION, "2")
        self.assertTrue(self.strategy22.definition["strategy"]["enabled"])
        self.assertEqual(self.strategy22.parameters["valuation_mild_entry"], 65)
        self.assertEqual(self.strategy22.parameters["valuation_high_entry"], 75)
        self.assertIn("SPY", self.strategy22.required_tickers)
        self.assertIn("VALUATION_SCORE", self.strategy22.required_market_fields["QQQ"])

        date = pd.Timestamp("2024-01-02")
        targets = []
        for qqq_close in (100.0, 90.0, 80.0):
            result = self.strategy22.evaluate(
                date,
                {
                    "QQQ": {"Close": qqq_close, "VALUATION_SCORE": 80.0},
                    "BIL": {"Close": 100.0},
                    "SPY": {"Close": 100.0},
                },
                self.portfolio,
            )
            targets.append(result["target"])
            date += pd.Timedelta(days=1)
        self.assertEqual(targets, [
            {"QQQ": 0.50, "BIL": 0.50},
            {"QQQ": 0.85, "BIL": 0.15},
            {"QQQ": 0.90, "BIL": 0.10},
        ])


if __name__ == "__main__":
    unittest.main()

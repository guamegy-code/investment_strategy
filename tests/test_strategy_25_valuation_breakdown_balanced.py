import sys
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "legacy-python"))

from main import load_active_strategies  # noqa: E402
from strategy_domain import strategy_identity  # noqa: E402
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
            "Close": 90.0, "EMA20": 95.0, "EMA55": 96.0, "EMA200": 94.0,
            "ROC5": -3.0, "ROC20": 1.0, "ROC60": 2.0,
            "EMA20_SLOPE5": 1.0, "EMA200_SLOPE20": 1.0,
        }
    elif regime == "risk6":
        qqq = {
            "Close": 80.0, "EMA20": 95.0, "EMA55": 96.0, "EMA200": 94.0,
            "ROC5": -5.0, "ROC20": -8.0, "ROC60": 2.0,
            "EMA20_SLOPE5": -2.0, "EMA200_SLOPE20": 1.0,
        }
    elif regime == "structural":
        qqq = {
            "Close": 75.0, "EMA20": 85.0, "EMA55": 90.0, "EMA200": 100.0,
            "ROC5": -5.0, "ROC20": -8.0, "ROC60": -20.0,
            "EMA20_SLOPE5": -2.0, "EMA200_SLOPE20": -1.0,
        }
    else:
        qqq = {
            "Close": 105.0, "EMA20": 103.0, "EMA55": 100.0, "EMA200": 98.0,
            "ROC5": 3.0, "ROC20": 5.0, "ROC60": 8.0,
            "EMA20_SLOPE5": 2.0, "EMA200_SLOPE20": 1.0,
        }
    qqq["VALUATION_SCORE"] = valuation_score
    return {
        "QQQ": qqq,
        "SPY": {"Close": 105.0, "EMA20": 103.0, "ROC5": 1.0},
        "BIL": {"Close": 100.0},
    }


class Strategy25ValuationBreakdownBalancedTests(unittest.TestCase):
    def strategy(self):
        return DeclarativeStrategy.from_yaml(
            ROOT / "strategies" / "25_qqq_valuation_breakdown_balanced.yaml"
        )

    def test_extreme_risk_enters_thirty_percent_defense_immediately(self):
        strategy = self.strategy()
        portfolio = PortfolioStub()
        start = pd.Timestamp("2025-01-02")

        strategy.evaluate(start, observations(70), portfolio)
        signal = strategy.evaluate(
            start + pd.offsets.BDay(1),
            observations(45, regime="risk6"),
            portfolio,
        )

        self.assertEqual(strategy.defense_mode, "DEFENSE")
        self.assertEqual(signal["target"], {"QQQ": 0.3, "BIL": 0.7})
        self.assertTrue(signal["rebalance"])

    def test_structural_bear_uses_false_true_state_without_inventing_target_change(self):
        strategy = self.strategy()
        portfolio = PortfolioStub()
        start = pd.Timestamp("2025-01-02")

        strategy.evaluate(start, observations(70), portfolio)
        strategy.evaluate(
            start + pd.offsets.BDay(1), observations(45, regime="risk6"), portfolio
        )
        portfolio.current_weights = {"QQQ": 0.3, "BIL": 0.7}
        signal = strategy.evaluate(
            start + pd.offsets.BDay(2), observations(45, regime="structural"), portfolio
        )

        self.assertEqual(strategy.structural_bear_mode, "TRUE")
        self.assertEqual(signal["target"], {"QQQ": 0.3, "BIL": 0.7})
        context = strategy.notification_context
        self.assertIn({
            "name": "structural_bear_mode", "previous": "FALSE", "current": "TRUE",
        }, context["state_changes"])
        self.assertFalse(context["target_changed"])
        self.assertFalse(context["rebalance_required"])

    def test_non_extreme_warning_retains_six_day_confirmation(self):
        strategy = self.strategy()
        portfolio = PortfolioStub()
        start = pd.Timestamp("2025-01-02")
        strategy.evaluate(start, observations(70), portfolio)
        strategy.evaluate(
            start + pd.offsets.BDay(1), observations(45, regime="risk4"), portfolio
        )

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
        self.assertEqual(signal["target"], {"QQQ": 0.3, "BIL": 0.7})

    def test_two_recovery_days_restore_normal_and_reset_peak(self):
        strategy = self.strategy()
        portfolio = PortfolioStub()
        start = pd.Timestamp("2025-01-02")
        strategy.evaluate(start, observations(70), portfolio)
        strategy.evaluate(
            start + pd.offsets.BDay(1), observations(45, regime="risk6"), portfolio
        )
        portfolio.current_weights = {"QQQ": 0.3, "BIL": 0.7}

        first = strategy.evaluate(
            start + pd.offsets.BDay(2), observations(48), portfolio
        )
        self.assertEqual(strategy.defense_mode, "DEFENSE")
        self.assertEqual(first["target"], {"QQQ": 0.3, "BIL": 0.7})

        second = strategy.evaluate(
            start + pd.offsets.BDay(3), observations(48), portfolio
        )
        self.assertEqual(strategy.defense_mode, "NORMAL")
        self.assertEqual(strategy.valuation_peak, 48)
        self.assertEqual(second["target"], {"QQQ": 1.0, "BIL": 0.0})

    def test_dash_loader_exposes_strategy_25(self):
        identities = {strategy_identity(strategy) for strategy in load_active_strategies()}
        self.assertIn("dsl:qqq-valuation-breakdown-balanced", identities)


if __name__ == "__main__":
    unittest.main()

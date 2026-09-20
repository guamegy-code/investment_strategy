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
    def __init__(self):
        self.current_weights = {"QQQ": 1.0, "BIL": 0.0}

    def weights(self, prices):
        return {ticker: self.current_weights.get(ticker, 0.0) for ticker in prices}


def observations(close, valuation_score, *, regime="bull"):
    if regime == "risk6":
        qqq = {
            "Close": close, "EMA20": 95.0, "EMA55": 96.0, "EMA200": 94.0,
            "ROC5": -5.0, "ROC20": -8.0, "ROC60": 2.0,
            "EMA20_SLOPE5": -2.0, "EMA200_SLOPE20": 1.0,
        }
    else:
        qqq = {
            "Close": close, "EMA20": close - 2.0, "EMA55": close - 4.0,
            "EMA200": close - 6.0, "ROC5": 2.0, "ROC20": 4.0,
            "ROC60": 6.0, "EMA20_SLOPE5": 1.0, "EMA200_SLOPE20": 1.0,
        }
    qqq["VALUATION_SCORE"] = valuation_score
    return {
        "QQQ": qqq,
        "SPY": {"Close": 105.0, "EMA20": 103.0, "ROC5": 1.0},
        "BIL": {"Close": 100.0},
    }


class Strategy28ValuationWarningDipBuyerTests(unittest.TestCase):
    def strategy(self):
        return DeclarativeStrategy.from_yaml(
            ROOT / "strategies" / "28_qqq_valuation_warning_dip_buyer.yaml"
        )

    def test_warning_builds_thirty_percent_cash_buffer(self):
        strategy = self.strategy()
        portfolio = PortfolioStub()
        start = pd.Timestamp("2025-01-02")

        strategy.evaluate(start, observations(100.0, 70), portfolio)
        signal = strategy.evaluate(
            start + pd.offsets.BDay(1), observations(95.0, 45), portfolio
        )

        self.assertEqual(strategy.defense_mode, "WARNING")
        self.assertEqual(strategy.stage, 0)
        self.assertEqual(signal["target"], {"QQQ": 0.70, "BIL": 0.30})

    def test_first_dip_redeploys_the_entire_warning_buffer(self):
        strategy = self.strategy()
        portfolio = PortfolioStub()
        start = pd.Timestamp("2025-01-02")
        strategy.evaluate(start, observations(100.0, 70), portfolio)
        strategy.evaluate(
            start + pd.offsets.BDay(1), observations(95.0, 45), portfolio
        )

        first = strategy.evaluate(
            start + pd.offsets.BDay(2), observations(90.0, 45), portfolio
        )
        second = strategy.evaluate(
            start + pd.offsets.BDay(3), observations(80.0, 45), portfolio
        )
        third = strategy.evaluate(
            start + pd.offsets.BDay(4), observations(67.5, 45), portfolio
        )

        self.assertEqual(first["target"], {"QQQ": 1.0, "BIL": 0.0})
        self.assertEqual(second["target"], {"QQQ": 1.0, "BIL": 0.0})
        self.assertEqual(third["target"], {"QQQ": 1.0, "BIL": 0.0})

    def test_defense_has_priority_over_dip_stage(self):
        strategy = self.strategy()
        portfolio = PortfolioStub()
        start = pd.Timestamp("2025-01-02")
        strategy.evaluate(start, observations(100.0, 70), portfolio)
        signal = strategy.evaluate(
            start + pd.offsets.BDay(1),
            observations(90.0, 45, regime="risk6"),
            portfolio,
        )

        self.assertEqual(strategy.stage, 1)
        self.assertEqual(strategy.defense_mode, "DEFENSE")
        self.assertEqual(signal["target"], {"QQQ": 0.30, "BIL": 0.70})

    def test_dash_loader_exposes_strategy_28(self):
        identities = {strategy_identity(strategy) for strategy in load_active_strategies()}
        self.assertIn("dsl:qqq-valuation-warning-dip-buyer", identities)


if __name__ == "__main__":
    unittest.main()

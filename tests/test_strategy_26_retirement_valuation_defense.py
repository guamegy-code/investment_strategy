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
        self.current_weights = weights or {
            "QQQ": 0.70, "TDF2050_PROXY": 0.30, "BIL": 0.0,
        }

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
    spy_bearish = regime in {"risk4", "risk6", "structural"}
    return {
        "QQQ": qqq,
        "SPY": {
            "Close": 95.0 if spy_bearish else 105.0,
            "EMA20": 100.0 if spy_bearish else 103.0,
            "ROC5": -2.0 if spy_bearish else 1.0,
        },
        "TDF2050_PROXY": {"Close": 100.0},
        "BIL": {"Close": 100.0},
    }


class Strategy26RetirementValuationDefenseTests(unittest.TestCase):
    def strategy(self):
        return DeclarativeStrategy.from_yaml(
            ROOT / "strategies" / "26_band_7030_tdf_valuation_defense.yaml"
        )

    def test_extreme_valuation_breakdown_enters_thirty_percent_defense(self):
        strategy = self.strategy()
        portfolio = PortfolioStub()
        start = pd.Timestamp("2025-01-02")

        strategy.evaluate(start, observations(70), portfolio)
        signal = strategy.evaluate(
            start + pd.offsets.BDay(1), observations(45, regime="risk6"), portfolio
        )

        self.assertEqual(strategy.defense_mode, "DEFENSE")
        self.assertEqual(signal["target"], {
            "QQQ": 0.30, "TDF2050_PROXY": 0.0, "BIL": 0.70,
        })

    def test_structural_bear_warning_overrides_valuation_defense(self):
        strategy = self.strategy()
        portfolio = PortfolioStub()
        start = pd.Timestamp("2025-01-02")

        strategy.evaluate(start, observations(70), portfolio)
        strategy.evaluate(
            start + pd.offsets.BDay(1), observations(45, regime="risk6"), portfolio
        )
        signal = strategy.evaluate(
            start + pd.offsets.BDay(2),
            observations(45, regime="structural"),
            portfolio,
        )

        self.assertEqual(strategy.defense_mode, "DEFENSE")
        self.assertNotEqual(strategy.trend_mode, "BEAR")
        self.assertEqual(signal["target"], {
            "QQQ": 0.0, "TDF2050_PROXY": 0.0, "BIL": 1.0,
        })
        context = strategy.notification_context
        self.assertIn({
            "name": "structural_bear_mode", "previous": "FALSE", "current": "TRUE",
        }, context["state_changes"])
        self.assertEqual(context["previous_target_weights"], {
            "QQQ": 0.30, "TDF2050_PROXY": 0.0, "BIL": 0.70,
        })
        self.assertEqual(context["target_weights"], {
            "QQQ": 0.0, "TDF2050_PROXY": 0.0, "BIL": 1.0,
        })
        self.assertTrue(context["target_changed"])
        self.assertTrue(context["rebalance_required"])
        self.assertFalse(context["prealerts"][0]["matched"])

    def test_non_extreme_warning_requires_six_days(self):
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
            self.assertEqual(signal["target"]["QQQ"], 0.70)

        signal = strategy.evaluate(
            start + pd.offsets.BDay(7), observations(45, regime="risk4"), portfolio
        )
        self.assertEqual(strategy.defense_mode, "DEFENSE")
        self.assertEqual(signal["target"]["QQQ"], 0.30)

    def test_risk_asset_target_never_exceeds_seventy_percent(self):
        strategy = self.strategy()
        portfolio = PortfolioStub()
        start = pd.Timestamp("2025-01-02")

        for offset, observation in enumerate((
            observations(70),
            observations(45, regime="risk6"),
            observations(45, regime="structural"),
            observations(48),
            observations(48),
        )):
            signal = strategy.evaluate(start + pd.offsets.BDay(offset), observation, portfolio)
            self.assertLessEqual(signal["target"]["QQQ"], 0.70)
            self.assertAlmostEqual(sum(signal["target"].values()), 1.0)

    def test_dash_loader_exposes_strategy_26(self):
        identities = {strategy_identity(strategy) for strategy in load_active_strategies()}
        self.assertIn("dsl:band-7030-tdf-valuation-defense", identities)


if __name__ == "__main__":
    unittest.main()

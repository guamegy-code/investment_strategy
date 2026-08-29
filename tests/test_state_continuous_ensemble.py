import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "src" / "legacy-python"),
)

from validation.state_continuous_ensemble import (  # noqa: E402
    EnsembleProfile,
    StateContinuousEnsembleStrategy,
    continuous_expert_weight,
)


class FakeBaseline:
    holding_tickers = ("QQQ", "TDF2050_PROXY", "BIL")
    observation_tickers = ("SPY",)
    required_tickers = (*holding_tickers, *observation_tickers)
    required_market_fields = {"QQQ": ("Close", "EMA200")}
    risk_asset_tickers = ("QQQ",)


class FakePortfolio:
    def __init__(self, weights):
        self._weights = weights

    def weights(self, _prices):
        return self._weights.copy()


def qqq(
    close=115.0,
    ema200=100.0,
    roc60=15.0,
    roc120=25.0,
    roc252=40.0,
    volatility=0.25,
):
    return {
        "Close": close,
        "EMA200": ema200,
        "ROC60": roc60,
        "ROC120": roc120,
        "ROC252": roc252,
        "VOL60": volatility,
    }


def market(**kwargs):
    return {
        "QQQ": qqq(**kwargs),
        "TDF2050_PROXY": {"Close": 100.0},
        "BIL": {"Close": 100.0},
        "SPY": {"Close": 100.0},
    }


def point(state="BULL", target=None, safe_tdf_share=1.0, rebalance=False):
    return {
        "state": state,
        "target": target or {
            "QQQ": 0.70,
            "TDF2050_PROXY": 0.30,
            "BIL": 0.0,
        },
        "rebalance": rebalance,
        "days": 2,
        "reason": None,
        "risk_off_score": 0,
        "recovery_score": 6,
        "safe_tdf_share": safe_tdf_share,
    }


class StateContinuousEnsembleTests(unittest.TestCase):
    def strategy(self, schedule, share=0.20):
        return StateContinuousEnsembleStrategy(
            EnsembleProfile("TEST", share),
            schedule,
            FakeBaseline(),
            lambda _row: None,
        )

    def test_continuous_expert_keeps_full_weight_in_positive_trend(self):
        self.assertAlmostEqual(continuous_expert_weight(qqq()), 0.70)

    def test_continuous_expert_respects_downside_floor(self):
        stressed = qqq(
            close=80.0,
            roc60=-20.0,
            roc120=-30.0,
            roc252=-50.0,
            volatility=0.50,
        )
        self.assertAlmostEqual(continuous_expert_weight(stressed), 0.20)

    def test_twenty_percent_ensemble_blends_non_bear_target(self):
        date = pd.Timestamp("2024-01-02")
        strategy = self.strategy({date: point()})
        stressed_market = market(
            close=80.0,
            roc60=-20.0,
            roc120=-30.0,
            roc252=-50.0,
            volatility=0.50,
        )

        signal = strategy.evaluate(
            date,
            stressed_market,
            FakePortfolio({"QQQ": 0.70, "TDF2050_PROXY": 0.30, "BIL": 0.0}),
        )

        self.assertAlmostEqual(signal["target"]["QQQ"], 0.60)
        self.assertAlmostEqual(signal["target"]["TDF2050_PROXY"], 0.40)
        self.assertAlmostEqual(sum(signal["target"].values()), 1.0)

    def test_bear_target_is_never_weakened(self):
        date = pd.Timestamp("2024-01-02")
        bear_target = {"QQQ": 0.0, "TDF2050_PROXY": 0.0, "BIL": 1.0}
        strategy = self.strategy({
            date: point(
                state="BEAR",
                target=bear_target,
                safe_tdf_share=0.0,
            )
        })

        signal = strategy.evaluate(
            date,
            market(),
            FakePortfolio(bear_target),
        )

        self.assertEqual(signal["target"], bear_target)
        self.assertEqual(strategy.residual, 0.0)

    def test_zero_ensemble_share_reproduces_shadow_target(self):
        date = pd.Timestamp("2024-01-02")
        baseline_target = {
            "QQQ": 0.73,
            "TDF2050_PROXY": 0.27,
            "BIL": 0.0,
        }
        strategy = self.strategy(
            {date: point(target=baseline_target)},
            share=0.0,
        )

        signal = strategy.evaluate(
            date,
            market(),
            FakePortfolio(baseline_target),
        )

        self.assertEqual(signal["target"], baseline_target)
        self.assertFalse(signal["rebalance"])

    def test_ensemble_rebalance_is_limited_to_one_check_per_week(self):
        first = pd.Timestamp("2024-01-02")
        second = pd.Timestamp("2024-01-03")
        next_week = pd.Timestamp("2024-01-09")
        schedule = {date: point() for date in (first, second, next_week)}
        strategy = self.strategy(schedule)
        portfolio = FakePortfolio(
            {"QQQ": 0.70, "TDF2050_PROXY": 0.30, "BIL": 0.0}
        )
        stressed_market = market(
            close=80.0,
            roc60=-20.0,
            roc120=-30.0,
            roc252=-50.0,
            volatility=0.50,
        )

        first_signal = strategy.evaluate(first, stressed_market, portfolio)
        second_signal = strategy.evaluate(second, stressed_market, portfolio)
        next_week_signal = strategy.evaluate(next_week, stressed_market, portfolio)

        self.assertTrue(first_signal["rebalance"])
        self.assertFalse(second_signal["rebalance"])
        self.assertTrue(next_week_signal["rebalance"])


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "legacy-python"))

from validation.strategy15_shock_buffer_overlay import (  # noqa: E402
    ShockBufferProfile,
    Strategy15ShockBufferOverlay,
    fast_shock_signal,
)


class FakeBaseline:
    holding_tickers = ("QQQ", "TDF2050_PROXY", "BIL")
    observation_tickers = ("SPY",)
    required_tickers = (*holding_tickers, *observation_tickers)
    required_market_fields = {}
    risk_asset_tickers = ("QQQ",)


class FakePortfolio:
    def __init__(self, weights=None):
        self._weights = weights or {
            "QQQ": 0.70,
            "TDF2050_PROXY": 0.30,
            "BIL": 0.0,
        }

    def weights(self, _prices):
        return self._weights.copy()


def market(
    qqq_close=90.0,
    qqq_ema20=95.0,
    qqq_ema55=100.0,
    qqq_roc5=-5.0,
    qqq_roc20=-5.0,
    qqq_drawdown20=-0.06,
    spy_close=95.0,
    spy_ema20=100.0,
    spy_roc5=-2.0,
):
    return {
        "QQQ": {
            "Close": qqq_close,
            "EMA20": qqq_ema20,
            "EMA55": qqq_ema55,
            "ROC5": qqq_roc5,
            "ROC20": qqq_roc20,
            "DRAWDOWN20": qqq_drawdown20,
        },
        "TDF2050_PROXY": {"Close": 100.0},
        "BIL": {"Close": 100.0},
        "SPY": {"Close": spy_close, "EMA20": spy_ema20, "ROC5": spy_roc5},
    }


def point(state="CAUTION", risk_off_score=5, target=None, rebalance=False):
    return {
        "state": state,
        "target": target or {
            "QQQ": 0.70,
            "TDF2050_PROXY": 0.30,
            "BIL": 0.0,
        },
        "rebalance": rebalance,
        "days": 1,
        "reason": None,
        "risk_off_score": risk_off_score,
        "recovery_score": 1,
        "safe_tdf_share": 1.0,
    }


class Strategy15ShockBufferTests(unittest.TestCase):
    def strategy(self, schedule, profile=None, record=lambda _row: None):
        return Strategy15ShockBufferOverlay(
            profile or ShockBufferProfile("TEST", 0.025),
            schedule,
            FakeBaseline(),
            record,
        )

    def test_fast_shock_is_available_while_production_is_still_bull(self):
        date = pd.Timestamp("2024-01-02")
        strategy = self.strategy({date: point(state="BULL", risk_off_score=0)})

        signal = strategy.evaluate(date, market(), FakePortfolio())

        self.assertTrue(fast_shock_signal(market()))
        self.assertTrue(signal["rebalance"])
        self.assertEqual(signal["reason"], "STRATEGY15_SHOCK_BUFFER_ENTER_FAST_SHOCK")

    def test_entry_sells_actual_qqq_weight_not_the_nominal_target(self):
        date = pd.Timestamp("2024-01-02")
        strategy = self.strategy({date: point()})
        portfolio = FakePortfolio({
            "QQQ": 0.68,
            "TDF2050_PROXY": 0.30,
            "BIL": 0.02,
        })

        signal = strategy.evaluate(date, market(), portfolio)

        self.assertAlmostEqual(signal["target"]["QQQ"], 0.655)
        self.assertAlmostEqual(signal["target"]["TDF2050_PROXY"], 0.30)
        self.assertAlmostEqual(signal["target"]["BIL"], 0.045)

    def test_buffer_exits_after_exactly_five_evaluated_trading_days(self):
        dates = pd.date_range("2024-01-02", periods=6, freq="B")
        schedule = {
            dates[0]: point(),
            **{
                date: point(state="CAUTION", risk_off_score=0)
                for date in dates[1:]
            },
        }
        strategy = self.strategy(schedule)
        portfolio = FakePortfolio()

        signals = [
            strategy.evaluate(date, market(), portfolio)
            for date in dates
        ]

        self.assertTrue(all(not signal["rebalance"] for signal in signals[1:5]))
        self.assertTrue(signals[5]["rebalance"])
        self.assertEqual(signals[5]["reason"], "STRATEGY15_SHOCK_BUFFER_EXIT_TTL")
        self.assertAlmostEqual(signals[5]["target"]["QQQ"], 0.70)

    def test_caution_episode_cannot_rearm_after_the_ttl_exit(self):
        dates = pd.date_range("2024-01-02", periods=7, freq="B")
        schedule = {
            date: point(state="CAUTION", risk_off_score=0)
            for date in dates
        }
        schedule[dates[0]] = point()
        strategy = self.strategy(schedule)
        portfolio = FakePortfolio()

        for date in dates[:5]:
            strategy.evaluate(date, market(), portfolio)
        strategy.evaluate(
            dates[5],
            market(
                qqq_close=105.0,
                qqq_ema20=100.0,
                qqq_roc5=2.0,
                qqq_drawdown20=-0.02,
                spy_close=105.0,
                spy_ema20=100.0,
                spy_roc5=2.0,
            ),
            portfolio,
        )
        later_signal = strategy.evaluate(dates[6], market(), portfolio)

        self.assertFalse(strategy.armed)
        self.assertFalse(later_signal["rebalance"])

    def test_structural_defense_target_has_priority_over_buffer(self):
        date = pd.Timestamp("2024-01-02")
        strategy = self.strategy({date: point(target={
            "QQQ": 0.0,
            "TDF2050_PROXY": 0.0,
            "BIL": 1.0,
        }, rebalance=True)})

        signal = strategy.evaluate(date, market(), FakePortfolio())

        self.assertEqual(signal["target"], {
            "QQQ": 0.0,
            "TDF2050_PROXY": 0.0,
            "BIL": 1.0,
        })
        self.assertNotEqual(
            signal["reason"],
            "STRATEGY15_SHOCK_BUFFER_ENTER_STRICT",
        )


if __name__ == "__main__":
    unittest.main()

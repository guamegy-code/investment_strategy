import sys
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "legacy-python"))

from validation.strategy15_bounded_shock_buffer import (  # noqa: E402
    BoundedBufferProfile,
    Strategy15BoundedShockBuffer,
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


class BoundedShockBufferTests(unittest.TestCase):
    def strategy(self, schedule, profile, drawdowns):
        return Strategy15BoundedShockBuffer(
            profile,
            schedule,
            FakeBaseline(),
            drawdowns,
            lambda _row: None,
        )

    def test_confirmation_waits_for_another_lower_close(self):
        first = pd.Timestamp("2024-01-02")
        second = pd.Timestamp("2024-01-03")
        profile = BoundedBufferProfile("CONFIRM", confirmation_days=1)
        strategy = self.strategy(
            {first: point(state="BULL", risk_off_score=0), second: point(state="BULL", risk_off_score=0)},
            profile,
            {first: -0.05, second: -0.06},
        )

        pending = strategy.evaluate(first, market(), FakePortfolio())
        entered = strategy.evaluate(
            second, market(qqq_close=89.0), FakePortfolio()
        )

        self.assertFalse(pending["rebalance"])
        self.assertTrue(entered["rebalance"])
        self.assertEqual(
            entered["reason"],
            "STRATEGY15_BOUNDED_BUFFER_ENTER_CONFIRMED",
        )

    def test_unwind_reverses_only_available_buffer_and_caps_qqq_at_70(self):
        dates = pd.date_range("2024-01-02", periods=6, freq="B")
        profile = BoundedBufferProfile("IMMEDIATE")
        strategy = self.strategy(
            {date: point() for date in dates},
            profile,
            {date: -0.05 for date in dates},
        )
        entry_portfolio = FakePortfolio({
            "QQQ": 0.68,
            "TDF2050_PROXY": 0.30,
            "BIL": 0.02,
        })

        strategy.evaluate(dates[0], market(), entry_portfolio)
        for date in dates[1:5]:
            strategy.evaluate(date, market(), entry_portfolio)
        exit_signal = strategy.evaluate(
            dates[5],
            market(),
            FakePortfolio({"QQQ": 0.69, "TDF2050_PROXY": 0.30, "BIL": 0.01}),
        )

        self.assertTrue(exit_signal["rebalance"])
        self.assertEqual(exit_signal["reason"], "STRATEGY15_BOUNDED_BUFFER_EXIT_TTL")
        self.assertAlmostEqual(exit_signal["target"]["QQQ"], 0.70)
        self.assertAlmostEqual(exit_signal["target"]["BIL"], 0.0)
        self.assertAlmostEqual(exit_signal["target"]["TDF2050_PROXY"], 0.30)

    def test_unwind_does_not_buy_qqq_when_it_is_already_above_cap(self):
        dates = pd.date_range("2024-01-02", periods=6, freq="B")
        profile = BoundedBufferProfile("IMMEDIATE")
        strategy = self.strategy(
            {date: point() for date in dates},
            profile,
            {date: -0.05 for date in dates},
        )
        portfolio = FakePortfolio()
        strategy.evaluate(dates[0], market(), portfolio)
        for date in dates[1:5]:
            strategy.evaluate(date, market(), portfolio)
        exit_signal = strategy.evaluate(
            dates[5],
            market(),
            FakePortfolio({"QQQ": 0.71, "TDF2050_PROXY": 0.265, "BIL": 0.025}),
        )

        self.assertFalse(exit_signal["rebalance"])
        self.assertAlmostEqual(exit_signal["target"]["QQQ"], 0.71)
        self.assertAlmostEqual(exit_signal["target"]["BIL"], 0.025)

    def test_portfolio_drawdown_gate_blocks_shallow_portfolio_loss(self):
        date = pd.Timestamp("2024-01-02")
        profile = BoundedBufferProfile(
            "DD_GATE", require_portfolio_drawdown=True
        )
        shallow = self.strategy({date: point()}, profile, {date: -0.03})
        deep = self.strategy({date: point()}, profile, {date: -0.04})

        blocked = shallow.evaluate(date, market(), FakePortfolio())
        entered = deep.evaluate(date, market(), FakePortfolio())

        self.assertFalse(blocked["rebalance"])
        self.assertTrue(entered["rebalance"])
        self.assertEqual(
            entered["reason"], "STRATEGY15_BOUNDED_BUFFER_ENTER_DD_GATE",
        )


if __name__ == "__main__":
    unittest.main()

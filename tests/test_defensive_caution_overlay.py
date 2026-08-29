import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "src" / "legacy-python"),
)

from validation.defensive_caution_overlay import (  # noqa: E402
    DefensiveCautionOverlayStrategy,
    DefensiveCautionProfile,
    START_DATE,
    _rolling_windows,
    defensive_caution_signal,
)


class FakeBaseline:
    holding_tickers = ("QQQ", "TDF2050_PROXY", "BIL")
    observation_tickers = ("SPY",)
    required_tickers = (*holding_tickers, *observation_tickers)
    required_market_fields = {
        "QQQ": ("Close", "EMA55", "ROC20"),
        "SPY": ("Close", "EMA20", "ROC5"),
    }
    risk_asset_tickers = ("QQQ",)


class FakePortfolio:
    def weights(self, _prices):
        return {"QQQ": 0.70, "TDF2050_PROXY": 0.0, "BIL": 0.30}


def market(
    qqq_close=90.0,
    qqq_ema55=100.0,
    qqq_roc20=-5.0,
    qqq_ema20=95.0,
    qqq_roc60=-10.0,
    spy_close=95.0,
    spy_ema20=100.0,
    spy_roc5=-2.0,
):
    return {
        "QQQ": {
            "Close": qqq_close,
            "EMA55": qqq_ema55,
            "ROC20": qqq_roc20,
            "EMA20": qqq_ema20,
            "ROC60": qqq_roc60,
        },
        "TDF2050_PROXY": {"Close": 100.0},
        "BIL": {"Close": 100.0},
        "SPY": {
            "Close": spy_close,
            "EMA20": spy_ema20,
            "ROC5": spy_roc5,
        },
    }


def point(
    state="CAUTION",
    risk_off_score=5,
    target=None,
    safe_tdf_share=0.0,
    rebalance=False,
    days=2,
):
    return {
        "state": state,
        "target": target or {
            "QQQ": 0.70,
            "TDF2050_PROXY": 0.0,
            "BIL": 0.30,
        },
        "rebalance": rebalance,
        "days": days,
        "reason": None,
        "risk_off_score": risk_off_score,
        "recovery_score": 1,
        "safe_tdf_share": safe_tdf_share,
    }


class DefensiveCautionOverlayTests(unittest.TestCase):
    def strategy(
        self,
        schedule,
        record=lambda _row: None,
        profile=None,
    ):
        return DefensiveCautionOverlayStrategy(
            profile or DefensiveCautionProfile("TEST", 0.05),
            schedule,
            FakeBaseline(),
            record,
        )

    def test_strict_signal_requires_every_pre_registered_condition(self):
        self.assertTrue(defensive_caution_signal(point(), market()))
        self.assertFalse(
            defensive_caution_signal(point(risk_off_score=4), market())
        )
        self.assertFalse(
            defensive_caution_signal(point(), market(qqq_close=101.0))
        )
        self.assertFalse(
            defensive_caution_signal(point(), market(qqq_roc20=1.0))
        )
        self.assertFalse(
            defensive_caution_signal(point(), market(spy_close=101.0))
        )
        self.assertFalse(
            defensive_caution_signal(point(), market(spy_roc5=-0.5))
        )

    def test_intermediate_signal_adds_trend_and_momentum_requirements(self):
        self.assertTrue(
            defensive_caution_signal(
                point(), market(), require_intermediate_trend=True
            )
        )
        self.assertFalse(
            defensive_caution_signal(
                point(),
                market(qqq_ema20=101.0),
                require_intermediate_trend=True,
            )
        )
        self.assertFalse(
            defensive_caution_signal(
                point(),
                market(qqq_roc60=1.0),
                require_intermediate_trend=True,
            )
        )

    def test_activation_reduces_qqq_by_five_points_in_one_day(self):
        date = pd.Timestamp("2024-01-02")
        rows = []
        strategy = self.strategy({date: point()}, rows.append)

        signal = strategy.evaluate(date, market(), FakePortfolio())

        self.assertTrue(signal["rebalance"])
        self.assertEqual(signal["days"], 1)
        self.assertEqual(signal["reason"], "DEFENSIVE_CAUTION_ENTER")
        self.assertAlmostEqual(signal["target"]["QQQ"], 0.65)
        self.assertAlmostEqual(signal["target"]["BIL"], 0.35)
        self.assertTrue(rows[0]["Activated"])

    def test_profit_band_target_is_reduced_instead_of_reset(self):
        date = pd.Timestamp("2024-01-02")
        schedule = {date: point(target={
            "QQQ": 0.74,
            "TDF2050_PROXY": 0.26,
            "BIL": 0.0,
        }, safe_tdf_share=1.0)}
        strategy = self.strategy(schedule)

        signal = strategy.evaluate(date, market(), FakePortfolio())

        self.assertAlmostEqual(signal["target"]["QQQ"], 0.69)
        self.assertAlmostEqual(signal["target"]["TDF2050_PROXY"], 0.31)
        self.assertAlmostEqual(sum(signal["target"].values()), 1.0)

    def test_defensive_tier_stays_active_until_production_leaves_caution(self):
        first = pd.Timestamp("2024-01-02")
        second = pd.Timestamp("2024-01-03")
        third = pd.Timestamp("2024-01-04")
        schedule = {
            first: point(),
            second: point(risk_off_score=2),
            third: point(state="BULL", risk_off_score=0, days=5),
        }
        rows = []
        strategy = self.strategy(schedule, rows.append)

        strategy.evaluate(first, market(), FakePortfolio())
        held = strategy.evaluate(second, market(spy_roc5=1.0), FakePortfolio())
        exited = strategy.evaluate(third, market(spy_roc5=1.0), FakePortfolio())

        self.assertAlmostEqual(held["target"]["QQQ"], 0.65)
        self.assertFalse(rows[1]["StrictSignal"])
        self.assertTrue(rows[1]["DefensiveActive"])
        self.assertTrue(exited["rebalance"])
        self.assertEqual(exited["days"], 5)
        self.assertEqual(exited["reason"], "DEFENSIVE_CAUTION_EXIT")
        self.assertAlmostEqual(exited["target"]["QQQ"], 0.70)

    def test_fast_exit_executes_overlay_removal_in_one_day(self):
        first = pd.Timestamp("2024-01-02")
        second = pd.Timestamp("2024-01-03")
        profile = DefensiveCautionProfile(
            "TEST_FAST_EXIT",
            0.05,
            exit_execution_days=1,
        )
        strategy = self.strategy(
            {
                first: point(),
                second: point(state="BULL", risk_off_score=0, days=5),
            },
            profile=profile,
        )

        strategy.evaluate(first, market(), FakePortfolio())
        signal = strategy.evaluate(
            second, market(spy_roc5=1.0), FakePortfolio()
        )

        self.assertTrue(signal["rebalance"])
        self.assertEqual(signal["days"], 1)
        self.assertEqual(signal["reason"], "DEFENSIVE_CAUTION_EXIT")

    def test_watch_caution_does_not_change_target(self):
        date = pd.Timestamp("2024-01-02")
        baseline_target = {
            "QQQ": 0.70,
            "TDF2050_PROXY": 0.30,
            "BIL": 0.0,
        }
        schedule = {date: point(
            risk_off_score=4,
            target=baseline_target,
            safe_tdf_share=1.0,
        )}
        strategy = self.strategy(schedule)

        signal = strategy.evaluate(date, market(), FakePortfolio())

        self.assertFalse(signal["rebalance"])
        self.assertEqual(signal["target"], baseline_target)

    def test_matched_period_uses_complete_calendar_rolling_windows(self):
        history = pd.DataFrame(index=pd.bdate_range(
            START_DATE,
            "2026-07-31",
        ))

        rolling_five = _rolling_windows(history, years=5)
        rolling_three = _rolling_windows(history, years=3)

        self.assertEqual(START_DATE, "2012-01-03")
        self.assertEqual(len(rolling_five), 10)
        self.assertNotIn("2022_2026", rolling_five)
        self.assertEqual(len(rolling_three), 12)
        self.assertNotIn("2024_2026", rolling_three)


if __name__ == "__main__":
    unittest.main()

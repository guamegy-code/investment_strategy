import sys
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "legacy-python"))

from validation.defensive_caution_overlay import (  # noqa: E402
    DefensiveCautionProfile,
)
from validation.strategy15_defensive_caution_overlay import (  # noqa: E402
    Strategy15DefensiveCautionOverlay,
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


def market(strong=True):
    if strong:
        qqq = {
            "Close": 90.0,
            "EMA20": 95.0,
            "EMA55": 100.0,
            "ROC20": -5.0,
            "ROC60": -10.0,
        }
        spy = {"Close": 95.0, "EMA20": 100.0, "ROC5": -2.0}
    else:
        qqq = {
            "Close": 105.0,
            "EMA20": 103.0,
            "EMA55": 100.0,
            "ROC20": 5.0,
            "ROC60": 10.0,
        }
        spy = {"Close": 105.0, "EMA20": 100.0, "ROC5": 2.0}
    return {
        "QQQ": qqq,
        "TDF2050_PROXY": {"Close": 100.0},
        "BIL": {"Close": 100.0},
        "SPY": spy,
    }


def point(state="CAUTION", target=None, risk_off_score=5, rebalance=False):
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


class Strategy15DefensiveCautionTests(unittest.TestCase):
    def strategy(self, schedule, record=lambda _row: None):
        return Strategy15DefensiveCautionOverlay(
            DefensiveCautionProfile("TEST", 0.05),
            schedule,
            FakeBaseline(),
            record,
        )

    def test_entry_moves_five_points_from_qqq_to_bil(self):
        date = pd.Timestamp("2024-01-02")
        strategy = self.strategy({date: point()})

        signal = strategy.evaluate(date, market(), FakePortfolio())

        self.assertTrue(signal["rebalance"])
        self.assertEqual(signal["days"], 1)
        self.assertEqual(signal["reason"], "STRATEGY15_DEFENSIVE_CAUTION_ENTER")
        self.assertEqual(signal["target"], {
            "QQQ": 0.65,
            "TDF2050_PROXY": 0.30,
            "BIL": 0.05,
        })

    def test_entry_is_forced_inside_the_normal_seven_point_five_band(self):
        date = pd.Timestamp("2024-01-02")
        rows = []
        strategy = self.strategy({date: point()}, rows.append)

        signal = strategy.evaluate(date, market(), FakePortfolio())

        self.assertLess(rows[0]["TargetDeviation"], 0.075)
        self.assertTrue(rows[0]["OverlayTransition"])
        self.assertTrue(signal["rebalance"])

    def test_structural_bear_target_keeps_priority(self):
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

    def test_sticky_tier_survives_signal_release_until_caution_ends(self):
        first = pd.Timestamp("2024-01-02")
        second = pd.Timestamp("2024-01-03")
        third = pd.Timestamp("2024-01-04")
        strategy = self.strategy({
            first: point(),
            second: point(risk_off_score=2),
            third: point(state="BULL", risk_off_score=0),
        })

        strategy.evaluate(first, market(), FakePortfolio())
        held = strategy.evaluate(second, market(False), FakePortfolio())
        exited = strategy.evaluate(third, market(False), FakePortfolio())

        self.assertAlmostEqual(held["target"]["QQQ"], 0.65)
        self.assertTrue(exited["rebalance"])
        self.assertEqual(exited["reason"], "STRATEGY15_DEFENSIVE_CAUTION_EXIT")
        self.assertAlmostEqual(exited["target"]["QQQ"], 0.70)

    def test_candidate_uses_its_own_seven_point_five_band_after_entry(self):
        date = pd.Timestamp("2024-01-02")
        strategy = self.strategy({date: point(risk_off_score=2)})

        signal = strategy.evaluate(
            date,
            market(False),
            FakePortfolio({
                "QQQ": 0.79,
                "TDF2050_PROXY": 0.21,
                "BIL": 0.0,
            }),
        )

        self.assertTrue(signal["rebalance"])
        self.assertEqual(signal["reason"], "STRATEGY15_DEFENSIVE_CAUTION_BAND")

if __name__ == "__main__":
    unittest.main()

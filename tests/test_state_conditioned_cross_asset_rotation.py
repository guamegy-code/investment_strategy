import sys
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "legacy-python"))

from validation.state_conditioned_cross_asset_rotation import (  # noqa: E402
    ALL_HOLDINGS,
    BIL,
    QQQ,
    TDF,
    StateConditionedCrossAssetRotation,
)


class PortfolioStub:
    def __init__(self, weights=None):
        self._weights = weights or {}

    def weights(self, prices):
        return {ticker: self._weights.get(ticker, 0.0) for ticker in prices}


def asset(*, positive=True, score=0.0, volatility=0.10):
    roc60 = 3.0 + score if positive else -1.0
    return {
        "Close": 110.0 if positive else 90.0,
        "EMA200": 100.0,
        "ROC60": roc60,
        "ROC120": 5.0 + score,
        "ROC252": 8.0 + score,
        "VOL60": volatility,
    }


def market(*, eligible=True):
    values = {
        ticker: asset(positive=False)
        for ticker in ALL_HOLDINGS
    }
    values[BIL] = {
        "Close": 100.0,
        "EMA200": 99.0,
        "ROC60": 1.0,
        "ROC120": 2.0,
        "ROC252": 4.0,
        "VOL60": 0.01,
    }
    if eligible:
        values["SHY"] = asset(score=5.0, volatility=0.04)
        values["GLD"] = asset(score=3.0, volatility=0.12)
    return values


def schedule_for(date, *, state="BEAR", bil_weight=0.80):
    return {
        pd.Timestamp(date): {
            "state": state,
            "target": {QQQ: 0.0, TDF: 1.0 - bil_weight, BIL: bil_weight},
            "rebalance": True,
            "days": 1,
            "reason": "STATE_CHANGE",
            "risk_off_score": 6,
            "recovery_score": 0,
        }
    }


class StateConditionedCrossAssetRotationTests(unittest.TestCase):
    def test_bear_keeps_strategy15_tdf_weight_while_rotating_bil_sleeve(self):
        date = pd.Timestamp("2025-02-03")
        records = []
        strategy = StateConditionedCrossAssetRotation(
            schedule_for(date), ALL_HOLDINGS, records.append
        )

        signal = strategy.evaluate(date, market(), PortfolioStub())

        self.assertEqual(signal["target"][QQQ], 0.0)
        self.assertEqual(signal["target"][TDF], 0.20)
        self.assertAlmostEqual(sum(signal["target"].values()), 1.0)
        self.assertGreater(signal["target"]["SHY"], 0.0)
        self.assertGreater(signal["target"]["GLD"], 0.0)
        self.assertLessEqual(signal["target"]["GLD"], 0.80 * 0.30)
        self.assertTrue(records)

    def test_ineligible_assets_leave_the_entire_sleeve_in_bil(self):
        date = pd.Timestamp("2025-02-03")
        strategy = StateConditionedCrossAssetRotation(
            schedule_for(date), ALL_HOLDINGS, lambda _: None
        )

        signal = strategy.evaluate(date, market(eligible=False), PortfolioStub())

        self.assertEqual(signal["target"][TDF], 0.20)
        self.assertEqual(signal["target"][BIL], 0.80)
        self.assertEqual(signal["target"]["SHY"], 0.0)
        self.assertEqual(signal["target"]["GLD"], 0.0)

    def test_no_bil_sleeve_means_exact_strategy15_target(self):
        date = pd.Timestamp("2025-02-03")
        schedule = schedule_for(date, state="BULL", bil_weight=0.0)
        schedule[date]["target"] = {QQQ: 0.70, TDF: 0.30, BIL: 0.0}
        strategy = StateConditionedCrossAssetRotation(
            schedule, ALL_HOLDINGS, lambda _: None
        )

        signal = strategy.evaluate(date, market(), PortfolioStub())

        self.assertEqual(signal["target"][QQQ], 0.70)
        self.assertEqual(signal["target"][TDF], 0.30)
        self.assertEqual(signal["target"][BIL], 0.0)
        self.assertEqual(signal["target"]["SHY"], 0.0)

    def test_pre_bear_and_recovery_keep_their_original_tdf_targets(self):
        cases = (
            ("CAUTION", 1.0, 0.0, 0.0),
            ("RECOVERY", 0.1, 0.6, 0.3),
        )
        for number, (state, bil_weight, qqq_weight, tdf_weight) in enumerate(cases):
            with self.subTest(state=state):
                date = pd.Timestamp("2025-02-03") + pd.offsets.BDay(number)
                schedule = schedule_for(date, state=state, bil_weight=bil_weight)
                schedule[date]["target"] = {
                    QQQ: qqq_weight,
                    TDF: tdf_weight,
                    BIL: bil_weight,
                }
                strategy = StateConditionedCrossAssetRotation(
                    schedule, ALL_HOLDINGS, lambda _: None
                )

                signal = strategy.evaluate(date, market(), PortfolioStub())

                self.assertEqual(signal["target"][QQQ], qqq_weight)
                self.assertEqual(signal["target"][TDF], tdf_weight)
                self.assertAlmostEqual(sum(signal["target"].values()), 1.0)


if __name__ == "__main__":
    unittest.main()

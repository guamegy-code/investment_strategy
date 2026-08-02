import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from validation.momentum_ranking import (  # noqa: E402
    MomentumRankingStrategy,
    PROFILES,
)


def asset(momentum, close=100.0, ema200=90.0):
    return {
        "Close": close,
        "EMA200": ema200,
        "ROC60": momentum,
        "ROC120": momentum,
        "ROC252": momentum,
        "VOL60": 0.10,
    }


def market(qqq, bnd=3.0, bil=2.0, gld=5.0, qqq_above_ema=True):
    return {
        "QQQ": asset(qqq, close=100.0, ema200=90.0 if qqq_above_ema else 110.0),
        "BND": asset(bnd),
        "BIL": asset(bil),
        "GLD": asset(gld),
    }


class MomentumRankingValidationTests(unittest.TestCase):
    @staticmethod
    def strategy(name):
        profile = next(profile for profile in PROFILES if profile.name == name)
        return MomentumRankingStrategy(profile)

    def test_absolute_momentum_allocates_70_percent_to_strong_qqq(self):
        strategy = self.strategy("ABSOLUTE_MOMENTUM_COMPOSITE")

        target = strategy._desired_target(market(20.0))

        self.assertEqual(target["QQQ"], 0.70)
        self.assertAlmostEqual(sum(target.values()), 1.0)
        self.assertLessEqual(target["GLD"], 0.20)

    def test_absolute_momentum_exits_qqq_below_cash_or_ema(self):
        strategy = self.strategy("ABSOLUTE_MOMENTUM_COMPOSITE")

        weak = strategy._desired_target(market(1.0))["QQQ"]
        below_ema = strategy._desired_target(
            market(20.0, qqq_above_ema=False)
        )["QQQ"]

        self.assertEqual(weak, 0.0)
        self.assertEqual(below_ema, 0.0)

    def test_absolute_core_profile_keeps_minimum_qqq_weight(self):
        strategy = self.strategy("ABSOLUTE_COMPOSITE_CORE_20")

        target = strategy._desired_target(market(1.0))

        self.assertEqual(target["QQQ"], 0.20)
        self.assertAlmostEqual(sum(target.values()), 1.0)

    def test_rank_weighted_target_respects_caps(self):
        strategy = self.strategy("RANK_WEIGHTED_MOMENTUM")

        target = strategy._desired_target(
            market(50.0, bnd=-10.0, bil=0.0, gld=40.0)
        )

        self.assertAlmostEqual(sum(target.values()), 1.0)
        self.assertLessEqual(target["QQQ"], 0.70)
        self.assertLessEqual(target["GLD"], 0.20)

    def test_rank_weighted_target_favors_stronger_asset(self):
        strategy = self.strategy("RANK_WEIGHTED_MOMENTUM")

        target = strategy._desired_target(
            market(-10.0, bnd=3.0, bil=8.0, gld=1.0)
        )

        self.assertGreater(target["BIL"], target["BND"])
        self.assertGreater(target["BIL"], target["QQQ"])


if __name__ == "__main__":
    unittest.main()

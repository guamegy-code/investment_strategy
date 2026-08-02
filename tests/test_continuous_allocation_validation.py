import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from continuous_allocation_validation import (  # noqa: E402
    ContinuousRiskAllocationStrategy,
    PROFILES,
)


def asset(roc60, roc120, volatility, **extra):
    values = {
        "Close": 100.0,
        "ROC60": roc60,
        "ROC120": roc120,
        "VOL60": volatility,
    }
    values.update(extra)
    return values


def market(qqq_momentum=20.0, qqq_volatility=0.20):
    return {
        "QQQ": asset(
            qqq_momentum,
            qqq_momentum,
            qqq_volatility,
            EMA200=90.0,
            ROC252=qqq_momentum,
        ),
        "BND": asset(2.0, 3.0, 0.06),
        "BIL": asset(1.0, 2.0, 0.01),
        "GLD": asset(8.0, 10.0, 0.15),
    }


class ContinuousAllocationValidationTests(unittest.TestCase):
    @staticmethod
    def strategy(name):
        profile = next(profile for profile in PROFILES if profile.name == name)
        return ContinuousRiskAllocationStrategy(profile)

    def test_targets_sum_to_one_and_respect_caps(self):
        strategy = self.strategy("CORE_20_TACTICAL_50")

        target = strategy._desired_target(market())

        self.assertAlmostEqual(sum(target.values()), 1.0)
        self.assertGreaterEqual(target["QQQ"], 0.20)
        self.assertLessEqual(target["QQQ"], 0.70)
        self.assertLessEqual(target["GLD"], 0.20)

    def test_stronger_trend_increases_qqq_weight(self):
        strategy = self.strategy("CORE_20_TACTICAL_50")

        weak = strategy._desired_target(market(qqq_momentum=-20.0))["QQQ"]
        strong = strategy._desired_target(market(qqq_momentum=20.0))["QQQ"]

        self.assertGreater(strong, weak)

    def test_high_volatility_reduces_only_tactical_exposure(self):
        strategy = self.strategy("CORE_20_TACTICAL_50")

        normal = strategy._desired_target(market(qqq_volatility=0.20))["QQQ"]
        high = strategy._desired_target(market(qqq_volatility=0.50))["QQQ"]

        self.assertGreater(normal, high)
        self.assertGreaterEqual(high, 0.20)

    def test_downside_overlay_keeps_full_weight_until_trend_is_negative(self):
        strategy = self.strategy("DOWNSIDE_OVERLAY_20_70")

        positive = strategy._desired_target(
            market(qqq_momentum=20.0)
        )["QQQ"]
        negative = strategy._desired_target(
            market(qqq_momentum=-20.0)
        )["QQQ"]

        self.assertEqual(positive, 0.70)
        self.assertLess(negative, positive)
        self.assertGreaterEqual(negative, 0.20)


if __name__ == "__main__":
    unittest.main()

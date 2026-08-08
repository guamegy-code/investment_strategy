import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from validation.tail_risk_overlay import (  # noqa: E402
    PROFILES,
    TailOverlayProfile,
    TailRiskOverlayStrategy,
)


class TailRiskOverlayTests(unittest.TestCase):
    def test_probability_below_deadband_keeps_seventy_percent(self):
        strategy = TailRiskOverlayStrategy(PROFILES[1])

        weight = strategy._risk_weight(0.17, 0.15)

        self.assertEqual(weight, 0.70)

    def test_higher_tail_probability_reduces_risk_monotonically(self):
        strategy = TailRiskOverlayStrategy(PROFILES[1])

        low = strategy._risk_weight(0.15, 0.15)
        medium = strategy._risk_weight(0.25, 0.15)
        high = strategy._risk_weight(0.40, 0.15)

        self.assertGreater(low, medium)
        self.assertGreater(medium, high)

    def test_each_profile_respects_its_floor_and_seventy_percent_cap(self):
        for profile in PROFILES:
            strategy = TailRiskOverlayStrategy(profile)

            self.assertEqual(strategy._risk_weight(0.10, 0.15), 0.70)
            self.assertEqual(
                strategy._risk_weight(0.90, 0.15),
                profile.minimum_risk_weight,
            )

    def test_invalid_floor_is_rejected(self):
        with self.assertRaises(ValueError):
            TailRiskOverlayStrategy(TailOverlayProfile("INVALID", 0.71))

    def test_target_sums_to_one(self):
        strategy = TailRiskOverlayStrategy(PROFILES[2])
        market = {"QQQ": {
            "ProbabilityLoss21": 0.40,
            "BaseLossProbability21": 0.10,
        }}

        target = strategy._desired_target(market)

        self.assertAlmostEqual(sum(target.values()), 1.0)
        self.assertLessEqual(target["QQQ"], 0.70)
        self.assertGreaterEqual(target["QQQ"], 0.30)


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from validation.multi_horizon_probability import (  # noqa: E402
    CompositeProbabilityAllocationStrategy,
)


class MultiHorizonProbabilityTests(unittest.TestCase):
    def test_no_stress_keeps_seventy_percent(self):
        strategy = CompositeProbabilityAllocationStrategy()

        weight = strategy._risk_weight(0.70, 0.60, 0.10, 0.15)

        self.assertEqual(weight, 0.70)

    def test_both_stresses_reduce_risk_more_than_either_one(self):
        direction_only = CompositeProbabilityAllocationStrategy()._risk_weight(
            0.35, 0.60, 0.10, 0.15
        )
        tail_only = CompositeProbabilityAllocationStrategy()._risk_weight(
            0.70, 0.60, 0.40, 0.15
        )
        both = CompositeProbabilityAllocationStrategy()._risk_weight(
            0.35, 0.60, 0.40, 0.15
        )

        self.assertLess(both, direction_only)
        self.assertLess(both, tail_only)

    def test_composite_target_respects_risk_cap(self):
        strategy = CompositeProbabilityAllocationStrategy()
        market = {"QQQ": {
            "ProbabilityUp42": 0.10,
            "BaseUpProbability42": 0.70,
            "ProbabilityLoss21": 0.80,
            "BaseLossProbability21": 0.10,
        }}

        target = strategy._desired_target(market)

        self.assertAlmostEqual(sum(target.values()), 1.0)
        self.assertGreaterEqual(target["QQQ"], 0.30)
        self.assertLessEqual(target["QQQ"], 0.70)


if __name__ == "__main__":
    unittest.main()

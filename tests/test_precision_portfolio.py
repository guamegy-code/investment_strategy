import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from validation.precision_portfolio import (  # noqa: E402
    PROFILES,
    PrecisionWarningAllocationStrategy,
)


def qqq(probability, q75=0.10, q90=0.20, q95=0.30, **extra):
    values = {
        "ProbabilityLoss21": probability,
        "ProbabilityQ75": q75,
        "ProbabilityQ90": q90,
        "ProbabilityQ95": q95,
        "DRAWDOWN120": -0.05,
        "REBOUND20": 0.02,
    }
    values.update(extra)
    return values


class PrecisionPortfolioTests(unittest.TestCase):
    def test_staged_profile_uses_q90_and_q95_allocations(self):
        strategy = PrecisionWarningAllocationStrategy(PROFILES[1])

        warning, _ = strategy._next_risk_target(qqq(0.25))
        strategy.risk_target = warning
        severe, _ = strategy._next_risk_target(qqq(0.35))

        self.assertEqual(warning, 0.60)
        self.assertEqual(severe, 0.50)

    def test_recovery_occurs_in_ten_point_steps(self):
        strategy = PrecisionWarningAllocationStrategy(PROFILES[1])
        strategy.risk_target = 0.50

        first, _ = strategy._next_risk_target(qqq(0.05))
        strategy.risk_target = first
        second, _ = strategy._next_risk_target(qqq(0.05))

        self.assertEqual(first, 0.60)
        self.assertEqual(second, 0.70)

    def test_exploratory_guard_blocks_late_entry(self):
        strategy = PrecisionWarningAllocationStrategy(PROFILES[2])

        weight, reason = strategy._next_risk_target(qqq(
            0.40, DRAWDOWN120=-0.20, REBOUND20=0.10
        ))

        self.assertEqual(weight, 0.70)
        self.assertEqual(reason, "NORMAL")

    def test_target_never_exceeds_seventy_percent(self):
        for risk_weight in (0.50, 0.60, 0.70):
            target = PrecisionWarningAllocationStrategy._target_for_risk(
                risk_weight
            )
            self.assertLessEqual(target["QQQ"], 0.70)
            self.assertAlmostEqual(sum(target.values()), 1.0)


if __name__ == "__main__":
    unittest.main()

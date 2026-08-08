import sys
import unittest
from pathlib import Path

import numpy as np


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from validation.ordinal_hazard import (  # noqa: E402
    fit_ordinal_hazard,
    multi_barrier_breach_days,
    ordinal_person_period_design,
)


class OrdinalHazardTests(unittest.TestCase):
    def test_multi_barrier_days_are_nested(self):
        close = np.array([100.0, 98.0, 96.0, 94.0, 91.0])

        breach = multi_barrier_breach_days(close, [0], 4)

        np.testing.assert_array_equal(breach[0], np.array([2, 3, 4]))

    def test_person_period_stacks_all_barrier_risk_sets(self):
        features = np.array([[0.0, 1.0]])
        breach = np.array([[2, 7, 0]])

        design, outcome = ordinal_person_period_design(features, breach)

        self.assertEqual(len(design), 7)
        self.assertEqual(outcome.sum(), 2.0)

    def test_probabilities_respect_loss_severity_order(self):
        train_x = np.arange(120, dtype=float).reshape(60, 2)
        breach = np.zeros((60, 3), dtype=int)
        breach[30:, 0] = 5
        breach[40:, 1] = 10
        breach[50:, 2] = 18

        probability, base = fit_ordinal_hazard(
            train_x, breach, np.array([118.0, 119.0])
        )

        self.assertTrue(np.all((probability > 0.0) & (probability < 1.0)))
        self.assertTrue(np.all(np.diff(probability) <= 0.0))
        self.assertTrue(np.all(np.diff(base) <= 0.0))


if __name__ == "__main__":
    unittest.main()

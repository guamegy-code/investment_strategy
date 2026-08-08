import sys
import unittest
from pathlib import Path

import numpy as np


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from validation.discrete_hazard import (  # noqa: E402
    first_breach_days,
    fit_discrete_hazard,
    person_period_design,
)


class DiscreteHazardTests(unittest.TestCase):
    def test_first_breach_day_uses_earliest_barrier_crossing(self):
        close = np.array([100.0, 99.0, 94.0, 90.0, 100.0])

        breach = first_breach_days(close, [0], 4, -0.05)

        self.assertEqual(breach[0], 2)

    def test_person_period_rows_stop_at_event_bucket(self):
        features = np.array([[0.0, 1.0], [1.0, 0.0]])
        breach = np.array([7, 0])

        design, outcome = person_period_design(features, breach, False)

        self.assertEqual(len(design), 6)
        self.assertEqual(outcome.sum(), 1.0)

    def test_fitted_cumulative_hazard_is_a_probability(self):
        train_x = np.arange(80, dtype=float).reshape(40, 2)
        breach = np.array([0] * 30 + [4, 7, 9, 12, 14, 17, 18, 20, 21, 5])

        probability, base = fit_discrete_hazard(
            train_x, breach, np.array([79.0, 80.0]), True
        )

        self.assertGreater(probability, 0.0)
        self.assertLess(probability, 1.0)
        self.assertGreater(base, 0.0)
        self.assertLess(base, 1.0)


if __name__ == "__main__":
    unittest.main()

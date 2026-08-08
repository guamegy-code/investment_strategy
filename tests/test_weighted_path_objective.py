import sys
import unittest
from pathlib import Path

import numpy as np


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from validation.probabilistic_allocation import _fit_probability  # noqa: E402


class WeightedPathObjectiveTests(unittest.TestCase):
    def test_weighted_probability_remains_a_valid_probability(self):
        train_x = np.arange(20, dtype=float).reshape(-1, 1)
        train_y = np.array([0.0] * 16 + [1.0] * 4)

        probability, base = _fit_probability(
            train_x, train_y, np.array([19.0]), 1.0, 4.0
        )

        self.assertGreater(probability, 0.0)
        self.assertLess(probability, 1.0)
        self.assertAlmostEqual(base, 5.0 / 22.0)

    def test_nonpositive_class_weight_is_rejected(self):
        with self.assertRaises(ValueError):
            _fit_probability(
                np.array([[0.0], [1.0]]),
                np.array([0.0, 1.0]),
                np.array([0.5]),
                1.0,
                0.0,
            )


if __name__ == "__main__":
    unittest.main()

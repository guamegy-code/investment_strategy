import sys
import unittest
from pathlib import Path

import numpy as np


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy import EXPANDING_RISK_FORECAST_30_70  # noqa: E402


class ExpandingRiskForecastStrategyTests(unittest.TestCase):
    def test_target_sums_to_one_and_respects_thirty_to_seventy_range(self):
        strategy = EXPANDING_RISK_FORECAST_30_70()

        strategy.forecast_stress_ratio = 1.0
        normal = strategy._desired_target()
        strategy.forecast_stress_ratio = 2.0
        stressed = strategy._desired_target()

        self.assertAlmostEqual(sum(normal.values()), 1.0)
        self.assertAlmostEqual(sum(stressed.values()), 1.0)
        self.assertEqual(normal["QQQ"], 0.70)
        self.assertEqual(stressed["QQQ"], 0.30)

    def test_unresolved_observation_is_not_added_to_training(self):
        strategy = EXPANDING_RISK_FORECAST_30_70()
        strategy._closes = [100.0] * 21
        strategy._pending_observations = [{
            "position": 0,
            "features": np.ones(7),
        }]

        strategy._resolve_observations(20)

        self.assertEqual(strategy.model_samples, 0)
        self.assertEqual(len(strategy._pending_observations), 1)

    def test_observation_resolves_after_complete_twenty_one_day_path(self):
        strategy = EXPANDING_RISK_FORECAST_30_70()
        strategy._closes = [100.0] + [99.0] * 21
        strategy._pending_observations = [{
            "position": 0,
            "features": np.ones(7),
        }]

        strategy._resolve_observations(21)

        self.assertEqual(strategy.model_samples, 1)
        self.assertEqual(len(strategy._pending_observations), 0)
        self.assertAlmostEqual(strategy._training_maximum_loss[0], 0.01)

    def test_ridge_forecast_returns_nonnegative_risk(self):
        train_x = np.arange(280, dtype=float).reshape(40, 7)
        train_y = np.linspace(0.01, 0.20, 40)

        prediction, base = EXPANDING_RISK_FORECAST_30_70._ridge_forecast(
            train_x, train_y, np.arange(7, dtype=float)
        )

        self.assertGreaterEqual(prediction, 0.0)
        self.assertGreater(base, 0.0)


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from validation.probabilistic_allocation import (  # noqa: E402
    FEATURE_COLUMNS,
    ProbabilityConfig,
    ProbabilisticDownsideAllocationStrategy,
    walk_forward_probabilities,
)


class ProbabilisticAllocationTests(unittest.TestCase):
    def test_risk_weight_never_exceeds_seventy_percent(self):
        strategy = ProbabilisticDownsideAllocationStrategy()

        favorable = strategy._risk_weight(0.90, 0.60)
        unfavorable = strategy._risk_weight(0.20, 0.60)

        self.assertEqual(favorable, 0.70)
        self.assertEqual(unfavorable, 0.30)

    def test_deadband_avoids_small_probability_changes(self):
        strategy = ProbabilisticDownsideAllocationStrategy()

        self.assertEqual(strategy._risk_weight(0.56, 0.60), 0.70)
        self.assertLess(strategy._risk_weight(0.50, 0.60), 0.70)

    def test_target_sums_to_one_and_safe_assets_are_equal(self):
        strategy = ProbabilisticDownsideAllocationStrategy()
        market = {
            "QQQ": {
                "ProbabilityUp": 0.40,
                "BaseUpProbability": 0.60,
            }
        }

        target = strategy._desired_target(market)

        self.assertAlmostEqual(sum(target.values()), 1.0)
        self.assertLessEqual(target["QQQ"], 0.70)
        self.assertEqual(target["BND"], target["BIL"])

    def test_walk_forward_forecast_does_not_use_unresolved_outcomes(self):
        periods = 150
        dates = pd.bdate_range("2000-01-03", periods=periods)
        close = np.linspace(100.0, 160.0, periods)
        data = pd.DataFrame(index=dates)
        data["QQQ_Close"] = close
        data["QQQ_EMA200"] = close * 0.95
        for column in FEATURE_COLUMNS:
            if column != "QQQ_EMA200_DISTANCE":
                data[column] = np.linspace(0.1, 1.0, periods)
        config = ProbabilityConfig(horizon_days=21, minimum_samples=1)

        forecasts = walk_forward_probabilities(data, config)
        first_decision = ~dates.to_period("M").duplicated()
        decision_positions = np.flatnonzero(first_decision)

        for position in decision_positions:
            expected = np.sum(decision_positions + 21 <= position)
            self.assertEqual(forecasts["ModelSamples"].iloc[position], expected)

    def test_future_price_changes_do_not_change_an_earlier_forecast(self):
        periods = 420
        dates = pd.bdate_range("2000-01-03", periods=periods)
        close = 100.0 + np.arange(periods) * 0.1
        data = pd.DataFrame(index=dates)
        data["QQQ_Close"] = close
        data["QQQ_EMA200"] = close * 0.98
        for column in FEATURE_COLUMNS:
            if column != "QQQ_EMA200_DISTANCE":
                data[column] = np.sin(np.arange(periods) / 30.0)
        config = ProbabilityConfig(horizon_days=21, minimum_samples=2)
        forecast_date = dates[300]

        original = walk_forward_probabilities(data, config)
        changed = data.copy()
        changed.loc[changed.index > forecast_date, "QQQ_Close"] *= 10.0
        revised = walk_forward_probabilities(changed, config)

        self.assertEqual(
            original.at[forecast_date, "ProbabilityUp"],
            revised.at[forecast_date, "ProbabilityUp"],
        )

    def test_invalid_risk_cap_is_rejected(self):
        config = ProbabilityConfig(maximum_risk_weight=0.71)

        with self.assertRaises(ValueError):
            ProbabilisticDownsideAllocationStrategy(config)


if __name__ == "__main__":
    unittest.main()

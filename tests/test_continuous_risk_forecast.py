import sys
import unittest
from pathlib import Path

import numpy as np


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from validation.continuous_risk_forecast import (  # noqa: E402
    MAXIMUM_RISK_WEIGHT,
    MINIMUM_RISK_WEIGHT,
    continuous_risk_weight,
    fit_ridge_risk,
    forward_continuous_risk_targets,
    _promotion_report,
)


class ContinuousRiskForecastTests(unittest.TestCase):
    def test_forward_targets_measure_downside_and_path_loss(self):
        close = np.array([100.0, 102.0, 99.0, 95.0, 97.0])

        volatility, loss = forward_continuous_risk_targets(close, [0], 4)

        self.assertGreater(volatility[0], 0.0)
        self.assertAlmostEqual(loss[0], 0.05)

    def test_ridge_prediction_and_base_are_nonnegative(self):
        train_x = np.arange(80, dtype=float).reshape(40, 2)
        train_y = np.linspace(0.01, 0.20, 40)

        prediction, base = fit_ridge_risk(
            train_x, train_y, np.array([80.0, 81.0])
        )

        self.assertGreaterEqual(prediction, 0.0)
        self.assertGreater(base, 0.0)

    def test_weight_is_monotonic_and_respects_pension_cap(self):
        normal = continuous_risk_weight(1.0, 1.75)
        elevated = continuous_risk_weight(1.4, 1.75)
        extreme = continuous_risk_weight(2.0, 1.75)

        self.assertEqual(normal, MAXIMUM_RISK_WEIGHT)
        self.assertGreater(normal, elevated)
        self.assertEqual(extreme, MINIMUM_RISK_WEIGHT)

    def test_promotion_requires_every_later_period_to_pass(self):
        import pandas as pd

        metrics = pd.DataFrame([
            {"Strategy": strategy, "Period": period, "CAGR": cagr,
             "MDD": mdd, "Sharpe": sharpe}
            for period, values in {
                "VALIDATION_2018_2022": ((0.08, -0.25, 0.40), (0.07, -0.20, 0.45)),
                "LOCK_2023_PRESENT": ((0.12, -0.15, 0.80), (0.09, -0.14, 0.75)),
            }.items()
            for strategy, (cagr, mdd, sharpe) in zip(
                ("STATIC_RETIREMENT_7030", "CANDIDATE"), values
            )
        ])
        selection = pd.DataFrame({"Strategy": ["CANDIDATE"], "Selected": [True]})

        report = _promotion_report(metrics, selection)

        self.assertFalse(bool(report["Promote"].iloc[0]))


if __name__ == "__main__":
    unittest.main()

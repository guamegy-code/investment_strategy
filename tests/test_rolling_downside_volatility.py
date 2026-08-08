import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from validation.rolling_downside_volatility import (  # noqa: E402
    MAXIMUM_RISK_WEIGHT,
    MINIMUM_RISK_WEIGHT,
    build_har_downside_features,
    fit_log_ridge_risk,
    limit_monthly_recovery,
    rolling_risk_weight,
)


class RollingDownsideVolatilityTests(unittest.TestCase):
    def test_har_features_do_not_change_before_a_future_price_edit(self):
        index = pd.date_range("2020-01-01", periods=100, freq="B")
        close = pd.Series(np.linspace(100.0, 120.0, len(index)), index=index)
        changed = close.copy()
        changed.iloc[90:] *= 0.5

        original_features = build_har_downside_features(close)
        changed_features = build_har_downside_features(changed)

        pd.testing.assert_frame_equal(
            original_features.iloc[:90], changed_features.iloc[:90]
        )

    def test_log_ridge_forecast_is_nonnegative(self):
        train_x = np.arange(160, dtype=float).reshape(40, 4)
        train_y = np.linspace(0.01, 0.20, 40)

        prediction, base = fit_log_ridge_risk(
            train_x, train_y, np.array([160.0, 161.0, 162.0, 163.0])
        )

        self.assertGreaterEqual(prediction, 0.0)
        self.assertGreater(base, 0.0)

    def test_weight_range_and_asymmetric_recovery(self):
        self.assertEqual(rolling_risk_weight(1.0), MAXIMUM_RISK_WEIGHT)
        self.assertEqual(rolling_risk_weight(2.0), MINIMUM_RISK_WEIGHT)
        self.assertAlmostEqual(limit_monthly_recovery(0.50, 0.70), 0.55)
        self.assertEqual(limit_monthly_recovery(0.70, 0.50), 0.50)


if __name__ == "__main__":
    unittest.main()

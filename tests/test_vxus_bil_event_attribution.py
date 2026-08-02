import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from validation.vxus_bil_event_attribution import forward_path_metrics


class VXUSBILEventAttributionTests(unittest.TestCase):
    def test_forward_path_uses_exact_trading_day_horizon(self):
        dates = pd.bdate_range("2024-01-02", periods=4)
        values = pd.Series([100.0, 90.0, 99.0, 110.0], index=dates)

        forward_return, max_drawdown = forward_path_metrics(
            values, dates[0], horizon=3
        )

        self.assertAlmostEqual(forward_return, 0.10)
        self.assertAlmostEqual(max_drawdown, -0.10)

    def test_incomplete_forward_window_is_missing(self):
        dates = pd.bdate_range("2024-01-02", periods=3)
        values = pd.Series([100.0, 101.0, 102.0], index=dates)

        forward_return, max_drawdown = forward_path_metrics(
            values, dates[1], horizon=2
        )

        self.assertTrue(pd.isna(forward_return))
        self.assertTrue(pd.isna(max_drawdown))


if __name__ == "__main__":
    unittest.main()

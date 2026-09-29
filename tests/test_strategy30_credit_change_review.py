"""Credit-change research uses only observations available before each session."""

import sys
import unittest
from pathlib import Path

import pandas as pd

legacy = Path(__file__).resolve().parents[1] / "src" / "legacy-python"
sys.path.insert(0, str(legacy))
sys.path.insert(0, str(legacy / "validation"))

from strategy30_credit_change_review import credit_features  # noqa: E402


class CreditChangeTests(unittest.TestCase):
    def test_lagged_differences_and_no_future_data(self):
        dates = pd.bdate_range("2020-01-01", periods=300)
        values = pd.Series([2 + i * 0.01 for i in range(300)], index=dates)
        base = credit_features(values.iloc[:-1], dates[:-1])
        with_future = credit_features(values, dates)

        pd.testing.assert_frame_equal(base, with_future.iloc[:-1], check_freq=False)
        self.assertAlmostEqual(with_future.loc[dates[25], "level"], values.loc[dates[24]])
        self.assertAlmostEqual(with_future.loc[dates[25], "diff5"], 0.05)
        self.assertAlmostEqual(with_future.loc[dates[25], "diff20"], 0.20)


if __name__ == "__main__":
    unittest.main()

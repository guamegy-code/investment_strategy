"""Check that research features are built from delayed QQQ-session values."""

import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

legacy = Path(__file__).resolve().parents[1] / "src" / "legacy-python"
sys.path.insert(0, str(legacy))
sys.path.insert(0, str(legacy / "validation"))

from strategy28_hy_oas_review import attach_credit_features  # noqa: E402


class ResearchFeatureTests(unittest.TestCase):
    def test_one_session_lag_and_changes_are_not_same_day(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dates = pd.bdate_range("2025-01-02", periods=280)
            pd.DataFrame({"Close": 100}, index=dates).rename_axis("Date").to_csv(
                root / "QQQ.csv"
            )
            baa = pd.Series(2.0, index=dates)
            hy = pd.Series(4.0, index=dates)
            hy.iloc[260] = 5.0
            attach_credit_features(root, baa, hy, lag=1)
            result = pd.read_csv(root / "QQQ.csv", index_col="Date", parse_dates=True)
            self.assertEqual(result.loc[dates[260], "HY_OAS_LEVEL"], 4.0)
            self.assertEqual(result.loc[dates[261], "HY_OAS_LEVEL"], 5.0)
            self.assertEqual(result.loc[dates[261], "HY_OAS_DIFF_5"], 1.0)
            self.assertTrue(pd.notna(result.loc[dates[261], "HY_OAS_Z252"]))


if __name__ == "__main__":
    unittest.main()

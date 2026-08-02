import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from validation.extended_data import _cash_total_return, _stitch


class ExtendedDataTests(unittest.TestCase):
    @staticmethod
    def _prices(index, values):
        frame = pd.DataFrame(index=index)
        for column in ("Close", "High", "Low", "Open"):
            frame[column] = values
        frame["Volume"] = 0.0
        return frame

    def test_stitch_uses_proxy_only_before_actual_inception(self):
        dates = pd.date_range("2020-01-01", periods=5)
        proxy = self._prices(dates, [10, 11, 12, 13, 14])
        actual = self._prices(dates[2:], [24, 26, 28])
        combined, seam = _stitch(actual, proxy)
        self.assertEqual(seam, dates[2])
        self.assertEqual(combined.loc[dates[1], "Close"], 22)
        self.assertEqual(combined.loc[dates[2], "Close"], 24)
        self.assertEqual(len(combined), 5)

    def test_cash_proxy_compounds_positive_yield(self):
        dates = pd.date_range("2020-01-01", periods=3)
        yields = self._prices(dates, [5.0, 5.0, 5.0])
        cash = _cash_total_return(yields)
        self.assertGreater(cash["Close"].iloc[-1], cash["Close"].iloc[0])
        self.assertTrue((cash["Open"] == cash["Close"]).all())


if __name__ == "__main__":
    unittest.main()

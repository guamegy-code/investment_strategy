import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from walkforward import WalkForwardProfileComparison


def history(annual_returns):
    values = []
    dates = []
    equity = 1.0
    for year, annual_return in annual_returns.items():
        year_dates = pd.bdate_range(f"{year}-01-02", periods=2)
        values.extend([equity, equity * (1 + annual_return)])
        dates.extend(year_dates)
        equity *= 1 + annual_return
    return pd.DataFrame({"Portfolio": values}, index=dates)


class WalkForwardTests(unittest.TestCase):
    def test_selection_uses_only_trailing_years(self):
        balanced = history({year: 0.10 for year in range(2010, 2017)})
        return_profile = history({
            2010: 0.20,
            2011: 0.20,
            2012: 0.20,
            2013: 0.20,
            2014: 0.20,
            2015: -0.20,
            2016: -0.20,
        })
        benchmark = history({year: 0.05 for year in range(2010, 2017)})

        comparison = WalkForwardProfileComparison(
            {"BALANCED": balanced, "RETURN": return_profile},
            benchmark,
            lookback_years=5,
        )
        selected = comparison.selected_years()

        self.assertEqual(selected.iloc[0]["TestYear"], 2015)
        self.assertEqual(selected.iloc[0]["SelectedProfile"], "RETURN")
        self.assertAlmostEqual(selected.iloc[0]["SelectedReturn"], -0.20)


if __name__ == "__main__":
    unittest.main()

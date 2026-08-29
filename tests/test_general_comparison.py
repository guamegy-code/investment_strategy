import sys
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "legacy-python"))

from config import GENERAL_COMPARISON_START_DATE  # noqa: E402
from general_comparison import (  # noqa: E402
    DATA_AVAILABILITY_EXCEPTION,
    GENERAL_COMPARISON_SCOPE,
    general_comparison_reports,
)


def result(name, start, end, daily_return=0.001):
    dates = pd.bdate_range(start, end)
    portfolio = pd.Series(
        [(1.0 + daily_return) ** index for index in range(len(dates))],
        index=dates,
    )
    history = pd.DataFrame({
        "Portfolio": portfolio,
        "TransactionCosts": pd.Series(
            [index * 0.0001 for index in range(len(dates))],
            index=dates,
        ),
    })
    return {
        "history": history,
        "summary": {"Strategy": name},
    }


class GeneralComparisonTests(unittest.TestCase):
    def test_canonical_date_is_exact_first_trading_day(self):
        self.assertEqual(GENERAL_COMPARISON_START_DATE, "2012-01-03")

    def test_general_report_clips_earlier_history_to_canonical_start(self):
        comparison, exceptions = general_comparison_reports([
            result("EARLY", "2011-01-03", "2014-12-31"),
            result("CANONICAL", "2012-01-03", "2014-12-31"),
        ])

        self.assertTrue(exceptions.empty)
        self.assertEqual(set(comparison["ComparisonScope"]), {
            GENERAL_COMPARISON_SCOPE,
        })
        self.assertEqual(set(comparison["StartDate"]), {
            pd.Timestamp("2012-01-03"),
        })
        self.assertEqual(len(set(comparison["EndDate"])), 1)
        self.assertEqual(len(set(comparison["Observations"])), 1)

    def test_later_data_is_reported_as_exception_not_mixed(self):
        comparison, exceptions = general_comparison_reports([
            result("GENERAL", "2012-01-03", "2026-07-31"),
            result("PRODUCT", "2022-06-30", "2026-07-31"),
        ])

        self.assertEqual(comparison["Strategy"].tolist(), ["GENERAL"])
        self.assertEqual(exceptions["Strategy"].tolist(), ["PRODUCT"])
        self.assertEqual(
            exceptions.iloc[0]["ComparisonScope"],
            DATA_AVAILABILITY_EXCEPTION,
        )
        self.assertEqual(
            exceptions.iloc[0]["Reason"],
            "REQUIRED_DATA_STARTS_AFTER_CANONICAL_DATE",
        )

    def test_general_rows_share_the_earliest_common_end(self):
        comparison, _ = general_comparison_reports([
            result("SHORT", "2012-01-03", "2025-12-31"),
            result("LONG", "2012-01-03", "2026-07-31"),
        ])

        self.assertEqual(set(comparison["EndDate"]), {
            pd.Timestamp("2025-12-31"),
        })


if __name__ == "__main__":
    unittest.main()

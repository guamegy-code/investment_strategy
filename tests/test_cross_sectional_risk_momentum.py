import json
import sys
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "src" / "legacy-python"
if str(LEGACY) not in sys.path:
    sys.path.insert(0, str(LEGACY))

from validation.cross_sectional_risk_momentum import (  # noqa: E402
    CANDIDATES,
    CrossSectionalRiskStrategy,
    _assert_lock,
)
from backtest import Backtest  # noqa: E402


class CrossSectionalRiskMomentumTests(unittest.TestCase):
    def schedule(self, qqq=0.70, tdf=0.30, bil=0.0):
        return {
            __import__("pandas").Timestamp("2025-02-03"): {
                "state": "BULL",
                "target": {"QQQ": qqq, "TDF2050_PROXY": tdf, "BIL": bil},
                "rebalance": False,
                "days": 5,
                "reason": None,
                "risk_off_score": 0,
                "recovery_score": 6,
            }
        }

    def market(self):
        return {
            ticker: {"XSMOM_12_1_LAG1": float(rank)}
            for rank, ticker in enumerate(CANDIDATES)
        }

    def test_lock_matches_implementation(self):
        lock = _assert_lock()
        self.assertEqual(lock["data"]["candidates"], list(CANDIDATES))

    def test_dynamic_selects_top_three_without_cash_filter(self):
        activity = []
        strategy = CrossSectionalRiskStrategy(self.schedule(), "dynamic", activity.append)
        signal = strategy.evaluate(
            __import__("pandas").Timestamp("2025-02-03"),
            self.market(),
            object(),
        )
        expected = set(CANDIDATES[-3:])
        self.assertEqual(set(strategy.selected), expected)
        self.assertAlmostEqual(sum(signal["target"][ticker] for ticker in CANDIDATES), 0.70)
        for ticker in expected:
            self.assertAlmostEqual(signal["target"][ticker], 0.70 / 3)
        self.assertTrue(signal["rebalance"])
        self.assertEqual(signal["days"], 1)

    def test_bear_keeps_risk_sleeve_at_zero(self):
        strategy = CrossSectionalRiskStrategy(self.schedule(0.0, 0.20, 0.80), "dynamic", lambda row: None)
        signal = strategy.evaluate(
            __import__("pandas").Timestamp("2025-02-03"),
            self.market(),
            object(),
        )
        self.assertAlmostEqual(sum(signal["target"][ticker] for ticker in CANDIDATES), 0.0)
        self.assertFalse(signal["rebalance"])
        self.assertEqual(signal["target"]["TDF2050_PROXY"], 0.20)
        self.assertEqual(signal["target"]["BIL"], 0.80)

    def test_static_equal_uses_entire_universe(self):
        strategy = CrossSectionalRiskStrategy(self.schedule(), "static", lambda row: None)
        signal = strategy.evaluate(
            __import__("pandas").Timestamp("2025-02-03"),
            self.market(),
            object(),
        )
        for ticker in CANDIDATES:
            self.assertAlmostEqual(signal["target"][ticker], 0.70 / len(CANDIDATES))

    def test_backtest_reads_custom_market_field_from_series_keys(self):
        strategy = CrossSectionalRiskStrategy(self.schedule(), "dynamic", lambda row: None)
        backtest = Backtest.__new__(Backtest)
        backtest.strategy = strategy
        backtest.tickers = strategy.required_tickers
        row = pd.Series({
            **{f"{ticker}_Close": 100.0 for ticker in strategy.required_tickers},
            **{f"{ticker}_XSMOM_12_1_LAG1": 1.0 for ticker in CANDIDATES},
        })
        market = backtest.get_market(row)
        self.assertEqual(market["QQQ"]["XSMOM_12_1_LAG1"], 1.0)


if __name__ == "__main__":
    unittest.main()

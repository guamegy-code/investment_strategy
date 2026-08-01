import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chart import rebalance_directions


class RebalanceMarkerTests(unittest.TestCase):
    def test_signal_is_marked_on_next_execution_date(self):
        dates = pd.bdate_range("2024-01-02", periods=4)
        history = pd.DataFrame({
            "Weights": [
                {"QQQ": 0.0, "BND": 0.0},
                {"QQQ": 0.7, "BND": 0.3},
                {"QQQ": 0.7, "BND": 0.3},
                {"QQQ": 0.4, "BND": 0.6},
            ]
        }, index=dates)
        trades = pd.DataFrame([
            {"Date": dates[1], "Ticker": "QQQ", "Shares": 1.0},
            {"Date": dates[1], "Ticker": "BND", "Shares": 0.5},
            {"Date": dates[3], "Ticker": "QQQ", "Shares": -0.4},
            {"Date": dates[3], "Ticker": "BND", "Shares": 0.4},
        ])
        rebalances = [
            {"Date": dates[0], "Target": {"QQQ": 0.7, "BND": 0.3}},
            {"Date": dates[2], "Target": {"QQQ": 0.4, "BND": 0.6}},
        ]

        directions = rebalance_directions(history, trades, rebalances)

        self.assertEqual(directions[dates[1]], "up")
        self.assertEqual(directions[dates[3]], "down")
        self.assertNotIn(dates[0], directions)
        self.assertNotIn(dates[2], directions)


if __name__ == "__main__":
    unittest.main()

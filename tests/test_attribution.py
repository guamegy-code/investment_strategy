import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from attribution import DynamicAllocationAttribution


class AttributionTests(unittest.TestCase):
    def setUp(self):
        dates = pd.bdate_range("2024-01-02", periods=70)
        self.history = pd.DataFrame({
            "Portfolio": [1 + index * 0.01 for index in range(70)],
            "TransactionCosts": [index * 0.0001 for index in range(70)],
            "Weights": [
                {"QQQ": 0.70 if index < 10 else 0.50, "BND": 0.20, "GLD": 0.10}
                for index in range(70)
            ],
            "StrategyState": ["BULL"] * 10 + ["CAUTION"] * 60,
            "RiskOffScore": [0] * 10 + [4] * 60,
            "RecoveryScore": [6] * 10 + [2] * 60,
        }, index=dates)
        self.market = pd.DataFrame({
            "QQQ_Close": [100 - index * 0.5 for index in range(70)],
        }, index=dates)
        self.benchmark = pd.DataFrame({
            "Portfolio": [1 + index * 0.008 for index in range(70)],
        }, index=dates)
        self.rebalances = [
            {
                "Date": dates[0],
                "Target": {"QQQ": 0.70, "BND": 0.20, "GLD": 0.10},
                "Reason": "INITIAL",
            },
            {
                "Date": dates[10],
                "Target": {"QQQ": 0.50, "BND": 0.35, "GLD": 0.15},
                "Reason": "BULL->CAUTION(risk_off=4,recovery=2)",
            },
        ]

    def test_state_and_transition_reports(self):
        attribution = DynamicAllocationAttribution(
            self.history,
            self.market,
            self.rebalances,
            self.benchmark,
        )
        states = attribution.state_summary()
        events = attribution.transition_events()

        self.assertEqual(set(states["State"]), {"BULL", "CAUTION"})
        self.assertEqual(len(events), 1)
        self.assertEqual(events.iloc[0]["Type"], "DEFENSIVE")
        self.assertTrue(events.iloc[0]["Success20D"])
        self.assertLess(events.iloc[0]["QQQForwardReturn20D"], 0)


if __name__ == "__main__":
    unittest.main()

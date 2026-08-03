import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from attribution import RetirementAllocationAttribution


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
        attribution = RetirementAllocationAttribution(
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

    def test_state_market_quality_uses_complete_forward_windows(self):
        attribution = RetirementAllocationAttribution(
            self.history,
            self.market,
            self.rebalances,
            self.benchmark,
        )

        quality = attribution.state_market_quality().set_index("State")

        self.assertEqual(quality.loc["BULL", "Samples5D"], 10)
        self.assertEqual(quality.loc["CAUTION", "Samples5D"], 55)
        self.assertEqual(quality.loc["CAUTION", "Samples20D"], 40)
        self.assertEqual(quality.loc["CAUTION", "Samples60D"], 0)
        self.assertEqual(quality.loc["BULL", "PositiveRate5D"], 0.0)
        self.assertLess(
            quality.loc["BULL", "AvgForwardMaxDrawdown5D"], 0.0
        )
        self.assertEqual(
            quality.loc["BULL", "AvgForwardMaxUpside5D"], 0.0
        )

    def test_state_market_quality_is_included_in_all_reports(self):
        attribution = RetirementAllocationAttribution(
            self.history,
            self.market,
            self.rebalances,
            self.benchmark,
        )

        self.assertIn("state_market_quality", attribution.all_reports())

    def test_transition_quality_measures_detection_delay_and_false_alarm(self):
        attribution = RetirementAllocationAttribution(
            self.history,
            self.market,
            self.rebalances,
            self.benchmark,
        )

        event = attribution.transition_events().iloc[0]
        quality = attribution.transition_quality().iloc[0]

        self.assertEqual(event["SignalDirection"], "RISK_OFF")
        self.assertEqual(event["DaysFromPrior120DHigh"], 10)
        self.assertAlmostEqual(event["DrawdownFromPrior120DHigh"], -0.05)
        self.assertTrue(event["DirectionalSuccess20D"])
        self.assertFalse(event["DirectionalFalseAlarm20D"])
        self.assertFalse(event["NoMeaningfulDownside20D"])
        self.assertEqual(event["StateDurationDays"], 60)
        self.assertEqual(quality["DirectionalSuccessRate20D"], 1.0)

    def test_recovery_relapse_is_detected_from_state_path(self):
        history = self.history.copy()
        history["StrategyState"] = (
            ["BULL"] * 10
            + ["BEAR"] * 10
            + ["RECOVERY"] * 5
            + ["BEAR"] * 45
        )
        rebalances = [
            self.rebalances[0],
            {
                "Date": history.index[10],
                "Target": {"QQQ": 0.0},
                "Reason": "BULL->BEAR(risk_off=6,recovery=0)",
            },
            {
                "Date": history.index[20],
                "Target": {"QQQ": 0.5},
                "Reason": "BEAR->RECOVERY(risk_off=2,recovery=4)",
            },
            {
                "Date": history.index[25],
                "Target": {"QQQ": 0.0},
                "Reason": "RECOVERY->BEAR(risk_off=6,recovery=0)",
            },
        ]
        attribution = RetirementAllocationAttribution(
            history,
            self.market,
            rebalances,
            self.benchmark,
        )

        recovery = attribution.transition_events().query(
            "Transition == 'BEAR->RECOVERY'"
        ).iloc[0]

        self.assertTrue(recovery["RecoveryRelapseToBear20D"])
        self.assertTrue(recovery["RecoveryRelapseToBear60D"])
        self.assertEqual(recovery["DaysUntilBearRelapse"], 5)
        self.assertEqual(recovery["StateDurationDays"], 5)
        self.assertTrue(recovery["ShortLivedState5D"])

    def test_safe_asset_rotation_is_not_a_state_transition(self):
        rebalances = self.rebalances + [{
            "Date": self.history.index[20],
            "Target": {"QQQ": 0.5, "BIL": 0.4, "GLD": 0.1},
            "Reason": "SAFE_ROTATION_BND->BIL",
        }]
        attribution = RetirementAllocationAttribution(
            self.history,
            self.market,
            rebalances,
            self.benchmark,
        )

        events = attribution.transition_events()

        self.assertEqual(len(events), 1)
        self.assertEqual(events.iloc[0]["Transition"], "BULL->CAUTION")

    def test_boolean_transition_rates_count_every_true_event(self):
        rebalances = [
            self.rebalances[0],
            self.rebalances[1],
            {
                "Date": self.history.index[30],
                "Target": {"QQQ": 0.7, "BND": 0.2, "GLD": 0.1},
                "Reason": "CAUTION->BULL(risk_off=1,recovery=5)",
            },
            {
                "Date": self.history.index[40],
                "Target": {"QQQ": 0.5, "BND": 0.35, "GLD": 0.15},
                "Reason": "BULL->CAUTION(risk_off=5,recovery=1)",
            },
        ]
        flat_market = self.market.copy()
        flat_market["QQQ_Close"] = 100.0
        attribution = RetirementAllocationAttribution(
            self.history,
            flat_market,
            rebalances,
            self.benchmark,
        )

        quality = attribution.transition_quality().query(
            "Transition == 'BULL->CAUTION'"
        ).iloc[0]

        self.assertEqual(quality["Count"], 2)
        self.assertEqual(quality["NoMeaningfulDownsideRate20D"], 1.0)


if __name__ == "__main__":
    unittest.main()

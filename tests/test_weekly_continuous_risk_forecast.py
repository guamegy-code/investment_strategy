import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from validation.weekly_continuous_risk_forecast import (  # noqa: E402
    MONTHLY_MODEL,
    WEEKLY_MODEL,
    _development_gate,
)


class WeeklyContinuousRiskForecastTests(unittest.TestCase):
    def test_weekly_gate_requires_risk_and_sharpe_improvement(self):
        metrics = pd.DataFrame([
            {
                "Strategy": MONTHLY_MODEL,
                "Period": "DEVELOPMENT_TO_2017",
                "CAGR": 0.10,
                "MDD": -0.30,
                "Sharpe": 0.60,
                "TransactionCosts": 0.01,
            },
            {
                "Strategy": WEEKLY_MODEL,
                "Period": "DEVELOPMENT_TO_2017",
                "CAGR": 0.099,
                "MDD": -0.25,
                "Sharpe": 0.65,
                "TransactionCosts": 0.02,
            },
        ])

        gate = _development_gate(metrics)

        self.assertTrue(bool(gate["Pass"].iloc[0]))
        self.assertGreater(gate["TransactionCostGapVsMonthly"].iloc[0], 0.0)

    def test_weekly_gate_rejects_worse_drawdown(self):
        metrics = pd.DataFrame([
            {
                "Strategy": MONTHLY_MODEL,
                "Period": "DEVELOPMENT_TO_2017",
                "CAGR": 0.10,
                "MDD": -0.30,
                "Sharpe": 0.60,
                "TransactionCosts": 0.01,
            },
            {
                "Strategy": WEEKLY_MODEL,
                "Period": "DEVELOPMENT_TO_2017",
                "CAGR": 0.11,
                "MDD": -0.31,
                "Sharpe": 0.65,
                "TransactionCosts": 0.02,
            },
        ])

        gate = _development_gate(metrics)

        self.assertFalse(bool(gate["Pass"].iloc[0]))


if __name__ == "__main__":
    unittest.main()

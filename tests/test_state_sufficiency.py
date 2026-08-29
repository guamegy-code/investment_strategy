import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "src" / "legacy-python"),
)

from validation.state_sufficiency import (  # noqa: E402
    CONTINUOUS_COLUMNS,
    MODEL_FEATURES,
    build_nonoverlap_sample,
    information_gate,
    _state_features,
    walk_forward_state_sufficiency,
)


class StateSufficiencyTests(unittest.TestCase):
    def test_bull_is_reference_state_and_other_states_are_one_hot(self):
        states = pd.Series(
            ["BULL", "CAUTION", "BEAR", "RECOVERY"],
            index=pd.date_range("2024-01-01", periods=4, freq="D"),
        )

        features = _state_features(states)

        self.assertEqual(features.iloc[0].sum(), 0.0)
        self.assertEqual(features.iloc[1]["State_CAUTION"], 1.0)
        self.assertEqual(features.iloc[2]["State_BEAR"], 1.0)
        self.assertEqual(features.iloc[3]["State_RECOVERY"], 1.0)

    def test_nonoverlap_sample_uses_disjoint_forward_return_windows(self):
        index = pd.date_range("2024-01-01", periods=12, freq="B")
        close = np.arange(100.0, 112.0)
        data = pd.DataFrame({
            "QQQ_Close": close,
            "QQQ_EMA55": close - 1.0,
            "QQQ_EMA200": close - 2.0,
            "QQQ_ROC20": np.linspace(-1.0, 1.0, len(index)),
            "QQQ_ROC60": np.linspace(-2.0, 2.0, len(index)),
            "QQQ_ROC120": np.linspace(-3.0, 3.0, len(index)),
            "QQQ_VOL60": 0.20,
            "QQQ_DRAWDOWN120": -0.02,
        }, index=index)
        schedule = {date: {"state": "BULL"} for date in index}

        sample = build_nonoverlap_sample(
            data, schedule, horizon_days=2, stride_days=2
        )

        self.assertTrue((sample["DecisionPosition"].diff().dropna() == 2).all())
        self.assertTrue(
            (sample["TargetEndDate"].to_numpy() == index[
                sample["DecisionPosition"].to_numpy() + 2
            ].to_numpy()).all()
        )
        self.assertAlmostEqual(sample.iloc[0]["ForwardReturn20D"], 0.02)

    def test_walk_forward_does_not_forecast_before_minimum_resolved_samples(self):
        index = pd.date_range("2020-01-01", periods=5, freq="20B")
        sample = pd.DataFrame({
            "ForwardReturn20D": [-0.04, 0.02, -0.03, 0.01, -0.02],
            "MaximumLoss20D": [0.08, 0.01, 0.06, 0.01, 0.05],
            "TailLoss5Pct": [1.0, 0.0, 1.0, 0.0, 1.0],
        }, index=index)
        for column in MODEL_FEATURES["STATE_ONLY"]:
            sample[column] = 0.0
        for number, column in enumerate(CONTINUOUS_COLUMNS):
            sample[column] = np.arange(len(sample), dtype=float) + number

        forecasts = walk_forward_state_sufficiency(sample, minimum_samples=2)

        self.assertEqual(list(forecasts.index), list(index[2:]))
        self.assertEqual(forecasts["ModelSamples"].tolist(), [2, 3, 4])
        self.assertTrue(forecasts[
            "STATE_PLUS_CONTINUOUS_TailProbability5Pct"
        ].between(0.0, 1.0).all())

    def test_information_gate_requires_both_eras(self):
        incremental = pd.DataFrame([
            {
                "Period": "DEVELOPMENT_PRE2021",
                "MaxLossRMSEImprovement": -0.001,
                "TailBrierImprovement": 0.001,
                "MaxLossTop20PctLiftGain": 0.1,
                "TailTop20PctLiftGain": 0.0,
            },
            {
                "Period": "RECENT_2021_PRESENT",
                "MaxLossRMSEImprovement": 0.001,
                "TailBrierImprovement": 0.001,
                "MaxLossTop20PctLiftGain": 0.1,
                "TailTop20PctLiftGain": 0.0,
            },
        ])

        gate, decision = information_gate(incremental)

        self.assertFalse(gate.iloc[0]["IncrementalInformationPass"])
        self.assertFalse(decision.iloc[0]["ContinuousInformationConfirmed"])


if __name__ == "__main__":
    unittest.main()

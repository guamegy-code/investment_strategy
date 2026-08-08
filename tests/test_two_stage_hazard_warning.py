import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from validation.two_stage_hazard_warning import (  # noqa: E402
    build_two_stage_predictions,
    confirmation_masks,
    select_development_filter,
)


class TwoStageHazardWarningTests(unittest.TestCase):
    def test_confirmation_masks_use_fixed_trend_and_volatility_signs(self):
        sample = pd.DataFrame({
            "QQQ_ROC20": [-1.0, 1.0, 1.0],
            "QQQ_Close": [101.0, 99.0, 101.0],
            "QQQ_EMA200": [100.0, 100.0, 100.0],
            "QQQ_VOL_ACCELERATION": [0.1, -0.1, 0.1],
        })

        masks = confirmation_masks(sample)

        self.assertEqual(masks["TREND_WEAK"].tolist(), [True, True, False])
        self.assertEqual(masks["VOL_ACCELERATING"].tolist(), [True, False, True])
        self.assertEqual(masks["TREND_AND_VOL"].tolist(), [True, False, False])

    def test_filter_is_applied_before_cooldown(self):
        index = pd.date_range("2020-01-03", periods=5, freq="W-FRI")
        sample = pd.DataFrame({
            "EventProbability": [0.9, 0.9, 0.1, 0.1, 0.1],
            "QQQ_ROC20": [1.0, -1.0, 1.0, 1.0, 1.0],
            "QQQ_Close": 101.0,
            "QQQ_EMA200": 100.0,
            "QQQ_VOL_ACCELERATION": 0.1,
        }, index=index)

        predictions = build_two_stage_predictions(
            sample, pd.Series(0.8, index=index)
        )

        self.assertFalse(predictions["TREND_WEAK"].iloc[0])
        self.assertTrue(predictions["TREND_WEAK"].iloc[1])

    def test_selection_requires_precision_gain_and_fewer_false_positives(self):
        metrics = pd.DataFrame({
            "Filter": ["NONE", "TREND_WEAK", "VOL_ACCELERATING"],
            "Period": ["DEVELOPMENT_TO_2017"] * 3,
            "Warnings": [8, 5, 5],
            "Precision": [0.25, 0.40, 0.30],
            "EpisodeRecall": [0.10, 0.10, 0.05],
            "FalsePositivesPerYear": [0.8, 0.5, 0.6],
        })

        selected = select_development_filter(metrics)

        row = selected.loc[selected["SelectedForEvaluation"]].iloc[0]
        self.assertEqual(row["Filter"], "TREND_WEAK")
        self.assertTrue(bool(row["DevelopmentPass"]))


if __name__ == "__main__":
    unittest.main()

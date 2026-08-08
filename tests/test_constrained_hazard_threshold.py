import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from validation.constrained_hazard_threshold import (  # noqa: E402
    build_candidate_thresholds,
    select_development_quantile,
)


class ConstrainedHazardThresholdTests(unittest.TestCase):
    def test_candidate_threshold_excludes_current_probability(self):
        probability = pd.Series(np.arange(265, dtype=float))

        thresholds = build_candidate_thresholds(probability, (0.50,))

        self.assertTrue(np.isnan(thresholds.iloc[259, 0]))
        self.assertEqual(thresholds.iloc[260, 0], 129.5)

    def test_selection_maximizes_episode_recall_with_precision_constraint(self):
        metrics = pd.DataFrame({
            "Threshold": ["Q50", "Q55", "Q60", "Q90"],
            "Quantile": [0.50, 0.55, 0.60, 0.90],
            "Period": ["DEVELOPMENT_TO_2017"] * 4,
            "Warnings": [10, 8, 7, 3],
            "Precision": [0.20, 0.25, 0.30, 1.00],
            "EpisodeRecall": [0.80, 0.60, 0.60, 0.20],
            "FalsePositivesPerYear": [1.0, 0.8, 0.4, 0.0],
        })

        selected = select_development_quantile(metrics)

        self.assertEqual(selected.loc[selected["Selected"], "Threshold"].iloc[0], "Q60")
        self.assertFalse(bool(selected.loc[selected["Threshold"] == "Q90", "Eligible"].iloc[0]))


if __name__ == "__main__":
    unittest.main()

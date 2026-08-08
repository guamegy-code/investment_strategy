import sys
import unittest
from pathlib import Path

import numpy as np


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from validation.probability_accuracy import classification_metrics  # noqa: E402


class ProbabilityAccuracyTests(unittest.TestCase):
    def test_perfect_predictions_have_perfect_classification_scores(self):
        metrics = classification_metrics(
            np.array([0, 0, 1, 1]),
            np.array([0.1, 0.2, 0.8, 0.9]),
            0.5,
        )

        self.assertEqual(metrics["Accuracy"], 1.0)
        self.assertEqual(metrics["BalancedAccuracy"], 1.0)
        self.assertEqual(metrics["Precision"], 1.0)
        self.assertEqual(metrics["Recall"], 1.0)
        self.assertEqual(metrics["ROC_AUC"], 1.0)
        self.assertEqual(metrics["PR_AUC"], 1.0)

    def test_imbalanced_always_negative_prediction_has_half_balanced_accuracy(self):
        metrics = classification_metrics(
            np.array([0] * 9 + [1]),
            np.array([0.1] * 10),
            0.5,
        )

        self.assertEqual(metrics["Accuracy"], 0.9)
        self.assertEqual(metrics["BalancedAccuracy"], 0.5)
        self.assertEqual(metrics["Recall"], 0.0)
        self.assertTrue(np.isnan(metrics["Precision"]))

    def test_row_specific_threshold_matches_tail_warning_rule(self):
        metrics = classification_metrics(
            np.array([0, 1, 1]),
            np.array([0.14, 0.21, 0.30]),
            np.array([0.15, 0.20, 0.35]),
        )

        self.assertEqual(metrics["TP"], 1)
        self.assertEqual(metrics["FN"], 1)
        self.assertEqual(metrics["FP"], 0)


if __name__ == "__main__":
    unittest.main()

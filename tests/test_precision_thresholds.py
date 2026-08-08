import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from validation.precision_thresholds import (  # noqa: E402
    build_causal_thresholds,
    expanding_quantile,
)


class PrecisionThresholdTests(unittest.TestCase):
    def test_expanding_quantile_excludes_current_probability(self):
        values = pd.Series([0.1, 0.2, 0.3, 0.9])

        threshold = expanding_quantile(values, 0.5, minimum_history=3)

        self.assertTrue(np.isnan(threshold.iloc[2]))
        self.assertEqual(threshold.iloc[3], 0.2)

    def test_hybrid_threshold_is_never_below_either_component(self):
        index = pd.date_range("2020-01-01", periods=65, freq="MS")
        sample = pd.DataFrame({
            "ProbabilityLoss21": np.linspace(0.05, 0.40, len(index)),
            "BaseLossProbability21": 0.10,
        }, index=index)

        thresholds = build_causal_thresholds(sample)

        self.assertTrue(
            (thresholds["HYBRID_BASE_Q90"] >= thresholds["BASE_PLUS_3PP"]).all()
        )
        self.assertTrue(
            (thresholds["HYBRID_BASE_Q90"] >= thresholds["EXPANDING_Q90"]).all()
        )


if __name__ == "__main__":
    unittest.main()

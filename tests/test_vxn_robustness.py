import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "src" / "legacy-python"),
)

from validation.vxn_robustness import (  # noqa: E402
    block_max_reality_check,
    discover_drawdown_episodes,
    fit_logistic,
    paired_block_bootstrap,
    threshold_definition,
)
from validation.vxn_state_gates import _find_rule  # noqa: E402


class VxnRobustnessTests(unittest.TestCase):
    def test_threshold_definition_is_limited_to_bull_caution(self):
        definition = threshold_definition(2.5)
        entry = _find_rule(definition, "BULL", "CAUTION")
        recovery = _find_rule(definition, "BEAR", "RECOVERY")

        self.assertIn("VXN.roc5 >= 2.5", entry["when"])
        self.assertNotIn("VXN", recovery["when"])

    def test_lagged_definition_uses_prior_day_feature(self):
        definition = threshold_definition(5, lagged=True)
        entry = _find_rule(definition, "BULL", "CAUTION")

        self.assertIn("VXN.roc5_lag1 >= 5", entry["when"])

    def test_drawdown_episode_runs_from_peak_to_recovery(self):
        index = pd.bdate_range("2023-01-02", periods=7)
        prices = pd.Series([100, 105, 100, 93, 90, 104, 106], index=index)

        episodes = discover_drawdown_episodes(prices, threshold=-0.10)

        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes.iloc[0]["PeakDate"], index[1])
        self.assertEqual(episodes.iloc[0]["TroughDate"], index[4])
        self.assertEqual(episodes.iloc[0]["EndDate"], index[6])
        self.assertTrue(episodes.iloc[0]["Recovered"])

    def test_numpy_logistic_model_learns_direction(self):
        values = np.array([[-2], [-1], [-0.5], [0.5], [1], [2]], dtype=float)
        labels = np.array([0, 0, 0, 1, 1, 1], dtype=float)

        model = fit_logistic(values, labels, l2=0.1)
        probabilities = model.predict(np.array([[-1.5], [1.5]]))

        self.assertLess(probabilities[0], 0.5)
        self.assertGreater(probabilities[1], 0.5)

    def test_paired_bootstrap_is_zero_for_identical_returns(self):
        returns = pd.Series(
            np.tile([0.01, -0.005, 0.002], 20),
            index=pd.bdate_range("2023-01-02", periods=60),
        )

        result = paired_block_bootstrap(
            returns,
            returns,
            samples=20,
            block_length=5,
            seed=1,
        )

        self.assertAlmostEqual(result["CAGRGapMean"], 0.0)
        self.assertAlmostEqual(result["MDDImprovementMean"], 0.0)

    def test_reality_check_returns_one_for_zero_excess(self):
        excess = pd.DataFrame(
            np.zeros((60, 3)),
            columns=["a", "b", "c"],
        )

        result = block_max_reality_check(
            excess,
            samples=20,
            block_length=5,
            seed=1,
        )

        self.assertEqual(result["RealityCheckPValue"], 1.0)


if __name__ == "__main__":
    unittest.main()

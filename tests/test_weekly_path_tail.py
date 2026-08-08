import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from validation.weekly_path_tail import (  # noqa: E402
    episode_labels,
    suppress_overlapping_warnings,
)


class WeeklyPathTailTests(unittest.TestCase):
    def test_four_week_cooldown_suppresses_three_following_decisions(self):
        warning = pd.Series([True, True, True, True, True])

        accepted = suppress_overlapping_warnings(warning, cooldown_decisions=3)

        self.assertEqual(accepted.tolist(), [True, False, False, False, True])

    def test_consecutive_positive_labels_form_one_episode(self):
        outcome = pd.Series([False, True, True, False, True, False])

        labels = episode_labels(outcome)

        self.assertEqual(labels.tolist(), [0, 1, 1, 0, 2, 0])


if __name__ == "__main__":
    unittest.main()

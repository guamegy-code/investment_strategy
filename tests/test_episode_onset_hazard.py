import sys
import unittest
from pathlib import Path

import numpy as np


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from validation.episode_onset_hazard import (  # noqa: E402
    episode_onset_labels,
    onset_training_targets,
)


class EpisodeOnsetHazardTests(unittest.TestCase):
    def test_only_first_observation_of_each_episode_is_onset(self):
        event = np.array([False, True, True, False, True, True, True, False])

        onset, ongoing = episode_onset_labels(event)

        np.testing.assert_array_equal(
            onset, np.array([False, True, False, False, True, False, False, False])
        )
        np.testing.assert_array_equal(
            ongoing, np.array([False, False, True, False, False, True, True, False])
        )

    def test_training_censors_ongoing_events_and_keeps_onset_breach_day(self):
        breach = np.array([0, 12, 7, 0, 4, 2, 0])

        eligible, targets = onset_training_targets(breach)

        np.testing.assert_array_equal(
            eligible, np.array([True, True, False, True, True, False, True])
        )
        np.testing.assert_array_equal(targets, np.array([0, 12, 0, 4, 0]))


if __name__ == "__main__":
    unittest.main()

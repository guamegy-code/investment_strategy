import sys
import unittest
from pathlib import Path

import numpy as np


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from validation.path_tail_targets import forward_path_outcomes  # noqa: E402


class PathTailTargetTests(unittest.TestCase):
    def test_path_event_detects_intermediate_loss_despite_recovery(self):
        close = np.array([100.0, 94.0, 102.0, 103.0])

        outcome, lead = forward_path_outcomes(close, [0], 3, -0.05)

        self.assertEqual(outcome[0], 1)
        self.assertEqual(lead[0], 1.0)

    def test_path_event_reports_no_breach(self):
        close = np.array([100.0, 98.0, 99.0, 101.0])

        outcome, lead = forward_path_outcomes(close, [0], 3, -0.05)

        self.assertEqual(outcome[0], 0)
        self.assertTrue(np.isnan(lead[0]))


if __name__ == "__main__":
    unittest.main()

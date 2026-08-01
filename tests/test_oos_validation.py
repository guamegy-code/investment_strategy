import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from oos_validation import (
    _future_with_anchor,
    current_parameters,
    load_lock,
    verify_lock,
)


class OOSValidationTests(unittest.TestCase):
    def test_locked_parameters_match_selected_strategy(self):
        lock = load_lock()
        self.assertEqual(current_parameters(), lock["parameters"])
        self.assertEqual(verify_lock()["status"], "LOCKED")

    def test_future_slice_keeps_one_pre_cutoff_anchor(self):
        dates = pd.to_datetime(["2026-07-31", "2026-08-03", "2026-08-04"])
        history = pd.DataFrame({"Portfolio": [1.0, 1.1, 1.2]}, index=dates)
        anchored, future = _future_with_anchor(
            history, pd.Timestamp("2026-07-31")
        )
        self.assertEqual(len(future), 2)
        self.assertEqual(len(anchored), 3)
        self.assertEqual(anchored.index[0], dates[0])


if __name__ == "__main__":
    unittest.main()

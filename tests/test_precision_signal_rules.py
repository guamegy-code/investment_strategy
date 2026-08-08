import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from validation.precision_signal_rules import suppress_next_decision  # noqa: E402


class PrecisionSignalRuleTests(unittest.TestCase):
    def test_next_month_warning_is_suppressed(self):
        warning = pd.Series([True, True, True, False, True])

        accepted = suppress_next_decision(warning)

        self.assertEqual(accepted.tolist(), [True, False, True, False, True])

    def test_nonconsecutive_warnings_are_preserved(self):
        warning = pd.Series([True, False, True, False])

        accepted = suppress_next_decision(warning)

        self.assertEqual(accepted.tolist(), [True, False, True, False])


if __name__ == "__main__":
    unittest.main()

import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "src" / "legacy-python"),
)

from validation.breadth_200ma_gates import (  # noqa: E402
    METHODOLOGY_VERSION,
    PROFILES,
    _find_rule,
    add_breadth_features,
    candidate_definition,
    import_stockcharts_export,
    validate_coverage,
)


class Breadth200MAGateTests(unittest.TestCase):
    @staticmethod
    def profile(name):
        return next(profile for profile in PROFILES if profile.name == name)

    def test_stockcharts_import_uses_point_in_time_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "export.csv"
            output = Path(directory) / "normalized.csv"
            pd.DataFrame({
                "Date": ["2023-01-03", "2023-01-04"],
                "Close": [40.0, 41.0],
            }).to_csv(source, index=False)

            result = import_stockcharts_export(source, output)

            self.assertEqual(len(result), 2)
            self.assertEqual(
                set(result["MethodologyVersion"]),
                {METHODOLOGY_VERSION},
            )
            self.assertTrue(
                result["ObservationDate"].equals(result["AvailableDate"])
            )

    def test_breadth_change_is_causal(self):
        index = pd.bdate_range("2023-01-02", periods=22)
        close = pd.Series(range(22), index=index, dtype=float)
        frame = pd.DataFrame({"Close": close})

        result = add_breadth_features(frame)

        self.assertEqual(result["CHANGE20"].iloc[20], 20.0)
        self.assertEqual(
            result["CHANGE20_LAG1"].iloc[21],
            result["CHANGE20"].iloc[20],
        )

    def test_candidate_changes_only_bull_to_caution(self):
        baseline = candidate_definition(self.profile("BASELINE_ALIGNED"))
        definition = candidate_definition(
            self.profile("BREADTH_LEVEL_LT40")
        )
        entry = _find_rule(definition, "BULL", "CAUTION")
        recovery = _find_rule(definition, "BEAR", "RECOVERY")
        baseline_recovery = _find_rule(baseline, "BEAR", "RECOVERY")

        self.assertIn("BREADTH200.close < 40", entry["when"])
        self.assertEqual(recovery, baseline_recovery)

    def test_coverage_rejects_short_history(self):
        signal = pd.DataFrame({
            "ObservationDate": pd.to_datetime(["2023-01-03", "2023-01-04"]),
            "AvailableDate": pd.to_datetime(["2023-01-03", "2023-01-04"]),
            "Value": [40.0, 41.0],
            "MethodologyVersion": METHODOLOGY_VERSION,
        })
        calendar = pd.bdate_range("2022-01-03", "2023-01-10")

        with self.assertRaisesRegex(ValueError, "history begins"):
            validate_coverage(signal, calendar)


if __name__ == "__main__":
    unittest.main()

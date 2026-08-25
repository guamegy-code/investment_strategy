import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "src" / "legacy-python"),
)

from validation.cnn_official_recovery_gates import (  # noqa: E402
    PROFILES,
    _find_recovery_rule,
    add_cnn_features,
    candidate_definition,
    fear_episodes,
)


class CnnOfficialRecoveryGateTests(unittest.TestCase):
    @staticmethod
    def profile(name):
        return next(profile for profile in PROFILES if profile.name == name)

    def test_features_use_available_date_and_only_prior_values(self):
        signal = pd.DataFrame({
            "ObservationDate": pd.date_range("2024-01-01", periods=6),
            "AvailableDate": pd.date_range("2024-01-02", periods=6),
            "Value": [30, 20, 10, 15, 25, 35],
        })

        result = add_cnn_features(signal)

        self.assertEqual(result.index.min(), pd.Timestamp("2024-01-02"))
        self.assertEqual(result["MIN10"].iloc[-1], 10)
        self.assertEqual(result["DELTA5"].iloc[-1], 5)

    def test_candidate_changes_only_bear_recovery(self):
        baseline = candidate_definition(self.profile("BASELINE"))
        candidate = candidate_definition(self.profile("CNN_TURN_20_SCORE2"))

        baseline_rule = _find_recovery_rule(baseline)
        candidate_rule = _find_recovery_rule(candidate)
        self.assertEqual(baseline_rule["confirm"], candidate_rule["confirm"])
        self.assertIn("variables.recovery_score >= 2", candidate_rule["when"])
        self.assertIn("CNNFG.min10 <= 20", candidate_rule["when"])
        self.assertIn("CNNFG.delta5 >= 5", candidate_rule["when"])

        baseline_rules = baseline["state"]["market_mode"]["rules"]
        candidate_rules = candidate["state"]["market_mode"]["rules"]
        changed = [
            index for index, pair in enumerate(zip(baseline_rules, candidate_rules))
            if pair[0] != pair[1]
        ]
        self.assertEqual(changed, [baseline_rules.index(baseline_rule)])

    def test_fear_days_are_collapsed_into_episodes(self):
        signal = pd.DataFrame({
            "ObservationDate": pd.to_datetime([
                "2024-01-01", "2024-01-02", "2024-01-20", "2024-01-21"
            ]),
            "Value": [15, 18, 10, 25],
        })

        result = fear_episodes(signal, max_gap_days=10)

        self.assertEqual(len(result), 2)
        self.assertEqual(result.iloc[0]["ExtremeDays"], 2)
        self.assertEqual(result.iloc[1]["Min"], 10)


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path


sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "src" / "legacy-python"),
)

from validation.extreme_fear_recovery import (  # noqa: E402
    PROFILES,
    _find_recovery_rule,
    candidate_definition,
)


class ExtremeFearRecoveryTests(unittest.TestCase):
    @staticmethod
    def profile(name):
        return next(profile for profile in PROFILES if profile.name == name)

    def test_candidate_preserves_baseline_and_adds_only_early_recovery(self):
        baseline = candidate_definition(self.profile("BASELINE"))
        candidate = candidate_definition(self.profile("FEAR_SCORE2_MEMORY20"))
        baseline_rule = _find_recovery_rule(baseline)
        candidate_rule = _find_recovery_rule(candidate)

        self.assertIn("variables.recovery_score >= 3", candidate_rule["when"])
        self.assertIn("variables.recovery_score >= 2", candidate_rule["when"])
        self.assertIn("FEAR.recent_score20 >= 2", candidate_rule["when"])
        self.assertEqual(baseline_rule["confirm"], candidate_rule["confirm"])

        baseline_rules = baseline["state"]["market_mode"]["rules"]
        candidate_rules = candidate["state"]["market_mode"]["rules"]
        changed = [
            index for index, pair in enumerate(zip(baseline_rules, candidate_rules))
            if pair[0] != pair[1]
        ]
        self.assertEqual(changed, [baseline_rules.index(baseline_rule)])


if __name__ == "__main__":
    unittest.main()

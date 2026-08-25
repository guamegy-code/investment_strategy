import sys
import unittest
from pathlib import Path


sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "src" / "legacy-python"),
)

from validation.recovery_risk_tiers import (  # noqa: E402
    PROFILES,
    candidate_definition,
)


class RecoveryRiskTierTests(unittest.TestCase):
    @staticmethod
    def profile(name):
        return next(profile for profile in PROFILES if profile.name == name)

    def test_candidate_does_not_change_market_transition_rules(self):
        baseline = candidate_definition(self.profile("BASELINE"))
        candidate = candidate_definition(
            self.profile("RECOVERY25_SCORE2_MEMORY20")
        )

        self.assertEqual(
            baseline["state"]["market_mode"],
            candidate["state"]["market_mode"],
        )

    def test_candidate_inserts_stressed_recovery_tier_before_default(self):
        candidate = candidate_definition(
            self.profile("RECOVERY25_SCORE2_MEMORY20")
        )
        rules = candidate["state"]["risk_weight"]["rules"]
        stressed = next(
            rule for rule in rules if "FEAR.recent_score20" in rule.get("when", "")
        )
        stressed_index = rules.index(stressed)

        self.assertEqual(stressed["set"], "25%")
        self.assertEqual(rules[stressed_index + 1]["set"], "50%")
        self.assertTrue(any(
            "changed(state.risk_weight)" in rule.get("when", "")
            for rule in candidate["rebalance"]
        ))


if __name__ == "__main__":
    unittest.main()

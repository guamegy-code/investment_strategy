import sys
import unittest
from pathlib import Path


sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "src" / "legacy-python"),
)

from validation.credit_entry_gates import (  # noqa: E402
    PROFILES,
    _find_rule,
    _rules,
    candidate_definition,
)
from strategy_dsl import load_strategy_definition  # noqa: E402


class CreditEntryGateTests(unittest.TestCase):
    @staticmethod
    def profile(name):
        return next(profile for profile in PROFILES if profile.name == name)

    def test_baseline_loads_credit_assets_without_changing_rules(self):
        source = load_strategy_definition(
            Path(__file__).resolve().parents[1]
            / "strategies"
            / "14_profit_band_tdf2050_gate_spy_tdf100_no_bnd.yaml"
        )
        definition = candidate_definition(self.profile("BASELINE_ALIGNED"))

        self.assertEqual(_rules(definition), _rules(source))
        self.assertIn("HYG", definition["assets"]["observations"])
        self.assertIn("LQD", definition["assets"]["observations"])

    def test_or_profile_changes_only_bull_to_caution(self):
        baseline = candidate_definition(self.profile("BASELINE_ALIGNED"))
        definition = candidate_definition(
            self.profile("CREDIT_OR_REL20_GAP1")
        )
        changed = [
            index
            for index, (before, after) in enumerate(
                zip(_rules(baseline), _rules(definition))
            )
            if before != after
        ]
        entry = _find_rule(definition, "BULL", "CAUTION")

        self.assertEqual(changed, [_rules(definition).index(entry)])
        self.assertIn("or", entry["when"])
        self.assertIn("HYG.roc20 < LQD.roc20 - 1", entry["when"])

    def test_confirmation_profile_keeps_other_transitions_unchanged(self):
        baseline = candidate_definition(self.profile("BASELINE_ALIGNED"))
        definition = candidate_definition(
            self.profile("CREDIT_CONFIRM_REL20_GAP1")
        )
        entry = _find_rule(definition, "BULL", "CAUTION")
        recovery = _find_rule(definition, "BEAR", "RECOVERY")
        baseline_recovery = _find_rule(baseline, "BEAR", "RECOVERY")

        self.assertIn("HYG.close < HYG.ema20", entry["when"])
        self.assertEqual(recovery, baseline_recovery)


if __name__ == "__main__":
    unittest.main()

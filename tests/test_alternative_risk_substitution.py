import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy import AllocationState  # noqa: E402
from validation.alternative_risk_substitution import (  # noqa: E402
    AlternativeProfile,
    AlternativeRiskSubstitutionStrategy,
)


class AlternativeRiskSubstitutionTests(unittest.TestCase):
    @staticmethod
    def strategy(candidate, replaced=("BND", "BIL")):
        profile = AlternativeProfile(
            "TEST", candidate, frozenset(replaced)
        )
        return AlternativeRiskSubstitutionStrategy(profile)

    def test_recovery_caps_combined_risk_assets_at_70_percent(self):
        strategy = self.strategy("VTV")
        strategy.state = AllocationState.RECOVERY
        strategy.safe_asset = "BND"

        target = strategy._target_for_state()

        self.assertEqual(target["QQQ"], 0.50)
        self.assertEqual(target["VTV"], 0.20)
        self.assertEqual(target["BND"], 0.30)
        self.assertLessEqual(target["QQQ"] + target["VTV"], 0.70)
        self.assertAlmostEqual(sum(target.values()), 1.0)

    def test_bear_keeps_30_percent_in_safe_asset(self):
        strategy = self.strategy("SPLV")
        strategy.state = AllocationState.BEAR
        strategy.safe_asset = "BIL"

        target = strategy._target_for_state()

        self.assertEqual(target["SPLV"], 0.70)
        self.assertEqual(target["BIL"], 0.30)
        self.assertAlmostEqual(sum(target.values()), 1.0)

    def test_candidate_is_zero_when_qqq_is_already_70_percent(self):
        strategy = self.strategy("VXUS")
        strategy.state = AllocationState.BULL
        strategy.safe_asset = "BND"

        target = strategy._target_for_state()

        self.assertEqual(target["QQQ"], 0.70)
        self.assertEqual(target["VXUS"], 0.0)
        self.assertEqual(target["BND"], 0.30)

    def test_non_selected_safe_asset_is_not_replaced(self):
        strategy = self.strategy("VIG", replaced=("BND",))
        strategy.state = AllocationState.RECOVERY
        strategy.safe_asset = "BIL"

        target = strategy._target_for_state()

        self.assertEqual(target["VIG"], 0.0)
        self.assertEqual(target["BIL"], 0.50)


if __name__ == "__main__":
    unittest.main()

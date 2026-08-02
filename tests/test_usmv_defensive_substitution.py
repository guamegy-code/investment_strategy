import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy import AllocationState  # noqa: E402
from validation.usmv_defensive_substitution import (  # noqa: E402
    PROFILES,
    USMVDefensiveSubstitutionStrategy,
)


class USMVDefensiveSubstitutionTests(unittest.TestCase):
    @staticmethod
    def strategy(name):
        profile = next(profile for profile in PROFILES if profile.name == name)
        return USMVDefensiveSubstitutionStrategy(profile)

    def test_bnd_is_replaced_during_recovery(self):
        strategy = self.strategy("USMV_INSTEAD_OF_BND")
        strategy.state = AllocationState.RECOVERY
        strategy.safe_asset = "BND"

        target = strategy._target_for_state()

        self.assertEqual(target["QQQ"], 0.50)
        self.assertEqual(target["USMV"], 0.20)
        self.assertEqual(target["BND"], 0.30)
        self.assertLessEqual(target["QQQ"] + target["USMV"], 0.70)

    def test_bil_profile_does_not_replace_selected_bnd(self):
        strategy = self.strategy("USMV_INSTEAD_OF_BIL")
        strategy.state = AllocationState.RECOVERY
        strategy.safe_asset = "BND"

        target = strategy._target_for_state()

        self.assertEqual(target["BND"], 0.50)
        self.assertEqual(target["USMV"], 0.0)

    def test_usmv_is_not_used_while_qqq_is_at_70_percent(self):
        strategy = self.strategy("USMV_INSTEAD_OF_BND_OR_BIL")
        strategy.state = AllocationState.BULL
        strategy.safe_asset = "BIL"

        target = strategy._target_for_state()

        self.assertEqual(target["QQQ"], 0.70)
        self.assertEqual(target["BIL"], 0.30)
        self.assertEqual(target["USMV"], 0.0)

    def test_all_safe_profile_caps_usmv_at_70_percent_in_bear(self):
        strategy = self.strategy("USMV_INSTEAD_OF_BND_OR_BIL")
        strategy.state = AllocationState.BEAR
        strategy.safe_asset = "BIL"

        target = strategy._target_for_state()

        self.assertEqual(target["QQQ"], 0.0)
        self.assertEqual(target["USMV"], 0.70)
        self.assertEqual(target["BIL"], 0.30)
        self.assertLessEqual(target["QQQ"] + target["USMV"], 0.70)
        self.assertAlmostEqual(sum(target.values()), 1.0)


if __name__ == "__main__":
    unittest.main()

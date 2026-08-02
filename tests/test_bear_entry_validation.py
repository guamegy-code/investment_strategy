import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bear_entry_validation import (  # noqa: E402
    BearEntryCandidateStrategy,
    PROFILES,
)
from strategy import AllocationState  # noqa: E402


def qqq_market(drawdown=-0.10, roc20=-6.0, rsi=40.0):
    return {
        "Close": 85.0,
        "EMA20": 90.0,
        "EMA55": 95.0,
        "EMA200": 100.0,
        "ROC5": -2.0,
        "ROC20": roc20,
        "ROC60": -8.0,
        "EMA20_SLOPE5": -1.0,
        "EMA200_SLOPE20": 0.2,
        "DRAWDOWN120": drawdown,
        "RSI14": rsi,
    }


class BearEntryValidationTests(unittest.TestCase):
    @staticmethod
    def strategy(profile_name):
        profile = next(profile for profile in PROFILES if profile.name == profile_name)
        strategy = BearEntryCandidateStrategy(profile)
        strategy.risk_off_score, _ = strategy._scores(qqq_market())
        return strategy

    def test_early_rule_does_not_require_falling_ema200(self):
        strategy = self.strategy("EARLY_TREND")

        self.assertTrue(strategy._is_structural_bear(qqq_market()))

    def test_long_trend_rule_keeps_falling_ema200_requirement(self):
        strategy = self.strategy("EARLY_LONG_TREND")

        self.assertFalse(strategy._is_structural_bear(qqq_market()))
        falling_long_trend = qqq_market()
        falling_long_trend["EMA200_SLOPE20"] = -0.1
        self.assertTrue(strategy._is_structural_bear(falling_long_trend))

    def test_fast_baseline_changes_confirmation_not_entry_rule(self):
        baseline = self.strategy("BASELINE")
        fast = self.strategy("BASELINE_FAST_CONFIRMATION")

        self.assertEqual(
            baseline._is_structural_bear(qqq_market()),
            fast._is_structural_bear(qqq_market()),
        )
        self.assertEqual(baseline.BEAR_CONFIRMATION_DAYS, 10)
        self.assertEqual(fast.BEAR_CONFIRMATION_DAYS, 5)

    def test_baseline_recovery_variants_keep_original_entry_rule(self):
        baseline = self.strategy("BASELINE")
        minimum_hold = self.strategy("BASELINE_MIN_HOLD_20")
        strict = self.strategy("BASELINE_STRICT_RECOVERY")

        for candidate in (minimum_hold, strict):
            self.assertEqual(
                baseline._is_structural_bear(qqq_market()),
                candidate._is_structural_bear(qqq_market()),
            )
            self.assertEqual(candidate.BEAR_CONFIRMATION_DAYS, 10)

    def test_acceleration_rule_requires_meaningful_20_day_decline(self):
        strategy = self.strategy("EARLY_ACCELERATION")

        self.assertFalse(
            strategy._is_structural_bear(qqq_market(roc20=-3.0))
        )
        self.assertTrue(strategy._is_structural_bear(qqq_market(roc20=-6.0)))

    def test_exhaustion_filter_blocks_only_deep_oversold_entry(self):
        strategy = self.strategy("EARLY_ACCELERATION_EXHAUSTION_FILTER")

        self.assertFalse(
            strategy._is_structural_bear(
                qqq_market(drawdown=-0.20, roc20=-8.0, rsi=25.0)
            )
        )
        self.assertTrue(
            strategy._is_structural_bear(
                qqq_market(drawdown=-0.15, roc20=-8.0, rsi=25.0)
            )
        )

    def test_minimum_hold_blocks_early_recovery(self):
        strategy = self.strategy("EARLY_ACCELERATION_MIN_HOLD_20")
        strategy.state = AllocationState.BEAR
        strategy._bear_state_days = 5
        recovery = qqq_market(roc20=2.0)
        recovery.update({
            "Close": 101.0,
            "EMA20": 100.0,
            "EMA55": 102.0,
            "ROC5": 2.0,
            "EMA20_SLOPE5": 1.0,
        })

        self.assertEqual(
            strategy._desired_state(recovery), AllocationState.BEAR
        )

    def test_strict_recovery_requires_price_and_roc20_confirmation(self):
        strategy = self.strategy("EARLY_ACCELERATION_STRICT_RECOVERY")
        strategy.state = AllocationState.BEAR
        weak_recovery = qqq_market(roc20=-1.0)
        weak_recovery.update({
            "Close": 101.0,
            "EMA20": 100.0,
            "EMA55": 102.0,
            "ROC5": 2.0,
            "EMA20_SLOPE5": 1.0,
        })
        confirmed_recovery = weak_recovery.copy()
        confirmed_recovery["ROC20"] = 2.0

        self.assertEqual(
            strategy._desired_state(weak_recovery), AllocationState.BEAR
        )
        self.assertEqual(
            strategy._desired_state(confirmed_recovery),
            AllocationState.RECOVERY,
        )

    def test_staged_bear_uses_partial_risk_until_baseline_confirms(self):
        strategy = self.strategy("EARLY_ACCELERATION_STAGED")
        strategy.state = AllocationState.BEAR
        strategy.safe_asset = "BND"

        early_target = strategy._target_for_state()
        strategy._baseline_bear_active = True
        confirmed_target = strategy._target_for_state()

        self.assertEqual(early_target["QQQ"], 0.35)
        self.assertEqual(confirmed_target["QQQ"], 0.0)


if __name__ == "__main__":
    unittest.main()

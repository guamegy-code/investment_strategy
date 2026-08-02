import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from shadow_oos_validation import (  # noqa: E402
    StrictRecoveryShadowStrategy,
    current_shadow_parameters,
    load_shadow_lock,
    verify_shadow_lock,
)
from strategy import AllocationState  # noqa: E402


def recovery_market(roc20):
    return {
        "Close": 101.0,
        "EMA20": 100.0,
        "EMA55": 102.0,
        "EMA200": 105.0,
        "ROC5": 2.0,
        "ROC20": roc20,
        "ROC60": -2.0,
        "EMA20_SLOPE5": 1.0,
        "EMA200_SLOPE20": -1.0,
        "DRAWDOWN120": -0.12,
    }


class ShadowOOSValidationTests(unittest.TestCase):
    def test_shadow_requires_strict_recovery_confirmation(self):
        strategy = StrictRecoveryShadowStrategy()
        strategy.state = AllocationState.BEAR

        self.assertEqual(
            strategy._desired_state(recovery_market(-1.0)),
            AllocationState.BEAR,
        )
        self.assertEqual(
            strategy._desired_state(recovery_market(2.0)),
            AllocationState.RECOVERY,
        )

    def test_locked_shadow_parameters_match_selected_candidate(self):
        self.assertEqual(
            current_shadow_parameters(), load_shadow_lock()["parameters"]
        )
        self.assertEqual(verify_shadow_lock()["status"], "LOCKED_SHADOW")


if __name__ == "__main__":
    unittest.main()

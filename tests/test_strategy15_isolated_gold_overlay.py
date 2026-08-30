import sys
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "legacy-python"))

from validation.strategy15_isolated_gold_overlay import (  # noqa: E402
    conditional_gld_signal,
    _target_with_overlay,
    _transfer_current_weights,
)
from validation.strategy15_isolated_gold_oos import verify_lock  # noqa: E402


class IsolatedGoldOverlayTests(unittest.TestCase):
    def test_oos_lock_matches_frozen_candidate(self):
        lock = verify_lock()
        self.assertEqual(lock["selected_candidate"], "CONDITIONAL_GLD_5")
        self.assertEqual(lock["oos_start"], "2026-08-03")

    def test_relative_strength_signal_requires_trend_and_two_horizons(self):
        market = {
            "GLD": {
                "Close": 110.0,
                "EMA200": 100.0,
                "ROC60": 10.0,
                "ROC120": 7.0,
                "ROC252": -5.0,
            },
            "QQQ": {"ROC60": 8.0, "ROC120": 6.0, "ROC252": 0.0},
        }
        self.assertTrue(conditional_gld_signal(market))
        market["GLD"]["ROC120"] = 5.0
        self.assertFalse(conditional_gld_signal(market))

    def test_overlay_target_changes_only_qqq_and_gld(self):
        target = _target_with_overlay(
            {"QQQ": 0.70, "TDF2050_PROXY": 0.30, "BIL": 0.0},
            active=True,
            weight=0.05,
        )
        self.assertEqual(target, {
            "QQQ": 0.65,
            "TDF2050_PROXY": 0.30,
            "BIL": 0.0,
            "GLD": 0.05,
        })

    def test_signal_only_transition_preserves_non_overlay_sleeves(self):
        current = {
            "QQQ": 0.77,
            "TDF2050_PROXY": 0.22,
            "BIL": 0.01,
        }
        entered = _transfer_current_weights(current, to_gld=True, weight=0.05)
        self.assertAlmostEqual(entered["QQQ"], 0.72)
        self.assertAlmostEqual(entered["TDF2050_PROXY"], 0.22)
        self.assertAlmostEqual(entered["BIL"], 0.01)
        self.assertAlmostEqual(entered["GLD"], 0.05)

        exited = _transfer_current_weights(entered, to_gld=False, weight=0.05)
        self.assertAlmostEqual(exited["QQQ"], 0.77)
        self.assertAlmostEqual(exited["TDF2050_PROXY"], 0.22)
        self.assertAlmostEqual(exited["BIL"], 0.01)
        self.assertAlmostEqual(exited["GLD"], 0.0)


if __name__ == "__main__":
    unittest.main()

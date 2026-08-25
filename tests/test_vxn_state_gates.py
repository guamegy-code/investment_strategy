import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "src" / "legacy-python"),
)

from validation.vxn_state_gates import (  # noqa: E402
    PROFILES,
    _find_rule,
    add_vxn_features,
    candidate_definition,
)


class VxnStateGateTests(unittest.TestCase):
    @staticmethod
    def profile(name):
        return next(profile for profile in PROFILES if profile.name == name)

    def test_percentile_rank_is_causal(self):
        index = pd.bdate_range("2020-01-01", periods=253)
        close = np.arange(1.0, 254.0)
        frame = pd.DataFrame({
            "Open": close,
            "High": close,
            "Low": close,
            "Close": close,
            "Volume": 0.0,
        }, index=index)

        features = add_vxn_features(frame)

        self.assertAlmostEqual(features["PCT_RANK252"].iloc[251], 1.0)
        self.assertAlmostEqual(features["PCT_RANK252"].iloc[252], 1.0)
        self.assertEqual(
            features["ROC5_LAG1"].iloc[20],
            features["ROC5"].iloc[19],
        )

    def test_entry_profile_changes_only_bull_to_caution_rule(self):
        definition = candidate_definition(self.profile("VXN_ENTRY_RANK70"))
        entry = _find_rule(definition, "BULL", "CAUTION")
        recovery = _find_rule(definition, "BEAR", "RECOVERY")

        self.assertIn("VXN", definition["assets"]["observations"])
        self.assertIn("VXN.pct_rank252 >= 0.70", entry["when"])
        self.assertNotIn("VXN", recovery["when"])

    def test_recovery_profile_changes_only_bear_to_recovery_rule(self):
        definition = candidate_definition(
            self.profile("VXN_BEAR_RECOVERY_FALLING")
        )
        entry = _find_rule(definition, "BULL", "CAUTION")
        recovery = _find_rule(definition, "BEAR", "RECOVERY")

        self.assertNotIn("VXN", entry["when"])
        self.assertIn("VXN.roc5 < 0", recovery["when"])

    def test_baseline_loads_vxn_to_keep_the_calendar_aligned(self):
        definition = candidate_definition(PROFILES[0])

        self.assertIn("VXN", definition["assets"].get("observations", []))


if __name__ == "__main__":
    unittest.main()

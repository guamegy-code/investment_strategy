import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy import AllocationState
from validation.vxus_bil_walkforward import VXUSBILShareStrategy


class VXUSBILWalkForwardTests(unittest.TestCase):
    def test_half_share_keeps_half_of_bil_in_bear(self):
        strategy = VXUSBILShareStrategy(0.50)
        strategy.state = AllocationState.BEAR
        strategy.safe_asset = "BIL"

        target = strategy._target_for_state()

        self.assertEqual(
            target,
            {"QQQ": 0.0, "BND": 0.0, "BIL": 0.50, "VXUS": 0.50},
        )

    def test_bnd_is_fully_eligible_regardless_of_bil_share(self):
        strategy = VXUSBILShareStrategy(0.0)
        strategy.state = AllocationState.BEAR
        strategy.safe_asset = "BND"

        target = strategy._target_for_state()

        self.assertEqual(
            target,
            {"QQQ": 0.0, "BND": 0.30, "BIL": 0.0, "VXUS": 0.70},
        )

    def test_untested_share_is_rejected(self):
        with self.assertRaises(ValueError):
            VXUSBILShareStrategy(0.33)


if __name__ == "__main__":
    unittest.main()

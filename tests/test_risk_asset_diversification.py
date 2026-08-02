import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from strategy import AllocationState  # noqa: E402
from validation.risk_asset_diversification import (  # noqa: E402
    FixedDiversifiedRiskStrategy,
    MomentumDiversifiedRiskStrategy,
)


def asset(momentum, above_ema=True):
    return {
        "Close": 100.0,
        "EMA200": 90.0 if above_ema else 110.0,
        "ROC60": momentum,
        "ROC120": momentum,
    }


def market(qqq, vtv, vxus, usmv, bil=2.0):
    return {
        "QQQ": asset(*qqq),
        "VTV": asset(*vtv),
        "VXUS": asset(*vxus),
        "USMV": asset(*usmv),
        "BIL": asset(bil),
    }


class RiskAssetDiversificationTests(unittest.TestCase):
    def test_fixed_mix_preserves_total_risk_cap(self):
        strategy = FixedDiversifiedRiskStrategy()
        strategy.state = AllocationState.BULL
        strategy.safe_asset = "BND"

        target = strategy._target_for_state()

        self.assertAlmostEqual(target["QQQ"], 0.42)
        self.assertAlmostEqual(target["VTV"], 0.14)
        self.assertAlmostEqual(target["VXUS"], 0.14)
        self.assertAlmostEqual(sum(target.values()), 1.0)

    def test_momentum_rotates_out_of_weak_qqq(self):
        strategy = MomentumDiversifiedRiskStrategy()

        selected = strategy._select_mix(market(
            qqq=(10.0, False),
            vtv=(8.0, True),
            vxus=(6.0, True),
            usmv=(1.0, True),
        ))

        self.assertEqual(selected, {"VTV": 0.5, "VXUS": 0.5})

    def test_momentum_caps_qqq_and_selects_best_complement(self):
        strategy = MomentumDiversifiedRiskStrategy()

        selected = strategy._select_mix(market(
            qqq=(12.0, True),
            vtv=(8.0, True),
            vxus=(6.0, True),
            usmv=(10.0, True),
        ))

        self.assertEqual(selected, {"QQQ": 0.6, "USMV": 0.4})

    def test_momentum_moves_fully_to_safe_assets_when_none_qualify(self):
        strategy = MomentumDiversifiedRiskStrategy()
        strategy.active_mix = strategy._select_mix(market(
            qqq=(1.0, False),
            vtv=(1.0, False),
            vxus=(1.0, False),
            usmv=(1.0, False),
        ))
        strategy.state = AllocationState.BULL
        strategy.safe_asset = "BND"

        target = strategy._target_for_state()

        self.assertEqual(target["BND"], 1.0)
        self.assertAlmostEqual(sum(target.values()), 1.0)


if __name__ == "__main__":
    unittest.main()

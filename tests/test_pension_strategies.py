import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from main import build_runner
from pension_strategies import (
    PensionKodexStrategy,
    PensionKoActStrategy,
    PensionNasdaqMixStrategy,
    PensionTimeStrategy,
)
from strategy import (
    AllocationState,
    PensionBlendedRiskAllocationStrategy,
    PensionBlendedVXUSSubstitutionStrategy,
    PensionRiskAllocationStrategy,
    PensionVXUSSubstitutionStrategy,
)


class PensionStrategyTests(unittest.TestCase):
    def test_product_strategies_have_unique_korean_risk_assets(self):
        expected = {
            PensionKodexStrategy: "379810.KS",
            PensionTimeStrategy: "426030.KS",
            PensionKoActStrategy: "0015B0.KS",
        }

        for strategy_type, risk_asset in expected.items():
            strategy = strategy_type()
            self.assertEqual(strategy.SIGNAL_ASSET, "QQQ")
            self.assertEqual(strategy.RISK_ASSET, risk_asset)
            self.assertIn("QQQ", strategy.required_tickers)
            self.assertIn(risk_asset, strategy.required_tickers)

    def test_product_tickers_can_be_overridden_for_another_data_provider(self):
        time_strategy = PensionTimeStrategy(risk_asset="426030")
        mix_strategy = PensionNasdaqMixStrategy(
            product_assets={"TIME": "426030", "KOACT": "0015B0"}
        )

        self.assertEqual(time_strategy.RISK_ASSET, "426030")
        self.assertIn("426030", mix_strategy.required_tickers)
        self.assertIn("0015B0", mix_strategy.required_tickers)
        self.assertNotIn("426030.KS", mix_strategy.required_tickers)

    def test_nasdaq_mix_uses_the_selected_product_weights(self):
        strategy = PensionNasdaqMixStrategy()
        strategy.state = AllocationState.BULL
        strategy.safe_asset = strategy.BOND_ASSET

        target = strategy._target_for_state()

        self.assertEqual(
            target,
            {
                "379810.KS": 0.35,
                "426030.KS": 0.21,
                "0015B0.KS": 0.14,
                "BND": 0.30,
                "BIL": 0.0,
            },
        )
        self.assertAlmostEqual(
            sum(target[ticker] for ticker in strategy.risk_assets), 0.70
        )

    def test_main_lists_all_active_pension_strategies(self):
        runner = build_runner()

        self.assertEqual(
            tuple(strategy.__class__.__name__ for strategy in runner.strategies),
            (
                "PensionRiskAllocationStrategy",
                "PensionBlendedRiskAllocationStrategy",
                "PensionVXUSSubstitutionStrategy",
                "PensionBlendedVXUSSubstitutionStrategy",
                "STATIC_PENSION_7030",
                "PensionNasdaqMixStrategy",
                "PensionKodexStrategy",
                "PensionTimeStrategy",
                "PensionKoActStrategy",
            ),
        )
        self.assertEqual(
            tuple(runner.tickers),
            (
                "QQQ",
                "BND",
                "BIL",
                "VXUS",
                "379810.KS",
                "426030.KS",
                "0015B0.KS",
            ),
        )
        self.assertTrue(runner.use_strategy_tickers)

    def test_vxus_substitution_respects_combined_risk_cap(self):
        strategy = PensionVXUSSubstitutionStrategy()
        strategy.state = AllocationState.RECOVERY
        strategy.safe_asset = "BND"

        target = strategy._target_for_state()

        self.assertEqual(target["QQQ"], 0.50)
        self.assertEqual(target["VXUS"], 0.20)
        self.assertEqual(target["BND"], 0.30)
        self.assertLessEqual(target["QQQ"] + target["VXUS"], 0.70)
        self.assertAlmostEqual(sum(target.values()), 1.0)

    def test_vxus_substitution_never_replaces_bil(self):
        strategy = PensionVXUSSubstitutionStrategy()
        strategy.state = AllocationState.BEAR
        strategy.safe_asset = "BIL"

        target = strategy._target_for_state()

        self.assertEqual(target["QQQ"], 0.0)
        self.assertEqual(target["VXUS"], 0.0)
        self.assertEqual(target["BIL"], 1.0)

    def test_vxus_is_reported_as_a_risk_asset(self):
        strategy = PensionVXUSSubstitutionStrategy()

        self.assertEqual(strategy.risk_asset_tickers, ("QQQ", "VXUS"))
        self.assertIn("VXUS", strategy.required_tickers)

    def test_pension_strategy_uses_25_point_safe_asset_ladder(self):
        cases = (
            (1.00, 1.00),
            (0.50, 0.75),
            (0.25, 0.75),
            (0.00, 0.50),
            (-0.25, 0.50),
            (-0.50, 0.25),
            (-1.00, 0.00),
        )
        for spread, expected_bnd_share in cases:
            strategy = PensionBlendedRiskAllocationStrategy()
            strategy._select_safe_asset({
                "BND": {"ROC40": 2.0 + spread},
                "BIL": {"ROC40": 2.0},
            })
            self.assertEqual(
                strategy.safe_asset_mix["BND"], expected_bnd_share
            )
            self.assertEqual(
                strategy.safe_asset_mix["BIL"], 1.0 - expected_bnd_share
            )

    def test_safe_asset_ladder_scales_the_state_safe_sleeve(self):
        strategy = PensionBlendedRiskAllocationStrategy()
        strategy.state = AllocationState.BULL
        strategy.safe_asset_mix = {"BND": 0.75, "BIL": 0.25}

        target = strategy._target_for_state()

        self.assertEqual(target, {"QQQ": 0.70, "BND": 0.225, "BIL": 0.075})

    def test_vxus_replaces_only_the_blended_bnd_portion(self):
        strategy = PensionBlendedVXUSSubstitutionStrategy()
        strategy.state = AllocationState.RECOVERY
        strategy.safe_asset_mix = {"BND": 0.75, "BIL": 0.25}

        target = strategy._target_for_state()

        self.assertEqual(
            target,
            {
                "QQQ": 0.50,
                "BND": 0.175,
                "BIL": 0.125,
                "VXUS": 0.20,
            },
        )
        self.assertLessEqual(target["QQQ"] + target["VXUS"], 0.70)


if __name__ == "__main__":
    unittest.main()

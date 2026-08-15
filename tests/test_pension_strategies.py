import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from main import build_runner, ensure_runner_data
from config import END_DATE, FX_RATE_TICKERS, START_DATE
from strategy_domain import strategy_display_name
from experimental_strategies import (
    RetirementAllocationProfitBandStrategy,
    RetirementAllocationProfitBandVXUSStrategy,
    RetirementAllocationSafeSleeveOnlyStrategy,
    RetirementAllocationSafeSleeveOnlyVXUSStrategy,
    _UpperRiskBandMixin,
)
from pension_strategies import (
    KodexNasdaqAllocationStrategy,
    KoActNasdaqAllocationStrategy,
    NasdaqProductMixAllocationStrategy,
    TimeNasdaqAllocationStrategy,
)
from strategy import (
    AllocationState,
    RetirementAllocationLegacyStrategy,
    RetirementAllocationSafeBlendStrategy,
    RetirementAllocationSelectiveRebalanceStrategy,
    RetirementAllocationSelectiveSafeBlendStrategy,
    RetirementAllocationSelectiveSPYStrategy,
    RetirementAllocationSelectiveVXUSStrategy,
    RetirementAllocationSPYStrategy,
    RetirementAllocationStrategy,
    RetirementAllocationVXUSStrategy,
    SafeBlendAllocationStrategy,
    VXUSSubstitutionStrategy,
)


class RetirementStrategyTests(unittest.TestCase):
    def test_selective_extensions_inherit_the_validated_rebalance_parent(self):
        self.assertTrue(issubclass(
            RetirementAllocationSafeBlendStrategy,
            RetirementAllocationStrategy,
        ))
        self.assertTrue(issubclass(
            RetirementAllocationVXUSStrategy,
            RetirementAllocationStrategy,
        ))
        self.assertTrue(issubclass(
            RetirementAllocationSPYStrategy,
            RetirementAllocationVXUSStrategy,
        ))
        self.assertTrue(issubclass(
            RetirementAllocationProfitBandVXUSStrategy,
            RetirementAllocationProfitBandStrategy,
        ))
        self.assertTrue(issubclass(
            RetirementAllocationProfitBandStrategy,
            _UpperRiskBandMixin,
        ))
        self.assertTrue(issubclass(
            RetirementAllocationSafeSleeveOnlyStrategy,
            RetirementAllocationProfitBandStrategy,
        ))
        self.assertTrue(issubclass(
            RetirementAllocationSafeSleeveOnlyVXUSStrategy,
            RetirementAllocationProfitBandVXUSStrategy,
        ))
        self.assertTrue(issubclass(
            RetirementAllocationStrategy,
            RetirementAllocationLegacyStrategy,
        ))
        self.assertTrue(issubclass(
            RetirementAllocationSelectiveRebalanceStrategy,
            RetirementAllocationStrategy,
        ))
        self.assertTrue(issubclass(
            RetirementAllocationSelectiveSafeBlendStrategy,
            RetirementAllocationSafeBlendStrategy,
        ))
        self.assertTrue(issubclass(
            RetirementAllocationSelectiveVXUSStrategy,
            RetirementAllocationVXUSStrategy,
        ))
        self.assertTrue(issubclass(
            RetirementAllocationSelectiveSPYStrategy,
            RetirementAllocationSPYStrategy,
        ))
        self.assertTrue(issubclass(
            RetirementAllocationSelectiveSPYStrategy,
            RetirementAllocationSelectiveVXUSStrategy,
        ))

    def test_selective_safe_blend_preserves_risk_and_safe_mix(self):
        strategy = RetirementAllocationSafeBlendStrategy()
        strategy.state = AllocationState.BULL
        strategy.safe_asset = "BND"
        strategy.safe_asset_mix = {"BND": 0.50, "BIL": 0.50}

        target = strategy._target_for_state()

        self.assertEqual(target, {"QQQ": 0.70, "BND": 0.15, "BIL": 0.15})

    def test_selective_vxus_counts_vxus_inside_risk_cap(self):
        strategy = RetirementAllocationVXUSStrategy()
        strategy.state = AllocationState.RECOVERY
        strategy.safe_asset = "BND"

        target = strategy._target_for_state()

        self.assertEqual(target["QQQ"], 0.50)
        self.assertEqual(target["VXUS"], 0.20)
        self.assertEqual(target["BND"], 0.30)
        self.assertLessEqual(target["QQQ"] + target["VXUS"], 0.70)

    def test_selective_vxus_blends_safe_assets_before_vxus_substitution(self):
        strategy = RetirementAllocationVXUSStrategy()
        strategy.state = AllocationState.RECOVERY
        strategy.safe_asset = "BND"
        strategy.safe_asset_mix = {"BND": 0.50, "BIL": 0.50}

        target = strategy._target_for_state()

        self.assertEqual(
            target,
            {"QQQ": 0.50, "BND": 0.05, "BIL": 0.25, "VXUS": 0.20},
        )
        self.assertAlmostEqual(target["QQQ"] + target["VXUS"], 0.70)

    def test_selective_spy_replaces_vxus_as_the_alternative_risk_asset(self):
        strategy = RetirementAllocationSPYStrategy()
        strategy.state = AllocationState.RECOVERY
        strategy.safe_asset = "BND"

        target = strategy._target_for_state()

        self.assertEqual(target["QQQ"], 0.50)
        self.assertEqual(target["SPY"], 0.20)
        self.assertEqual(target["BND"], 0.30)
        self.assertNotIn("VXUS", target)
        self.assertEqual(strategy.risk_asset_tickers, ("QQQ", "SPY"))
        self.assertIn("SPY", strategy.required_tickers)
        self.assertNotIn("VXUS", strategy.required_tickers)
        self.assertLessEqual(target["QQQ"] + target["SPY"], 0.70)

    def test_selective_spy_never_replaces_bil(self):
        strategy = RetirementAllocationSPYStrategy()
        strategy.state = AllocationState.BEAR
        strategy.safe_asset = "BIL"

        target = strategy._target_for_state()

        self.assertEqual(target["QQQ"], 0.0)
        self.assertEqual(target["SPY"], 0.0)
        self.assertEqual(target["BIL"], 1.0)

    def test_product_strategies_have_unique_korean_risk_assets(self):
        expected = {
            KodexNasdaqAllocationStrategy: "379810.KS",
            TimeNasdaqAllocationStrategy: "426030.KS",
            KoActNasdaqAllocationStrategy: "0015B0.KS",
        }

        for strategy_type, risk_asset in expected.items():
            strategy = strategy_type()
            self.assertIsInstance(
                strategy,
                RetirementAllocationProfitBandVXUSStrategy,
            )
            self.assertEqual(strategy.SIGNAL_ASSET, "QQQ")
            self.assertEqual(strategy.RISK_ASSET, "QQQ")
            self.assertEqual(
                strategy.risk_asset_tickers,
                (risk_asset, "VXUS"),
            )
            self.assertIn("QQQ", strategy.required_tickers)
            self.assertIn(risk_asset, strategy.required_tickers)
            self.assertIn("VXUS", strategy.required_tickers)
            self.assertEqual(strategy.FX_RATE_TICKER, "KRW=X")

    def test_product_tickers_can_be_overridden_for_another_data_provider(self):
        time_strategy = TimeNasdaqAllocationStrategy(
            risk_asset="426030"
        )
        mix_strategy = NasdaqProductMixAllocationStrategy(
            product_assets={"TIME": "426030", "KOACT": "0015B0"}
        )

        self.assertEqual(
            time_strategy.risk_asset_tickers,
            ("426030", "VXUS"),
        )
        self.assertIn("426030", mix_strategy.required_tickers)
        self.assertIn("0015B0", mix_strategy.required_tickers)
        self.assertNotIn("426030.KS", mix_strategy.required_tickers)

    def test_nasdaq_mix_uses_the_selected_product_weights(self):
        strategy = NasdaqProductMixAllocationStrategy()
        self.assertIsInstance(
            strategy,
            RetirementAllocationProfitBandVXUSStrategy,
        )
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
                "VXUS": 0.0,
            },
        )
        self.assertAlmostEqual(
            sum(target[ticker] for ticker in strategy.risk_assets), 0.70
        )

    def test_product_strategies_apply_vxus_in_recovery(self):
        single = KodexNasdaqAllocationStrategy()
        product_mix = NasdaqProductMixAllocationStrategy()

        for strategy in (single, product_mix):
            strategy.state = AllocationState.RECOVERY
            strategy.safe_asset = strategy.BOND_ASSET
            target = strategy._target_for_state()

            self.assertEqual(target["VXUS"], 0.20)
            self.assertEqual(target["BND"], 0.30)
            self.assertAlmostEqual(
                sum(target[ticker] for ticker in strategy.risk_asset_tickers),
                0.70,
            )

    def test_main_lists_all_active_pension_strategies(self):
        runner = build_runner()

        self.assertEqual(
            tuple(strategy_display_name(strategy) for strategy in runner.strategies),
            (
                "RetirementAllocationStrategy",
                "RetirementAllocationVXUSStrategy",
                "RetirementAllocationProfitBandStrategy",
                "RetirementAllocationProfitBandVXUSStrategy",
                "KodexNasdaqAllocationStrategy",
                "TimeNasdaqAllocationStrategy",
                "KoActNasdaqAllocationStrategy",
                "NasdaqProductMixAllocationStrategy",
                "STATIC_RETIREMENT_7030",
                "ASYMMETRIC_TREND_BAND_ADD_DEFENSE2",
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
                "GLD",
            ),
        )
        self.assertTrue(runner.use_strategy_tickers)
        self.assertEqual(
            runner.backtest_options,
            {"start_date": START_DATE, "end_date": END_DATE},
        )
        self.assertEqual(
            runner.strategies[0].strategy_id, "dsl:retirement-allocation"
        )
        self.assertEqual(
            runner.strategies[1].strategy_id,
            "dsl:retirement-allocation-vxus",
        )
        self.assertEqual(
            runner.strategies[2].strategy_id,
            "dsl:retirement-allocation-profit-band",
        )

    def test_main_downloads_only_through_the_runner_data_preparation(self):
        runner = build_runner()
        with patch(
            "main.ensure_data_files", return_value=("QQQ",)
        ) as ensure:
            downloaded = ensure_runner_data(runner)

        self.assertEqual(downloaded, ("QQQ",))
        expected_tickers = tuple(dict.fromkeys(
            (*runner.tickers, *FX_RATE_TICKERS)
        ))
        expected_fields = {}
        for strategy in runner.strategies:
            for ticker, fields in getattr(
                strategy, "required_market_fields", {}
            ).items():
                expected_fields.setdefault(ticker, set()).update(fields)
        ensure.assert_called_once_with(
            expected_tickers,
            data_dir=runner.data_dir,
            required_market_fields=expected_fields,
        )

    def test_vxus_substitution_respects_combined_risk_cap(self):
        strategy = VXUSSubstitutionStrategy()
        strategy.state = AllocationState.RECOVERY
        strategy.safe_asset = "BND"

        target = strategy._target_for_state()

        self.assertEqual(target["QQQ"], 0.50)
        self.assertEqual(target["VXUS"], 0.20)
        self.assertEqual(target["BND"], 0.30)
        self.assertLessEqual(target["QQQ"] + target["VXUS"], 0.70)
        self.assertAlmostEqual(sum(target.values()), 1.0)

    def test_vxus_substitution_never_replaces_bil(self):
        strategy = VXUSSubstitutionStrategy()
        strategy.state = AllocationState.BEAR
        strategy.safe_asset = "BIL"

        target = strategy._target_for_state()

        self.assertEqual(target["QQQ"], 0.0)
        self.assertEqual(target["VXUS"], 0.0)
        self.assertEqual(target["BIL"], 1.0)

    def test_vxus_is_reported_as_a_risk_asset(self):
        strategy = VXUSSubstitutionStrategy()

        self.assertEqual(strategy.risk_asset_tickers, ("QQQ", "VXUS"))
        self.assertIn("VXUS", strategy.required_tickers)

    def test_index_assets_can_each_map_to_multiple_products(self):
        class ProductMappedStrategy(VXUSSubstitutionStrategy):
            ASSET_MAPPING = {
                "QQQ": {"NASDAQ_A": 0.60, "NASDAQ_B": 0.40},
                "VXUS": {"GLOBAL_A": 0.75, "GLOBAL_B": 0.25},
                "BND": {"PENSION_BOND": 1.0},
                "BIL": {"PENSION_CASH": 1.0},
            }

        strategy = ProductMappedStrategy()
        strategy.state = AllocationState.RECOVERY
        strategy.safe_asset = "BND"

        self.assertEqual(
            strategy._target_for_state(),
            {
                "NASDAQ_A": 0.30,
                "NASDAQ_B": 0.20,
                "PENSION_BOND": 0.30,
                "PENSION_CASH": 0.0,
                "GLOBAL_A": 0.15,
                "GLOBAL_B": 0.05,
            },
        )
        self.assertEqual(
            strategy.risk_asset_tickers,
            ("NASDAQ_A", "NASDAQ_B", "GLOBAL_A", "GLOBAL_B"),
        )

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
            strategy = SafeBlendAllocationStrategy()
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

    def test_safe_asset_ladder_is_not_applied_in_bull(self):
        strategy = SafeBlendAllocationStrategy()
        strategy.state = AllocationState.BULL
        strategy.safe_asset = "BND"
        strategy.safe_asset_mix = {"BND": 0.75, "BIL": 0.25}

        target = strategy._target_for_state()

        self.assertEqual(target, {"QQQ": 0.70, "BND": 0.30, "BIL": 0.0})

    def test_safe_asset_ladder_scales_the_defensive_safe_sleeve(self):
        strategy = SafeBlendAllocationStrategy()
        strategy.state = AllocationState.RECOVERY
        strategy.safe_asset = "BND"
        strategy.safe_asset_mix = {"BND": 0.75, "BIL": 0.25}

        target = strategy._target_for_state()

        self.assertEqual(target, {"QQQ": 0.50, "BND": 0.375, "BIL": 0.125})

if __name__ == "__main__":
    unittest.main()

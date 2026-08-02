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
from strategy import AllocationState


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
                "379810.KS",
                "426030.KS",
                "0015B0.KS",
            ),
        )
        self.assertTrue(runner.use_strategy_tickers)


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from experimental_strategies import ASYMMETRIC_TREND_BAND_ADD_DEFENSE2_TUNED
from strategy import (
    ASYMMETRIC_TREND_BAND,
    ASYMMETRIC_TREND_BAND_ADD_DEFENSE,
    ASYMMETRIC_TREND_BAND_ADD_DEFENSE2,
    AllocationState,
    BaseStrategy,
    BASIC_BANG_DIV,
    DownsideTrendOverlayStrategy,
    RetirementAllocationStrategy,
    RETIREMENT_7030_BAND,
    STATIC_70_BND10_BIL10_GLD10,
    STATIC_RETIREMENT_7030,
)


class StablePortfolio:
    def weights(self, prices):
        return {ticker: 0.0 for ticker in prices}


def market(
    close, ema20, ema55, ema200, roc5, roc20, slope5,
    roc60=0.0, slope200=0.0, drawdown120=0.0,
    bnd_roc=1.0, bil_roc=0.0,
):
    return {
        "QQQ": {
            "Close": close,
            "EMA20": ema20,
            "EMA55": ema55,
            "EMA200": ema200,
            "ROC5": roc5,
            "ROC20": roc20,
            "EMA20_SLOPE5": slope5,
            "ROC60": roc60,
            "EMA200_SLOPE20": slope200,
            "DRAWDOWN120": drawdown120,
            "RSI14": 50.0,
        },
        "BND": {"Close": 100.0, "ROC40": bnd_roc},
        "BIL": {"Close": 100.0, "ROC40": bil_roc},
        "GLD": {"Close": 100.0},
    }


BULL = market(110, 105, 100, 95, 2, 5, 1, roc60=5)
CAUTION = market(99, 101, 100, 95, -1, -2, -1, roc60=-1)
STRUCTURAL_BEAR = market(
    85, 90, 95, 100, -4, -10, -2,
    roc60=-12, slope200=-1, drawdown120=-0.15,
)
RECOVERY = market(101, 100, 102, 98, 2, 1, 1, roc60=1)


class RetirementAllocationTests(unittest.TestCase):
    def setUp(self):
        self.strategy = RetirementAllocationStrategy()
        self.portfolio = StablePortfolio()
        self.date = pd.Timestamp("2024-01-02")

    def evaluate(self, data):
        result = self.strategy.evaluate(self.date, data, self.portfolio)
        self.date += pd.Timedelta(days=1)
        return result

    def test_final_configuration(self):
        self.assertEqual(self.strategy.SAFE_MOMENTUM_PERIOD, 40)
        self.assertEqual(self.strategy.SAFE_SWITCH_BUFFER, 0.25)
        self.assertEqual(
            self.strategy.STATE_RISK_WEIGHTS[AllocationState.BEAR], 0.00
        )
        self.assertEqual(
            self.strategy.STATE_RISK_WEIGHTS[AllocationState.RECOVERY], 0.50
        )

    def test_pension_strategy_uses_no_gold_and_caps_risk_asset(self):
        strategy = RetirementAllocationStrategy()
        portfolio = StablePortfolio()
        date = pd.Timestamp("2024-01-02")

        signal = strategy.evaluate(date, BULL, portfolio)

        self.assertEqual(strategy.state, AllocationState.BULL)
        self.assertEqual(signal["target"], {"QQQ": 0.70, "BND": 0.30, "BIL": 0.0})
        self.assertNotIn("GLD", signal["target"])
        self.assertLessEqual(signal["target"]["QQQ"], strategy.MAX_RISK_WEIGHT)

    def test_pension_strategy_preserves_dynamic_qqq_weights(self):
        strategy = RetirementAllocationStrategy()
        expected = {
            AllocationState.BULL: 0.70,
            AllocationState.CAUTION: 0.70,
            AllocationState.BEAR: 0.00,
            AllocationState.RECOVERY: 0.50,
        }

        self.assertEqual(strategy.STATE_RISK_WEIGHTS, expected)
        for state, risk_weight in expected.items():
            strategy.state = state
            strategy.safe_asset = strategy.BOND_ASSET
            target = strategy._target_for_state()
            self.assertAlmostEqual(sum(target.values()), 1.0)
            self.assertEqual(target[strategy.RISK_ASSET], risk_weight)
            self.assertLessEqual(risk_weight, strategy.MAX_RISK_WEIGHT)

    def test_pension_strategy_accepts_replaceable_product_identifiers(self):
        strategy = RetirementAllocationStrategy(
            risk_asset="TIGER_NASDAQ100",
            bond_asset="PENSION_BOND",
            cash_asset="PENSION_CASH",
        )
        custom_market = {
            "TIGER_NASDAQ100": BULL["QQQ"],
            "PENSION_BOND": {"Close": 100.0, "ROC40": 1.0},
            "PENSION_CASH": {"Close": 100.0, "ROC40": 0.0},
        }

        signal = strategy.evaluate(
            pd.Timestamp("2024-01-02"), custom_market, StablePortfolio()
        )

        self.assertEqual(
            strategy.required_tickers,
            ("TIGER_NASDAQ100", "PENSION_BOND", "PENSION_CASH"),
        )
        self.assertEqual(
            signal["target"],
            {"TIGER_NASDAQ100": 0.70, "PENSION_BOND": 0.30, "PENSION_CASH": 0.0},
        )

    def test_pension_strategy_separates_signal_from_traded_risk_asset(self):
        strategy = RetirementAllocationStrategy(
            signal_asset="QQQ_SIGNAL",
            risk_asset="KOREAN_NASDAQ_ETF",
            bond_asset="PENSION_BOND",
            cash_asset="PENSION_CASH",
        )
        custom_market = {
            "QQQ_SIGNAL": BULL["QQQ"],
            "KOREAN_NASDAQ_ETF": {"Close": 100.0},
            "PENSION_BOND": {"Close": 100.0, "ROC40": 1.0},
            "PENSION_CASH": {"Close": 100.0, "ROC40": 0.0},
        }

        signal = strategy.evaluate(
            pd.Timestamp("2024-01-02"), custom_market, StablePortfolio()
        )

        self.assertEqual(strategy.state, AllocationState.BULL)
        self.assertEqual(
            signal["target"],
            {
                "KOREAN_NASDAQ_ETF": 0.70,
                "PENSION_BOND": 0.30,
                "PENSION_CASH": 0.0,
            },
        )
        self.assertNotIn("QQQ_SIGNAL", signal["target"])
        self.assertEqual(
            strategy.required_tickers,
            (
                "QQQ_SIGNAL",
                "KOREAN_NASDAQ_ETF",
                "PENSION_BOND",
                "PENSION_CASH",
            ),
        )

    def test_pension_strategy_splits_total_risk_across_multiple_assets(self):
        strategy = RetirementAllocationStrategy(
            signal_asset="QQQ",
            risk_assets={
                "KODEX_NASDAQ": 0.50,
                "TIME_NASDAQ": 0.30,
                "KOACT_NASDAQ": 0.20,
            },
            bond_asset="PENSION_BOND",
            cash_asset="PENSION_CASH",
        )
        strategy.state = AllocationState.BULL
        strategy.safe_asset = strategy.BOND_ASSET

        target = strategy._target_for_state()

        self.assertEqual(
            target,
            {
                "KODEX_NASDAQ": 0.35,
                "TIME_NASDAQ": 0.21,
                "KOACT_NASDAQ": 0.14,
                "PENSION_BOND": 0.30,
                "PENSION_CASH": 0.0,
            },
        )
        self.assertAlmostEqual(
            sum(target[ticker] for ticker in strategy.risk_assets),
            strategy.MAX_RISK_WEIGHT,
        )
        self.assertEqual(
            strategy.required_tickers,
            (
                "QQQ",
                "KODEX_NASDAQ",
                "TIME_NASDAQ",
                "KOACT_NASDAQ",
                "PENSION_BOND",
                "PENSION_CASH",
            ),
        )

    def test_multiple_risk_asset_weights_must_sum_to_one(self):
        with self.assertRaises(ValueError):
            RetirementAllocationStrategy(
                risk_assets={"KODEX_NASDAQ": 0.50, "TIME_NASDAQ": 0.40}
            )

    def test_downside_overlay_respects_target_caps(self):
        strategy = DownsideTrendOverlayStrategy()
        overlay_market = {
            "QQQ": {
                "Close": 85.0,
                "EMA200": 100.0,
                "ROC60": -20.0,
                "ROC120": -30.0,
                "ROC252": -40.0,
                "VOL60": 0.40,
            },
            "BND": {"Close": 100.0, "ROC60": 2.0, "ROC120": 3.0, "VOL60": 0.06},
            "BIL": {"Close": 100.0, "ROC60": 1.0, "ROC120": 2.0, "VOL60": 0.01},
            "GLD": {"Close": 100.0, "ROC60": 8.0, "ROC120": 10.0, "VOL60": 0.15},
        }

        target = strategy._desired_target(overlay_market)

        self.assertAlmostEqual(sum(target.values()), 1.0)
        self.assertGreaterEqual(target["QQQ"], 0.20)
        self.assertLessEqual(target["QQQ"], 0.70)
        self.assertLessEqual(target["GLD"], 0.20)

    def test_complete_state_cycle(self):
        self.evaluate(BULL)
        self.assertEqual(self.strategy.state, AllocationState.BULL)
        self.assertEqual(self.strategy.target["QQQ"], 0.70)

        for _ in range(3):
            caution_signal = self.evaluate(CAUTION)
        self.assertTrue(caution_signal["rebalance"])
        self.assertEqual(self.strategy.state, AllocationState.CAUTION)
        self.assertEqual(self.strategy.target["QQQ"], 0.70)

        for _ in range(10):
            bear_signal = self.evaluate(STRUCTURAL_BEAR)
        self.assertTrue(bear_signal["rebalance"])
        self.assertEqual(self.strategy.state, AllocationState.BEAR)
        self.assertEqual(self.strategy.target["QQQ"], 0.0)

        self.assertFalse(self.evaluate(RECOVERY)["rebalance"])
        self.assertTrue(self.evaluate(RECOVERY)["rebalance"])
        self.assertEqual(self.strategy.state, AllocationState.RECOVERY)
        self.assertEqual(self.strategy.target["QQQ"], 0.50)

        for _ in range(3):
            bull_signal = self.evaluate(BULL)
        self.assertTrue(bull_signal["rebalance"])
        self.assertEqual(self.strategy.state, AllocationState.BULL)

    def test_shallow_breakdown_does_not_enter_bear(self):
        self.evaluate(BULL)
        for _ in range(12):
            self.evaluate(CAUTION)
        self.assertNotEqual(self.strategy.state, AllocationState.BEAR)

    def test_safe_asset_rotates_monthly_with_buffer(self):
        weak_bonds = market(
            110, 105, 100, 95, 2, 5, 1,
            roc60=5, bnd_roc=-2, bil_roc=1,
        )
        self.evaluate(weak_bonds)
        self.assertEqual(self.strategy.safe_asset, "BIL")
        self.assertAlmostEqual(self.strategy.target["BIL"], 0.30)

        strong_bonds = market(
            110, 105, 100, 95, 2, 5, 1,
            roc60=5, bnd_roc=2, bil_roc=1,
        )
        self.date = pd.Timestamp("2024-02-01")
        result = self.evaluate(strong_bonds)
        self.assertTrue(result["rebalance"])
        self.assertEqual(self.strategy.safe_asset, "BND")
        self.assertAlmostEqual(self.strategy.target["BND"], 0.30)
        self.assertEqual(self.strategy.target["BIL"], 0.0)

    def test_retained_static_benchmarks_sum_to_one(self):
        for strategy_class in (
            STATIC_70_BND10_BIL10_GLD10,
            STATIC_RETIREMENT_7030,
        ):
            target = strategy_class().target
            self.assertAlmostEqual(sum(target.values()), 1.0)
            self.assertEqual(target["QQQ"], 0.70)

    def test_pension_static_benchmark_is_replaceable_and_has_no_gold(self):
        strategy = STATIC_RETIREMENT_7030(
            signal_asset="QQQ_SIGNAL",
            risk_asset="TIGER_NASDAQ100",
            bond_asset="PENSION_BOND",
            cash_asset="PENSION_CASH",
        )

        self.assertEqual(
            strategy.target,
            {
                "TIGER_NASDAQ100": 0.70,
                "PENSION_BOND": 0.15,
                "PENSION_CASH": 0.15,
            },
        )
        self.assertNotIn("GLD", strategy.target)
        self.assertEqual(
            strategy.required_tickers,
            (
                "QQQ_SIGNAL",
                "TIGER_NASDAQ100",
                "PENSION_BOND",
                "PENSION_CASH",
            ),
        )

    def test_pension_static_supports_the_same_multiple_risk_assets(self):
        strategy = STATIC_RETIREMENT_7030(
            signal_asset="QQQ",
            risk_assets={"KODEX_NASDAQ": 0.60, "TIME_NASDAQ": 0.40},
        )

        self.assertEqual(
            strategy.target,
            {
                "KODEX_NASDAQ": 0.42,
                "TIME_NASDAQ": 0.28,
                "BND": 0.15,
                "BIL": 0.15,
            },
        )

    def test_static_benchmark_tracks_dynamic_market_state(self):
        strategy = STATIC_70_BND10_BIL10_GLD10()

        strategy.evaluate(self.date, BULL, self.portfolio)
        self.assertEqual(strategy.state, AllocationState.BULL)
        for _ in range(3):
            strategy.evaluate(self.date, CAUTION, self.portfolio)

        self.assertEqual(strategy.state, AllocationState.CAUTION)
        self.assertEqual(
            strategy.target,
            {"QQQ": 0.70, "BND": 0.10, "BIL": 0.10, "GLD": 0.10},
        )

    def test_basic_bang_div_uses_qqq_rsi(self):
        strategy = BASIC_BANG_DIV()
        test_market = market(110, 105, 100, 95, 2, 5, 1, roc60=5)
        test_market["QQQ"]["RSI14"] = 85.0
        signal = strategy.evaluate(self.date, test_market, self.portfolio)
        self.assertTrue(signal["rebalance"])
        self.assertEqual(strategy.state, "BEAR")
        self.assertEqual(signal["target"]["QQQ"], 0.30)

    def test_retirement_band_uses_qqq_and_bnd(self):
        strategy = RETIREMENT_7030_BAND()
        signal = strategy.evaluate(self.date, BULL, self.portfolio)
        self.assertTrue(signal["rebalance"])
        self.assertEqual(signal["target"], {"QQQ": 0.70, "BND": 0.30})

    def test_asymmetric_trend_band_uses_qld_ema55(self):
        strategy = ASYMMETRIC_TREND_BAND()
        test_market = {**BULL, "QLD": {"Close": 100.0, "EMA55": 90.0}}
        signal = strategy.evaluate(self.date, test_market, self.portfolio)
        self.assertTrue(signal["rebalance"])
        self.assertIn("UPTREND_BUY_DIP_QLD", signal["reason"])

    def test_defense2_extreme_overbought_uses_disparity60(self):
        strategy = ASYMMETRIC_TREND_BAND_ADD_DEFENSE2()
        test_market = market(110, 105, 100, 95, 2, 5, 1, roc60=5)
        test_market["QQQ"]["RSI14"] = 96.0
        test_market["QQQ"]["DISPARITY60"] = 110.0

        signal = strategy.evaluate(self.date, test_market, self.portfolio)

        self.assertTrue(signal["rebalance"])
        self.assertIn("EXTREME_OVERBOUGHT", signal["reason"])

    def test_defense2_tuned_preserves_pension_safe_asset_floor(self):
        strategy = ASYMMETRIC_TREND_BAND_ADD_DEFENSE2_TUNED()

        self.assertEqual(
            strategy.target_weights,
            {"QQQ": 0.65, "GLD": 0.05, "BND": 0.30},
        )
        self.assertEqual(
            strategy.defensive_weights,
            {"QQQ": 0.30, "GLD": 0.10, "BND": 0.60},
        )
        self.assertGreaterEqual(strategy.target_weights["BND"], 0.30)
        self.assertGreaterEqual(strategy.defensive_weights["BND"], 0.30)
        self.assertEqual(strategy.defensive_drawdown, -0.155)

    def test_defense2_tuned_enters_defense_at_15_5pct_drawdown(self):
        strategy = ASYMMETRIC_TREND_BAND_ADD_DEFENSE2_TUNED()
        strategy.highest_price = 100.0
        falling_market = market(
            84.4, 90.0, 95.0, 100.0, -5.0, -10.0, -2.0,
            roc60=-12.0,
        )

        signal = strategy.evaluate(
            self.date, falling_market, self.portfolio
        )

        self.assertTrue(strategy.is_defensive_mode)
        self.assertTrue(signal["rebalance"])
        self.assertEqual(signal["target"]["BND"], 0.60)

    def test_existing_strategies_use_the_new_base_contract(self):
        strategy_types = (
            BASIC_BANG_DIV,
            RETIREMENT_7030_BAND,
            ASYMMETRIC_TREND_BAND,
            ASYMMETRIC_TREND_BAND_ADD_DEFENSE,
            ASYMMETRIC_TREND_BAND_ADD_DEFENSE2,
            ASYMMETRIC_TREND_BAND_ADD_DEFENSE2_TUNED,
        )

        for strategy_type in strategy_types:
            self.assertIs(strategy_type._signal, BaseStrategy._signal)
            self.assertIsNot(strategy_type.evaluate, BaseStrategy.evaluate)

        signal = RETIREMENT_7030_BAND().evaluate(
            self.date, BULL, self.portfolio
        )
        self.assertEqual(
            set(signal), {"rebalance", "target", "days", "reason"}
        )


if __name__ == "__main__":
    unittest.main()

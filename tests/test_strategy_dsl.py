from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from backtest import Backtest
from experimental_strategies import (
    RetirementAllocationProfitBandStrategy,
    RetirementAllocationProfitBandVXUSStrategy,
)
from strategy import (
    ASYMMETRIC_TREND_BAND_ADD_DEFENSE2,
    RETIREMENT_7030_BAND,
    RetirementAllocationStrategy,
    RetirementAllocationVXUSStrategy,
    STATIC_RETIREMENT_7030,
)
from strategy_domain import StrategyEngine, strategy_identity
from strategy_dsl import (
    DeclarativeStrategy,
    OperatorRegistry,
    ProductMappedStrategy,
    StrategyDefinitionError,
    StrategyExpressionError,
    load_strategy_directory,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PortfolioStub:
    def __init__(self, weights=None):
        self.current_weights = weights or {}

    def weights(self, prices):
        return {
            ticker: self.current_weights.get(ticker, 0.0)
            for ticker in prices
        }


def definition(**overrides):
    value = {
        "strategy": {"id": "test", "name": "Test", "version": 1},
        "assets": {"required": ["QQQ", "BND"], "risk": ["QQQ"]},
        "target": [{"weights": {"QQQ": "70%", "BND": "30%"}}],
        "execution": {"days": 1},
    }
    value.update(overrides)
    return value


def market(close=100.0, drawdown=-0.05):
    return {
        "QQQ": {"Close": close, "EMA200": 95.0, "DRAWDOWN120": drawdown},
        "BND": {"Close": 10.0},
    }


def retirement_market(mode="bull", *, bnd_roc=1.0, bil_roc=0.0):
    qqq = {
        "Close": 105.0,
        "EMA20": 103.0,
        "EMA55": 100.0,
        "EMA200": 98.0,
        "ROC5": 3.0,
        "ROC20": 5.0,
        "EMA20_SLOPE5": 2.0,
        "ROC60": 8.0,
        "EMA200_SLOPE20": 1.0,
        "DRAWDOWN120": 0.0,
    }
    if mode == "caution":
        qqq.update({
            "Close": 85.0,
            "EMA20": 90.0,
            "EMA55": 95.0,
            "EMA200": 100.0,
            "ROC5": -4.0,
            "ROC20": -10.0,
            "EMA20_SLOPE5": -2.0,
            "ROC60": 1.0,
            "EMA200_SLOPE20": 1.0,
            "DRAWDOWN120": -0.05,
        })
    elif mode == "bear":
        qqq.update({
            "Close": 80.0,
            "EMA20": 90.0,
            "EMA55": 95.0,
            "EMA200": 100.0,
            "ROC5": -4.0,
            "ROC20": -10.0,
            "EMA20_SLOPE5": -2.0,
            "ROC60": -12.0,
            "EMA200_SLOPE20": -1.0,
            "DRAWDOWN120": -0.15,
        })
    return {
        "QQQ": qqq,
        "BND": {"Close": 100.0, "ROC40": bnd_roc},
        "BIL": {"Close": 100.0, "ROC40": bil_roc},
        "VXUS": {"Close": 100.0},
    }


def product_definition(**overrides):
    value = {
        "strategy": {"id": "products", "name": "Products", "version": 1},
        "source": "band-7030",
        "products": {
            "QQQ": {"PRODUCT_A": "60%", "PRODUCT_B": "40%"},
            "BND": {"PRODUCT_C": "100%"},
        },
    }
    value.update(overrides)
    return value


def product_market():
    return {
        **market(),
        "PRODUCT_A": {"Close": 20.0},
        "PRODUCT_B": {"Close": 30.0},
        "PRODUCT_C": {"Close": 10.0},
    }


def rotation_definition():
    return {
        "strategy": {"id": "rotation", "name": "Rotation", "version": 1},
        "assets": {
            "required": ["QQQ", "TDF", "BIL", "GLD", "SHY", "KOSPI"],
            "risk": ["QQQ"],
        },
        "target": [{"weights": {
            "QQQ": "0%", "TDF": "20%", "BIL": "80%",
            "GLD": "0%", "SHY": "0%", "KOSPI": "0%",
        }}],
        "rotation": {
            "sleeve": "BIL",
            "check": "monthly",
            "candidates": [
                {"ticker": "GLD", "group": "gold", "asset_class": "GOLD"},
                {"ticker": "SHY", "group": "short", "asset_class": "BOND"},
                {"ticker": "KOSPI", "group": "equity", "asset_class": "EQUITY"},
            ],
            "top_n": 2,
            "max_single_sleeve_share": "50%",
            "max_gold_sleeve_share": "30%",
            "max_equity_sleeve_share": "30%",
        },
        "execution": {"days": 1},
    }


def rotation_market(*, shy_eligible=True, kospi_eligible=False):
    def candidate(roc60, roc120, roc252, volatility, *, eligible=True):
        return {
            "Close": 110.0 if eligible else 90.0,
            "EMA200": 100.0,
            "ROC60": roc60,
            "ROC120": roc120,
            "ROC252": roc252,
            "VOL60": volatility,
        }

    return {
        "QQQ": {"Close": 100.0},
        "TDF": {"Close": 100.0},
        "BIL": {"Close": 100.0, "ROC60": 1.0, "ROC120": 2.0, "ROC252": 3.0},
        "GLD": candidate(8.0, 11.0, 16.0, 0.15),
        "SHY": candidate(5.0, 7.0, 9.0, 0.05, eligible=shy_eligible),
        "KOSPI": candidate(7.0, 9.0, 13.0, 0.20, eligible=kospi_eligible),
    }


class DeclarativeStrategyTests(unittest.TestCase):
    def test_static_retirement_yaml_matches_python_market_state_path(self):
        declarative = DeclarativeStrategy.from_yaml(
            PROJECT_ROOT / "strategies" / "09_static_7030.yaml"
        )
        python_strategy = STATIC_RETIREMENT_7030()
        portfolio = PortfolioStub({"QQQ": 0.70, "BND": 0.15, "BIL": 0.15})
        start = pd.Timestamp("2025-01-02")
        cases = [
            (start, retirement_market("bull")),
            *(
                (start + pd.Timedelta(days=offset), retirement_market("caution"))
                for offset in range(1, 4)
            ),
            *(
                (start + pd.Timedelta(days=offset), retirement_market("bear"))
                for offset in range(4, 14)
            ),
            *(
                (start + pd.Timedelta(days=offset), retirement_market("bull"))
                for offset in range(14, 17)
            ),
        ]

        for index, (date, market_data) in enumerate(cases):
            python_signal = python_strategy.evaluate(date, market_data, portfolio)
            declarative_signal = declarative.evaluate(date, market_data, portfolio)

            self.assertEqual(declarative_signal["target"], python_signal["target"])
            if index:
                self.assertEqual(
                    declarative_signal["rebalance"], python_signal["rebalance"]
                )
            self.assertEqual(declarative.state, python_strategy.state.value)
            self.assertEqual(
                declarative.risk_off_score, python_strategy.risk_off_score
            )
            self.assertEqual(
                declarative.recovery_score, python_strategy.recovery_score
            )

    def test_asymmetric_defense2_yaml_matches_python_path(self):
        declarative = DeclarativeStrategy.from_yaml(
            PROJECT_ROOT / "strategies" / "10_trend_band_defense.yaml"
        )
        python_strategy = ASYMMETRIC_TREND_BAND_ADD_DEFENSE2()
        portfolio = PortfolioStub({"QQQ": 0.65, "GLD": 0.05, "BND": 0.30})

        def observations(close, ema55, ema200, rsi=50.0, disparity=100.0):
            return {
                "QQQ": {
                    "Close": close,
                    "EMA55": ema55,
                    "EMA200": ema200,
                    "RSI14": rsi,
                    "DISPARITY60": disparity,
                },
                "GLD": {"Close": 100.0},
                "BND": {"Close": 100.0},
            }

        cases = [
            (pd.Timestamp("2025-01-02"), observations(100.0, 105.0, 100.0)),
            (pd.Timestamp("2025-01-03"), observations(74.0, 90.0, 100.0)),
            (pd.Timestamp("2025-01-06"), observations(76.0, 101.0, 100.0)),
            (
                pd.Timestamp("2025-01-07"),
                observations(110.0, 105.0, 100.0, rsi=96.0, disparity=110.0),
            ),
        ]

        for date, market_data in cases:
            python_signal = python_strategy.evaluate(date, market_data, portfolio)
            declarative_signal = declarative.evaluate(date, market_data, portfolio)

            self.assertEqual(declarative_signal["target"], python_signal["target"])
            self.assertEqual(
                declarative_signal["rebalance"], python_signal["rebalance"]
            )
            self.assertEqual(declarative_signal["days"], python_signal["days"])
            self.assertEqual(
                declarative.defense_mode == "DEFENSIVE",
                python_strategy.is_defensive_mode,
            )
            self.assertEqual(declarative.peak_price, python_strategy.highest_price)

    def test_profit_band_vxus_yaml_matches_python_transition_path(self):
        declarative = DeclarativeStrategy.from_yaml(
            PROJECT_ROOT / "strategies" / "04_profit_band_vxus.yaml"
        )
        python_strategy = RetirementAllocationProfitBandVXUSStrategy()
        portfolio = PortfolioStub({
            "QQQ": 0.70,
            "BND": 0.30,
            "BIL": 0.0,
            "VXUS": 0.0,
        })
        start = pd.Timestamp("2025-01-02")
        cases = [
            (start, retirement_market("bull")),
            *(
                (start + pd.Timedelta(days=offset), retirement_market("bear"))
                for offset in range(1, 11)
            ),
            *(
                (
                    start + pd.Timedelta(days=offset),
                    retirement_market("bull", bnd_roc=0.50, bil_roc=0.0),
                )
                for offset in range(11, 13)
            ),
        ]

        for index, (date, observations) in enumerate(cases):
            python_signal = python_strategy.evaluate(date, observations, portfolio)
            declarative_signal = declarative.evaluate(date, observations, portfolio)

            self.assertEqual(declarative_signal["target"], python_signal["target"])
            self.assertEqual(declarative_signal["days"], python_signal["days"])
            if index:
                self.assertEqual(
                    declarative_signal["rebalance"], python_signal["rebalance"]
                )
            self.assertEqual(declarative.state, python_strategy.state.value)
            self.assertEqual(declarative.safe_asset, python_strategy.safe_asset)

    def test_profit_band_vxus_yaml_preserves_qqq_during_safe_rotation(self):
        declarative = DeclarativeStrategy.from_yaml(
            PROJECT_ROOT / "strategies" / "04_profit_band_vxus.yaml"
        )
        python_strategy = RetirementAllocationProfitBandVXUSStrategy()
        portfolio = PortfolioStub({
            "QQQ": 0.74,
            "BND": 0.26,
            "BIL": 0.0,
            "VXUS": 0.0,
        })
        start = pd.Timestamp("2025-01-02")

        for date, observations in [
            (start, retirement_market("bull")),
            (
                pd.Timestamp("2025-02-03"),
                retirement_market("bull", bnd_roc=0.0, bil_roc=1.0),
            ),
        ]:
            python_signal = python_strategy.evaluate(date, observations, portfolio)
            declarative_signal = declarative.evaluate(date, observations, portfolio)
            self.assertEqual(declarative_signal["target"], python_signal["target"])
            self.assertEqual(
                declarative_signal["rebalance"], python_signal["rebalance"]
            )
            self.assertEqual(declarative_signal["days"], python_signal["days"])

    def test_profit_band_yaml_preserves_qqq_and_rotates_only_safe_sleeve(self):
        declarative = DeclarativeStrategy.from_yaml(
            PROJECT_ROOT / "strategies" / "03_profit_band.yaml"
        )
        python_strategy = RetirementAllocationProfitBandStrategy()
        portfolio = PortfolioStub({"QQQ": 0.74, "BND": 0.26, "BIL": 0.0})
        start = pd.Timestamp("2025-01-02")

        first_python = python_strategy.evaluate(
            start, retirement_market("bull"), portfolio
        )
        first_yaml = declarative.evaluate(
            start, retirement_market("bull"), portfolio
        )
        rotation_market = retirement_market(
            "bull", bnd_roc=0.0, bil_roc=1.0
        )
        second_python = python_strategy.evaluate(
            pd.Timestamp("2025-02-03"), rotation_market, portfolio
        )
        second_yaml = declarative.evaluate(
            pd.Timestamp("2025-02-03"), rotation_market, portfolio
        )

        self.assertEqual(first_yaml["target"], first_python["target"])
        self.assertFalse(first_yaml["rebalance"])
        self.assertEqual(second_yaml["target"], second_python["target"])
        self.assertEqual(second_yaml["target"], {
            "QQQ": 0.74,
            "BND": 0.0,
            "BIL": 0.26,
        })
        self.assertTrue(second_yaml["rebalance"])
        self.assertEqual(second_yaml["days"], 1)

    def test_retirement_vxus_yaml_matches_python_transition_path(self):
        declarative = DeclarativeStrategy.from_yaml(
            PROJECT_ROOT / "strategies" / "02_allocation_vxus.yaml"
        )
        python_strategy = RetirementAllocationVXUSStrategy()
        portfolio = PortfolioStub({
            "QQQ": 0.70,
            "BND": 0.30,
            "BIL": 0.0,
            "VXUS": 0.0,
        })
        start = pd.Timestamp("2025-01-02")
        cases = [
            (start, retirement_market("bull")),
            *(
                (start + pd.Timedelta(days=offset), retirement_market("bear"))
                for offset in range(1, 11)
            ),
            *(
                (
                    start + pd.Timedelta(days=offset),
                    retirement_market("bull", bnd_roc=0.50, bil_roc=0.0),
                )
                for offset in range(11, 13)
            ),
        ]
        for index, (date, observations) in enumerate(cases):
            python_signal = python_strategy.evaluate(date, observations, portfolio)
            declarative_signal = declarative.evaluate(date, observations, portfolio)

            self.assertEqual(declarative_signal["target"], python_signal["target"])
            self.assertEqual(declarative_signal["days"], python_signal["days"])
            if index:
                self.assertEqual(
                    declarative_signal["rebalance"], python_signal["rebalance"]
                )
            self.assertEqual(declarative.state, python_strategy.state.value)
            self.assertEqual(declarative.safe_asset, python_strategy.safe_asset)

    def test_market_mode_is_representative_state_regardless_of_yaml_order(self):
        strategy = DeclarativeStrategy(definition(
            state={
                "safe_asset": {"initial": "BND"},
                "market_mode": {
                    "initial": "BULL",
                    "rules": [
                        {"when": "QQQ.close < QQQ.ema200", "set": "BEAR"},
                        {"otherwise": True, "set": "BULL"},
                    ],
                },
            },
        ))

        strategy.evaluate(
            pd.Timestamp("2025-01-02"),
            market(close=90.0),
            PortfolioStub(),
        )

        self.assertEqual(strategy.safe_asset, "BND")
        self.assertEqual(strategy.market_mode, "BEAR")
        self.assertEqual(strategy.state, "BEAR")

    def test_market_mode_rejects_unsupported_state(self):
        with self.assertRaisesRegex(
            StrategyDefinitionError, "state.market_mode.initial"
        ):
            DeclarativeStrategy(definition(state={
                "market_mode": {"initial": "SIDEWAYS"},
            }))

    def test_uninitialized_market_mode_must_be_resolved_on_first_evaluation(self):
        strategy = DeclarativeStrategy(definition(state={
            "market_mode": {"initial": "UNINITIALIZED"},
        }))

        with self.assertRaisesRegex(
            StrategyDefinitionError, "state.market_mode must be one of"
        ):
            strategy.evaluate(
                pd.Timestamp("2025-01-02"), market(), PortfolioStub()
            )

    def test_market_indicators_are_discovered_and_prepared_before_evaluation(self):
        strategy = DeclarativeStrategy({
            "strategy": {"id": "prepared", "name": "Prepared", "version": 1},
            "assets": {"required": ["QQQ"], "risk": ["QQQ"]},
            "variables": {"positive_momentum": "QQQ.roc40 > 0"},
            "target": [{"weights": {"QQQ": "100%"}}],
        })
        self.assertEqual(
            strategy.required_market_fields,
            {"QQQ": ("CLOSE", "ROC40")},
        )

        with TemporaryDirectory() as directory:
            dates = pd.bdate_range("2024-01-02", periods=60)
            prices = pd.Series(range(100, 160), index=dates, dtype=float)
            pd.DataFrame({
                "Open": prices,
                "High": prices + 1,
                "Low": prices - 1,
                "Close": prices,
                "Volume": 1000,
            }).rename_axis("Date").to_csv(Path(directory) / "QQQ.csv")

            backtest = Backtest(
                strategy,
                data_dir=Path(directory),
                tickers=("QQQ",),
            )

        self.assertFalse(backtest.data["QQQ_ROC40"].isna().any())
        self.assertEqual(len(backtest.data), 20)

    def test_observation_asset_is_available_to_rules_but_not_target(self):
        strategy = DeclarativeStrategy({
            "strategy": {"id": "observed", "name": "Observed", "version": 1},
            "assets": {
                "required": ["QQQ", "BND"],
                "observations": ["SPY"],
                "risk": ["QQQ"],
            },
            "variables": {"broad_market_weak": "SPY.close < SPY.ema20"},
            "target": [
                {
                    "when": "variables.broad_market_weak",
                    "weights": {"QQQ": "50%", "BND": "50%"},
                },
                {"weights": {"QQQ": "70%", "BND": "30%"}},
            ],
        })
        observed_market = {
            "QQQ": {"Close": 100.0},
            "BND": {"Close": 100.0},
            "SPY": {"Close": 90.0, "EMA20": 100.0},
        }

        result = strategy.evaluate(pd.Timestamp("2025-01-02"), observed_market, PortfolioStub())

        self.assertEqual(strategy.holding_tickers, ("QQQ", "BND"))
        self.assertEqual(strategy.observation_tickers, ("SPY",))
        self.assertEqual(strategy.required_tickers, ("QQQ", "BND", "SPY"))
        self.assertEqual(result["target"], {"QQQ": 0.5, "BND": 0.5})
        self.assertNotIn("SPY", result["target"])

    def test_retirement_allocation_yaml_matches_python_transition_path(self):
        declarative = DeclarativeStrategy.from_yaml(
            PROJECT_ROOT / "strategies" / "01_allocation.yaml"
        )
        python_strategy = RetirementAllocationStrategy()
        portfolio = PortfolioStub({"QQQ": 0.70, "BND": 0.30, "BIL": 0.0})
        cases = [
            (pd.Timestamp("2025-01-02"), retirement_market("bull")),
            *(
                (pd.Timestamp("2025-01-02") + pd.Timedelta(days=offset),
                 retirement_market("caution"))
                for offset in range(1, 4)
            ),
            *(
                (pd.Timestamp("2025-01-05") + pd.Timedelta(days=offset),
                 retirement_market("bull"))
                for offset in range(1, 4)
            ),
            (
                pd.Timestamp("2025-02-03"),
                retirement_market("bull", bnd_roc=0.0, bil_roc=1.0),
            ),
            *(
                (pd.Timestamp("2025-02-03") + pd.Timedelta(days=offset),
                 retirement_market("bear", bnd_roc=0.0, bil_roc=1.0))
                for offset in range(1, 11)
            ),
            *(
                (pd.Timestamp("2025-02-13") + pd.Timedelta(days=offset),
                 retirement_market("bull", bnd_roc=0.0, bil_roc=1.0))
                for offset in range(1, 3)
            ),
        ]

        for index, (date, observations) in enumerate(cases):
            python_signal = python_strategy.evaluate(date, observations, portfolio)
            declarative_signal = declarative.evaluate(date, observations, portfolio)
            self.assertEqual(declarative_signal["target"], python_signal["target"])
            self.assertEqual(declarative_signal["days"], python_signal["days"])
            if index:
                self.assertEqual(
                    declarative_signal["rebalance"], python_signal["rebalance"]
                )
                if (
                    declarative_signal["rebalance"]
                    and python_signal["reason"]
                    and python_signal["reason"].split("->", 1)[0]
                    in {"BULL", "CAUTION", "BEAR", "RECOVERY"}
                ):
                    self.assertEqual(
                        declarative_signal["reason"].split("(", 1)[0],
                        python_signal["reason"].split("(", 1)[0],
                    )
            self.assertEqual(declarative.state, python_strategy.state.value)
            self.assertEqual(declarative.safe_asset, python_strategy.safe_asset)
            self.assertEqual(
                declarative.risk_off_score, python_strategy.risk_off_score
            )
            self.assertEqual(
                declarative.recovery_score, python_strategy.recovery_score
            )

    def test_example_matches_existing_fixed_band_strategy(self):
        declarative = DeclarativeStrategy.from_yaml(
            PROJECT_ROOT / "strategies" / "07_band_7030_bnd.yaml"
        )
        python_strategy = RETIREMENT_7030_BAND()
        portfolio = PortfolioStub({"QQQ": 0.76, "BND": 0.24})
        date = pd.Timestamp("2025-01-02")

        first = declarative.evaluate(date, market(), portfolio)
        declarative.evaluate(date + pd.Timedelta(days=1), market(), portfolio)
        second = declarative.evaluate(date + pd.Timedelta(days=2), market(), portfolio)
        expected = python_strategy.evaluate(date, market(), portfolio)

        self.assertEqual(first["target"], expected["target"])
        self.assertFalse(first["rebalance"])
        self.assertTrue(second["rebalance"])
        self.assertEqual(declarative.required_tickers, ("QQQ", "BND"))
        self.assertEqual(declarative.risk_asset_tickers, ("QQQ",))

    def test_arbitrary_state_name_and_changed_function(self):
        strategy = DeclarativeStrategy(definition(
            state={
                "risk_level": {
                    "initial": "70%",
                    "rules": [
                        {"when": "QQQ.drawdown120 <= -20%", "set": "20%"},
                        {"when": "QQQ.drawdown120 <= -10%", "set": "40%"},
                        {"otherwise": True, "set": "70%"},
                    ],
                }
            },
            target=[{"weights": {
                "QQQ": "state.risk_level",
                "BND": "1 - state.risk_level",
            }}],
            rebalance=[{"when": "changed(state.risk_level)"}],
        ))
        portfolio = PortfolioStub({"QQQ": 0.70, "BND": 0.30})
        start = pd.Timestamp("2025-01-02")

        first = strategy.evaluate(start, market(drawdown=-0.05), portfolio)
        second = strategy.evaluate(
            start + pd.Timedelta(days=1), market(drawdown=-0.12), portfolio
        )

        self.assertFalse(first["rebalance"])
        self.assertTrue(second["rebalance"])
        self.assertEqual(second["target"], {"QQQ": 0.40, "BND": 0.60})
        self.assertEqual(strategy.state, 0.40)

    def test_confirmation_changes_state_only_after_required_days(self):
        strategy = DeclarativeStrategy(definition(
            state={
                "mode": {
                    "initial": "normal",
                    "rules": [
                        {
                            "when": "QQQ.close < QQQ.ema200",
                            "set": "defensive",
                            "confirm": 2,
                        }
                    ],
                }
            },
            target=[
                {
                    "when": "state.mode == 'defensive'",
                    "weights": {"QQQ": "20%", "BND": "80%"},
                },
                {"weights": {"QQQ": "70%", "BND": "30%"}},
            ],
            rebalance=[{"when": "changed(state.mode)"}],
        ))
        portfolio = PortfolioStub({"QQQ": 0.70, "BND": 0.30})
        date = pd.Timestamp("2025-01-02")

        first = strategy.evaluate(date, market(close=90.0), portfolio)
        second = strategy.evaluate(date + pd.Timedelta(days=1), market(close=90.0), portfolio)

        self.assertEqual(first["target"]["QQQ"], 0.70)
        self.assertEqual(second["target"]["QQQ"], 0.20)
        self.assertTrue(second["rebalance"])

    def test_custom_operator_is_available_without_new_dsl_keyword(self):
        registry = OperatorRegistry()
        registry.register("double", lambda value: value * 2)
        strategy = DeclarativeStrategy(definition(
            variables={"risk_weight": "double(20%)"},
            target=[{"weights": {
                "QQQ": "variables.risk_weight",
                "BND": "1 - variables.risk_weight",
            }}],
        ), operators=registry)

        signal = strategy.evaluate(
            pd.Timestamp("2025-01-02"), market(), PortfolioStub()
        )

        self.assertEqual(signal["target"], {"QQQ": 0.40, "BND": 0.60})

    def test_python_execution_syntax_is_rejected(self):
        strategy = DeclarativeStrategy(definition(
            variables={"bad": "__import__('os').getcwd()"},
        ))
        with self.assertRaises(StrategyExpressionError):
            strategy.evaluate(pd.Timestamp("2025-01-02"), market(), PortfolioStub())

    def test_explicit_identity_is_stable_across_instances(self):
        first = DeclarativeStrategy(definition())
        second = DeclarativeStrategy(definition())
        self.assertEqual(strategy_identity(first), "dsl:test")
        self.assertEqual(strategy_identity(first), strategy_identity(second))
        StrategyEngine(first)

    def test_directory_loader_skips_disabled_strategies(self):
        source = (PROJECT_ROOT / "strategies" / "01_allocation.yaml").read_text(
            encoding="utf-8"
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "strategy.yaml"
            path.write_text(source, encoding="utf-8")
            self.assertEqual(load_strategy_directory(directory), [])
            self.assertEqual(len(load_strategy_directory(directory, enabled_only=False)), 1)

    def test_definition_requires_explicit_assets(self):
        invalid = definition()
        invalid["assets"] = {}
        with self.assertRaises(StrategyDefinitionError):
            DeclarativeStrategy(invalid)

    def test_target_always_requires_a_weights_rule_list(self):
        invalid = definition()
        invalid["target"] = {"QQQ": "70%", "BND": "30%"}
        with self.assertRaisesRegex(
            StrategyDefinitionError, "target must be a non-empty rule list"
        ):
            DeclarativeStrategy(invalid)

    def test_undocumented_special_weight_names_are_not_supported(self):
        invalid = definition()
        invalid["target"] = [{
            "weights": {"QQQ": "70%", "BND": "remaining"}
        }]
        strategy = DeclarativeStrategy(invalid)
        with self.assertRaisesRegex(StrategyExpressionError, "unknown name"):
            strategy.evaluate(
                pd.Timestamp("2025-01-02"), market(), PortfolioStub()
            )

    def test_execution_uses_only_the_documented_days_key(self):
        invalid = definition()
        invalid["execution"] = 3
        with self.assertRaisesRegex(
            StrategyDefinitionError, "execution must be a mapping"
        ):
            DeclarativeStrategy(invalid)

    def test_rotation_replaces_only_the_bil_sleeve_and_records_the_reason(self):
        strategy = DeclarativeStrategy(rotation_definition())

        signal = strategy.evaluate(
            pd.Timestamp("2025-01-02"), rotation_market(), PortfolioStub()
        )

        self.assertTrue(signal["rebalance"])
        self.assertEqual(signal["target"]["QQQ"], 0.0)
        self.assertEqual(signal["target"]["TDF"], 0.20)
        self.assertAlmostEqual(signal["target"]["GLD"], 0.20)
        self.assertAlmostEqual(signal["target"]["SHY"], 0.40)
        self.assertAlmostEqual(signal["target"]["BIL"], 0.20)
        self.assertEqual(strategy.rotation_decision["selected"], ("GLD", "SHY"))
        self.assertEqual(
            strategy.rotation_decision["candidates"]["GLD"]["selection_status"],
            "선택",
        )
        self.assertFalse(strategy.rotation_decision["candidates"]["KOSPI"]["eligible"])
        self.assertIn("자동 자산 교체", signal["reason"])

    def test_rotation_reviews_monthly_and_forces_a_trade_when_selection_changes(self):
        strategy = DeclarativeStrategy(rotation_definition())
        portfolio = PortfolioStub()
        strategy.evaluate(pd.Timestamp("2025-01-02"), rotation_market(), portfolio)

        unchanged = strategy.evaluate(
            pd.Timestamp("2025-01-03"), rotation_market(), portfolio
        )
        changed = strategy.evaluate(
            pd.Timestamp("2025-02-03"),
            rotation_market(shy_eligible=False, kospi_eligible=True),
            portfolio,
        )

        self.assertFalse(unchanged["rebalance"])
        self.assertIsNone(unchanged["reason"])
        self.assertTrue(changed["rebalance"])
        self.assertEqual(changed["target"]["QQQ"], 0.0)
        self.assertEqual(changed["target"]["TDF"], 0.20)
        self.assertAlmostEqual(changed["target"]["GLD"], 0.24)
        self.assertAlmostEqual(changed["target"]["KOSPI"], 0.24)
        self.assertAlmostEqual(changed["target"]["BIL"], 0.32)
        self.assertEqual(strategy.rotation_decision["previous_selected"], ("GLD", "SHY"))
        self.assertEqual(strategy.rotation_decision["selected"], ("GLD", "KOSPI"))

    def test_rotation_keeps_incumbent_when_challenger_advantage_is_small(self):
        configured = rotation_definition()
        configured["rotation"]["switch_score_margin"] = 3.0
        strategy = DeclarativeStrategy(configured)
        portfolio = PortfolioStub()
        strategy.evaluate(pd.Timestamp("2025-01-02"), rotation_market(), portfolio)
        next_market = rotation_market(kospi_eligible=True)
        next_market["KOSPI"].update({"ROC60": 6.0, "ROC120": 8.0, "ROC252": 10.0})

        signal = strategy.evaluate(pd.Timestamp("2025-02-03"), next_market, portfolio)

        self.assertEqual(strategy.rotation_decision["selected"], ("GLD", "SHY"))
        self.assertFalse(signal["rebalance"])

    def test_rotation_selection_sleeps_instead_of_resetting_with_zero_sleeve(self):
        strategy = DeclarativeStrategy(rotation_definition())
        selected_target = strategy._target_weights(rotation_market(), PortfolioStub())
        strategy._apply_rotation(
            pd.Timestamp("2025-01-02"), selected_target, rotation_market()
        )
        dormant_target = {ticker: 0.0 for ticker in strategy.holding_tickers}
        dormant_target["QQQ"] = 1.0

        strategy._apply_rotation(
            pd.Timestamp("2025-01-03"), dormant_target, rotation_market()
        )

        self.assertEqual(strategy._rotation_selected, ("GLD", "SHY"))
        self.assertIsNotNone(strategy._rotation_last_period)

    def test_rotation_candidate_must_be_a_required_holding(self):
        invalid = rotation_definition()
        invalid["rotation"]["candidates"][0]["ticker"] = "MISSING"

        with self.assertRaisesRegex(StrategyDefinitionError, "must be in assets.required"):
            DeclarativeStrategy(invalid)

    def test_product_mapping_splits_one_source_asset_across_products(self):
        source = DeclarativeStrategy(definition(
            rebalance=[{"when": "target_deviation() >= 5%"}]
        ))
        strategy = ProductMappedStrategy(
            product_definition(), source
        )
        portfolio = PortfolioStub({
            "PRODUCT_A": 0.46,
            "PRODUCT_B": 0.30,
            "PRODUCT_C": 0.24,
        })

        strategy.evaluate(
            pd.Timestamp("2025-01-02"), product_market(), portfolio
        )
        signal = strategy.evaluate(
            pd.Timestamp("2025-01-03"), product_market(), portfolio
        )

        self.assertTrue(signal["rebalance"])
        self.assertEqual(signal["target"], {
            "PRODUCT_A": 0.42,
            "PRODUCT_B": 0.28,
            "PRODUCT_C": 0.30,
        })
        self.assertEqual(
            strategy.required_tickers,
            ("QQQ", "BND", "PRODUCT_A", "PRODUCT_B", "PRODUCT_C"),
        )
        self.assertEqual(
            strategy.risk_asset_tickers, ("PRODUCT_A", "PRODUCT_B")
        )

    def test_product_mapping_defers_risk_sleeve_rebalancing_above_70_percent(self):
        class SourceStrategy:
            required_tickers = ("QQQ", "BND")
            holding_tickers = required_tickers
            risk_asset_tickers = ("QQQ",)
            parameters = {"canonical_risk_weight": 0.70}

            def evaluate(self, date, market, portfolio):
                return {
                    "rebalance": True,
                    "target": {"QQQ": 0.76, "BND": 0.24},
                    "days": 1,
                }

        configured = product_definition(products={
            "QQQ": {"PRODUCT_A": "50%", "PRODUCT_B": "50%"},
            "BND": {"PRODUCT_C": "100%"},
        })
        strategy = ProductMappedStrategy(configured, SourceStrategy())
        portfolio = PortfolioStub({
            "PRODUCT_A": 0.50,
            "PRODUCT_B": 0.26,
            "PRODUCT_C": 0.24,
        })

        signal = strategy.evaluate(
            pd.Timestamp("2025-01-02"), product_market(), portfolio
        )

        self.assertEqual(signal["target"], {
            "PRODUCT_A": 0.50,
            "PRODUCT_B": 0.26,
            "PRODUCT_C": 0.24,
        })

    def test_product_mapping_restores_configured_mix_at_70_percent(self):
        class SourceStrategy:
            required_tickers = ("QQQ", "BND")
            holding_tickers = required_tickers
            risk_asset_tickers = ("QQQ",)
            parameters = {"canonical_risk_weight": 0.70}

            def evaluate(self, date, market, portfolio):
                return {
                    "rebalance": True,
                    "target": {"QQQ": 0.70, "BND": 0.30},
                    "days": 1,
                }

        configured = product_definition(products={
            "QQQ": {"PRODUCT_A": "50%", "PRODUCT_B": "50%"},
            "BND": {"PRODUCT_C": "100%"},
        })
        strategy = ProductMappedStrategy(configured, SourceStrategy())
        portfolio = PortfolioStub({
            "PRODUCT_A": 0.50,
            "PRODUCT_B": 0.26,
            "PRODUCT_C": 0.24,
        })

        signal = strategy.evaluate(
            pd.Timestamp("2025-01-02"), product_market(), portfolio
        )

        self.assertEqual(signal["target"], {
            "PRODUCT_A": 0.35,
            "PRODUCT_B": 0.35,
            "PRODUCT_C": 0.30,
        })

    def test_state_check_evaluates_only_once_per_period(self):
        strategy = DeclarativeStrategy(definition(
            state={
                "safe_asset": {
                    "initial": "BND",
                    "check": "monthly",
                    "rules": [
                        {"when": "QQQ.close < QQQ.ema200", "set": "BIL"},
                        {"otherwise": True, "set": "BND"},
                    ],
                }
            },
            target=[
                {
                    "when": "state.safe_asset == 'BIL'",
                    "weights": {"QQQ": "70%", "BND": "30%"},
                },
                {"weights": {"QQQ": "70%", "BND": "30%"}},
            ],
        ))
        portfolio = PortfolioStub()

        strategy.evaluate(pd.Timestamp("2025-01-02"), market(close=100.0), portfolio)
        strategy.evaluate(pd.Timestamp("2025-01-03"), market(close=90.0), portfolio)
        self.assertEqual(strategy._state_values["safe_asset"], "BND")

        strategy.evaluate(pd.Timestamp("2025-02-03"), market(close=90.0), portfolio)
        self.assertEqual(strategy._state_values["safe_asset"], "BIL")

    def test_ordered_rebalance_rules_use_first_match_and_rule_days(self):
        strategy = DeclarativeStrategy(definition(
            state={
                "mode": {
                    "initial": "normal",
                    "rules": [
                        {"when": "QQQ.close < QQQ.ema200", "set": "defensive"},
                        {"otherwise": True, "set": "normal"},
                    ],
                }
            },
            rebalance=[
                {"when": "changed(state.mode)", "days": 3},
                {
                    "check": "monthly",
                    "when": "target_deviation() >= 5%",
                    "days": 1,
                },
            ],
        ))
        portfolio = PortfolioStub({"QQQ": 0.60, "BND": 0.40})
        start = pd.Timestamp("2025-01-02")

        strategy.evaluate(start, market(close=100.0), portfolio)
        signal = strategy.evaluate(start + pd.Timedelta(days=1), market(close=90.0), portfolio)

        self.assertTrue(signal["rebalance"])
        self.assertEqual(signal["days"], 3)
        self.assertEqual(signal["reason"], "normal->defensive")

    def test_korean_product_mapping_selects_fx_rate_in_python(self):
        configured = product_definition(products={
            "QQQ": {"379810.KS": "100%"},
            "BND": {"BND": "100%"},
        })
        strategy = ProductMappedStrategy(configured, DeclarativeStrategy(definition()))

        self.assertEqual(strategy.FX_RATE_TICKER, "KRW=X")

    def test_product_mapping_requires_each_source_share_to_sum_to_100_percent(self):
        invalid = product_definition(products={
            "QQQ": {"PRODUCT_A": "60%", "PRODUCT_B": "30%"}
        })
        with self.assertRaisesRegex(StrategyDefinitionError, "sum to 100%"):
            ProductMappedStrategy(invalid, DeclarativeStrategy(definition()))

    def test_directory_loader_resolves_a_local_source_strategy(self):
        source = """
strategy:
  id: base
  name: Base
  version: 1
  enabled: false
assets:
  required: [QQQ, BND]
  risk: [QQQ]
target:
  - weights: {QQQ: 70%, BND: 30%}
"""
        products = """
strategy:
  id: mapped
  name: Mapped
  version: 1
source: base
products:
  QQQ: {PRODUCT_A: 50%, PRODUCT_B: 50%}
  BND: {PRODUCT_C: 100%}
"""
        with TemporaryDirectory() as directory:
            Path(directory, "base.yaml").write_text(source, encoding="utf-8")
            Path(directory, "mapped.yaml").write_text(products, encoding="utf-8")
            loaded = load_strategy_directory(directory)

        self.assertEqual(len(loaded), 1)
        self.assertIsInstance(loaded[0], ProductMappedStrategy)
        self.assertEqual(loaded[0].strategy_id, "dsl:mapped")


if __name__ == "__main__":
    unittest.main()

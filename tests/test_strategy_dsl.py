from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from strategy import RETIREMENT_7030_BAND
from strategy_domain import StrategyEngine, strategy_identity
from strategy_dsl import (
    DeclarativeStrategy,
    OperatorRegistry,
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
        "target": {"QQQ": "70%", "BND": "30%"},
        "execution": {"days": 1},
    }
    value.update(overrides)
    return value


def market(close=100.0, drawdown=-0.05):
    return {
        "QQQ": {"Close": close, "EMA200": 95.0, "DRAWDOWN120": drawdown},
        "BND": {"Close": 10.0},
    }


class DeclarativeStrategyTests(unittest.TestCase):
    def test_example_matches_existing_fixed_band_strategy(self):
        declarative = DeclarativeStrategy.from_yaml(
            PROJECT_ROOT / "strategies" / "retirement_7030_band.yaml"
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
            target={
                "QQQ": "state.risk_level",
                "BND": "remaining",
            },
            rebalance={"when": "changed(state.risk_level)"},
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
        self.assertEqual(strategy.state, "40%")

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
                {"otherwise": True, "weights": {"QQQ": "70%", "BND": "30%"}},
            ],
            rebalance={"when": "changed(state.mode)"},
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
            target={"QQQ": "variables.risk_weight", "BND": "remaining"},
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
        source = (PROJECT_ROOT / "strategies" / "retirement_7030_band.yaml").read_text(
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


if __name__ == "__main__":
    unittest.main()

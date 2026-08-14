import sys
import threading
import unittest
from pathlib import Path

import pandas as pd


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from strategy import AllocationState, RetirementAllocationStrategy  # noqa: E402
from backtest import Backtest  # noqa: E402
from strategy_domain import (  # noqa: E402
    MarketSnapshot,
    StrategyEngine,
    StrategyEvaluation,
    StrategyIsolationError,
    StrategyRuntimeState,
)


class StablePortfolio:
    def weights(self, prices):
        return {ticker: 0.0 for ticker in prices}


def bull_market():
    return {
        "QQQ": {
            "Close": 110.0,
            "EMA20": 105.0,
            "EMA55": 100.0,
            "EMA200": 95.0,
            "ROC5": 2.0,
            "ROC20": 5.0,
            "ROC40": 4.0,
            "ROC60": 5.0,
            "ROC120": 8.0,
            "ROC252": 12.0,
            "EMA20_SLOPE5": 1.0,
            "EMA200_SLOPE20": 0.5,
            "DRAWDOWN120": 0.0,
            "RSI14": 55.0,
            "VOL60": 0.15,
        },
        "BND": {"Close": 100.0, "ROC40": 1.0},
        "BIL": {"Close": 100.0, "ROC40": 0.0},
        "GLD": {"Close": 100.0},
    }


class MarketSnapshotTests(unittest.TestCase):
    def test_snapshot_isolated_from_source_and_returned_mappings(self):
        source = bull_market()
        snapshot = MarketSnapshot(pd.Timestamp("2024-01-02"), source)

        source["QQQ"]["Close"] = 1.0
        returned = snapshot.assets
        returned["QQQ"]["Close"] = 2.0

        self.assertEqual(snapshot.assets["QQQ"]["Close"], 110.0)
        self.assertEqual(snapshot.to_dict()["as_of"], "2024-01-02T00:00:00")


class StrategyEngineTests(unittest.TestCase):
    def setUp(self):
        self.strategy = RetirementAllocationStrategy()
        self.engine = StrategyEngine(self.strategy)
        self.market = MarketSnapshot(
            pd.Timestamp("2024-01-02"), bull_market()
        )
        self.portfolio = StablePortfolio()

    def test_isolated_evaluation_is_deterministic_and_does_not_mutate_template(self):
        initial = self.engine.initial_runtime_state

        first = self.engine.evaluate(self.market, self.portfolio, initial)
        second = self.engine.evaluate(self.market, self.portfolio, initial)

        self.assertIsNone(self.strategy.state)
        self.assertEqual(first.evaluation.to_dict(), second.evaluation.to_dict())
        self.assertEqual(
            first.next_runtime_state.values["state"], AllocationState.BULL
        )
        self.assertEqual(
            second.next_runtime_state.values["target"],
            {"QQQ": 0.70, "BND": 0.30, "BIL": 0.0},
        )

    def test_advance_updates_owned_strategy_and_returns_explicit_contracts(self):
        step = self.engine.advance(self.market, self.portfolio)

        self.assertIsInstance(step.evaluation, StrategyEvaluation)
        self.assertIsInstance(step.next_runtime_state, StrategyRuntimeState)
        self.assertEqual(self.strategy.state, AllocationState.BULL)
        self.assertTrue(step.evaluation.rebalance_required)
        self.assertEqual(step.evaluation.previous_state, None)
        self.assertEqual(step.evaluation.next_state, "BULL")
        self.assertEqual(step.evaluation.reason, "INITIAL")
        self.assertEqual(
            step.evaluation.target_weights,
            {"QQQ": 0.70, "BND": 0.30, "BIL": 0.0},
        )

    def test_runtime_state_returns_copies(self):
        state = self.engine.initial_runtime_state
        values = state.values
        values["risk_off_score"] = 999

        self.assertEqual(state.values["risk_off_score"], 0)

    def test_runtime_state_cannot_be_applied_to_another_strategy_identity(self):
        class OtherStrategy(RetirementAllocationStrategy):
            pass

        state = self.engine.initial_runtime_state

        with self.assertRaisesRegex(ValueError, "runtime state belongs to"):
            state.apply_to(OtherStrategy())

    def test_runtime_state_rejects_a_different_strategy_version(self):
        state = self.engine.initial_runtime_state
        incompatible = RetirementAllocationStrategy()
        incompatible.STRATEGY_VERSION = "2"

        with self.assertRaisesRegex(ValueError, "version"):
            state.apply_to(incompatible)

    def test_noncopyable_legacy_strategy_keeps_dict_interface_in_sequential_mode(self):
        class ExistingThirdPartyStrategy:
            def __init__(self):
                self.lock = threading.Lock()
                self.state = "READY"
                self.calls = 0

            def evaluate(self, date, market, portfolio):
                self.calls += 1
                return {
                    "rebalance": True,
                    "target": {"QQQ": 1.0},
                    "days": 2,
                    "reason": "EXTERNAL_RULE",
                    "custom_metadata": "preserved-by-strategy",
                }

        strategy = ExistingThirdPartyStrategy()
        engine = StrategyEngine(strategy)

        step = engine.advance(self.market, self.portfolio)

        self.assertEqual(strategy.calls, 1)
        self.assertTrue(step.evaluation.rebalance_required)
        self.assertEqual(step.evaluation.target_weights, {"QQQ": 1.0})
        self.assertEqual(step.evaluation.execution_days, 2)
        self.assertIsNone(step.next_runtime_state)
        with self.assertRaises(StrategyIsolationError):
            engine.evaluate(self.market, self.portfolio)


class LegacyBacktestCompatibilityTests(unittest.TestCase):
    def test_existing_non_base_strategy_runs_without_interface_changes(self):
        class ExistingStrategy:
            def __init__(self):
                self.lock = threading.Lock()
                self.state = "CUSTOM"
                self.calls = 0

            def evaluate(self, date, market, portfolio):
                self.calls += 1
                return {
                    "rebalance": self.calls == 1,
                    "target": {"QQQ": 1.0},
                    "days": 1,
                    "reason": "LEGACY_DICT_SIGNAL",
                }

        class InMemoryBacktest(Backtest):
            def load_data(self):
                dates = pd.bdate_range("2024-01-02", periods=3)
                return pd.DataFrame({
                    "QQQ_Open": [100.0, 101.0, 102.0],
                    "QQQ_Close": [100.0, 101.0, 102.0],
                }, index=dates)

        strategy = ExistingStrategy()
        backtest = InMemoryBacktest(strategy, tickers=("QQQ",))

        history, trades, rebalances = backtest.run_all()

        self.assertEqual(strategy.calls, 3)
        self.assertEqual(len(history), 3)
        self.assertFalse(trades.empty)
        self.assertEqual(rebalances[0]["Target"], {"QQQ": 1.0})
        self.assertEqual(rebalances[0]["Reason"], "LEGACY_DICT_SIGNAL")
        self.assertIsInstance(backtest.last_evaluation, StrategyEvaluation)


if __name__ == "__main__":
    unittest.main()

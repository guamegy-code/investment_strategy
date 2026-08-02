import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from backtest import Backtest
from portfolio import Portfolio
from strategy import AllocationState


class DummyStrategy:
    state = AllocationState.BULL


class ExecutionDelayTests(unittest.TestCase):
    def backtest_shell(self, delay=0, bear_delay=None):
        backtest = Backtest.__new__(Backtest)
        backtest.strategy = DummyStrategy()
        backtest.signal_delay_days = delay
        backtest.bear_signal_delay_days = bear_delay
        backtest.delayed_rebalance = None
        backtest.portfolio = Portfolio()
        return backtest

    @staticmethod
    def signal():
        return {
            "target": {"QQQ": 1.0},
            "days": 1,
            "reason": "TEST",
        }

    def test_one_day_extra_delay_activates_on_second_open(self):
        backtest = self.backtest_shell(delay=1)
        date = pd.Timestamp("2024-01-02")
        backtest._queue_rebalance(self.signal(), date)
        backtest._activate_delayed_rebalance(date + pd.Timedelta(days=1))
        self.assertIsNone(backtest.portfolio.pending_target)
        backtest._activate_delayed_rebalance(date + pd.Timedelta(days=2))
        self.assertEqual(backtest.portfolio.pending_target, {"QQQ": 1.0})

    def test_bear_specific_delay_overrides_general_delay(self):
        backtest = self.backtest_shell(delay=0, bear_delay=2)
        backtest.strategy.state = AllocationState.BEAR
        backtest._queue_rebalance(self.signal(), pd.Timestamp("2024-01-02"))
        self.assertEqual(backtest.delayed_rebalance["remaining"], 2)

    def test_new_signal_supersedes_unstarted_order(self):
        backtest = self.backtest_shell(delay=2)
        date = pd.Timestamp("2024-01-02")
        backtest._queue_rebalance(self.signal(), date)
        replacement = self.signal()
        replacement["target"] = {"QQQ": 0.5}
        backtest._queue_rebalance(replacement, date + pd.Timedelta(days=1))
        self.assertEqual(
            backtest.delayed_rebalance["target"], {"QQQ": 0.5}
        )

    def test_immediate_signal_cancels_older_delayed_order(self):
        backtest = self.backtest_shell(delay=2)
        date = pd.Timestamp("2024-01-02")
        backtest._queue_rebalance(self.signal(), date)
        backtest.signal_delay_days = 0
        replacement = self.signal()
        replacement["target"] = {"QQQ": 0.5}
        backtest._queue_rebalance(replacement, date + pd.Timedelta(days=1))
        self.assertIsNone(backtest.delayed_rebalance)
        self.assertEqual(backtest.portfolio.pending_target, {"QQQ": 0.5})

    def test_first_execution_records_pre_rebalance_weights(self):
        portfolio = Portfolio()
        portfolio.cash = 0.0
        portfolio.positions = {"QQQ": 0.6, "BND": 0.4}
        portfolio.start_rebalance(
            {"QQQ": 0.7, "BND": 0.3},
            days=1,
            date=pd.Timestamp("2024-01-02"),
        )

        portfolio.update(
            {"QQQ": 1.0, "BND": 1.0},
            date=pd.Timestamp("2024-01-03"),
        )

        event = portfolio.get_rebalances()[0]
        self.assertEqual(event["ExecutionDate"], pd.Timestamp("2024-01-03"))
        self.assertEqual(event["ExecutionDays"], 1)
        self.assertEqual(event["PreWeights"], {"QQQ": 0.6, "BND": 0.4})


if __name__ == "__main__":
    unittest.main()

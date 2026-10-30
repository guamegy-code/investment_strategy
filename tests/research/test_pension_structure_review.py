"""Controlled ablation and genuinely independent capital accounting checks."""
from tests import PROJECT_ROOT
import sys
import unittest

import pandas as pd

sys.path.insert(0, str(PROJECT_ROOT / "src/legacy-python/validation"))
from pension_structure_review import (LOCK, BinaryTrend, aggregate_sleeves,
                                     definition_for, trend_features)
from strategy_dsl import DeclarativeStrategy, load_strategy_definition
from strategy30_31_yaml_parity import STRATEGY30


class PensionStructureTests(unittest.TestCase):
    def test_ablation_changes_only_registered_state_blocks(self):
        base = load_strategy_definition(STRATEGY30)
        self.assertEqual(definition_for("BASE30"), base)
        for variant, states in LOCK["ablation_states"].items():
            changed = definition_for(variant)
            DeclarativeStrategy(changed)  # Validate executable definitions.
            for name, state in changed["state"].items():
                expected = {"initial": states[name], "rules": []} if name in states else base["state"][name]
                self.assertEqual(state, expected)
            self.assertEqual(changed["target"], base["target"])
            self.assertEqual(changed["rebalance"], base["rebalance"])
            self.assertEqual(changed["parameters"], base["parameters"])

    def test_binary_trend_changes_only_on_its_own_signal(self):
        strategy = BinaryTrend("SMA", 20)
        for price, expected, rebalance in ((110, 1., True), (120, 1., False),
                                            (90, 0., True), (80, 0., False), (101, 1., True)):
            signal = strategy.evaluate(None, {"QQQ": {"Close": price, "SMA20": 100}}, None)
            self.assertEqual(signal["target"]["QQQ"], expected)
            self.assertEqual(signal["rebalance"], rebalance)

    def test_momentum_compares_with_cash_not_zero(self):
        strategy = BinaryTrend("EXCESS", 60)
        signal = strategy.evaluate(None, {"QQQ": {"Close": 110, "EXCESS60": -.01}}, None)
        self.assertEqual(signal["target"], {"QQQ": 0., "BIL": 1.})

    def test_feature_prefix_invariance_and_no_warmup_shortcut(self):
        dates = pd.bdate_range("2020-01-01", periods=180)
        qqq = pd.DataFrame({"Close": range(100, 280)}, index=dates)
        bil = pd.DataFrame({"Close": range(1000, 1180)}, index=dates)
        result = trend_features(qqq, bil)
        self.assertTrue(result.SMA120.iloc[:119].isna().all())
        self.assertTrue(result.EXCESS120.iloc[:120].isna().all())
        pd.testing.assert_frame_equal(result.iloc[:150], trend_features(qqq.iloc[:150], bil.iloc[:150]))

    def test_independent_capital_drifts_instead_of_daily_rebalancing(self):
        dates = pd.bdate_range("2020-01-01", periods=2)
        first = pd.DataFrame({"Portfolio": [1., 2.], "TransactionCosts": [0., .03],
            "Weights": [{"QQQ": 1.}]*2,
            "NotificationContext": [{"target_weights": {"QQQ": 1.}}]*2}, index=dates)
        second = pd.DataFrame({"Portfolio": [1., 1.], "TransactionCosts": [0., .01],
            "Weights": [{"QQQ": 0.}]*2,
            "NotificationContext": [{"target_weights": {"QQQ": 0.}}]*2}, index=dates)
        result = aggregate_sleeves([first, second])
        self.assertEqual(result.Portfolio.tolist(), [1., 1.5])
        self.assertAlmostEqual(result.QQQActual.iloc[-1], 2/3)
        self.assertAlmostEqual(result.QQQTarget.iloc[-1], 2/3)
        self.assertAlmostEqual(result.TransactionCosts.iloc[-1], .02)

    def test_mismatched_sleeve_dates_are_rejected(self):
        first = pd.DataFrame(index=pd.bdate_range("2020-01-01", periods=2))
        second = pd.DataFrame(index=pd.bdate_range("2020-01-02", periods=2))
        with self.assertRaises(ValueError):
            aggregate_sleeves([first, second])


if __name__ == "__main__":
    unittest.main()

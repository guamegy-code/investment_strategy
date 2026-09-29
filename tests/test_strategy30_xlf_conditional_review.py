"""Checks for the research-only financial relative-strength variants."""

import sys
import unittest
from pathlib import Path

import pandas as pd

legacy = Path(__file__).resolve().parents[1] / "src" / "legacy-python"
sys.path.insert(0, str(legacy))
sys.path.insert(0, str(legacy / "validation"))

from strategy30_xlf_conditional_review import (  # noqa: E402
    STRATEGY30, definition_for, prelead_window_signal,
)
from strategy_dsl import load_strategy_definition  # noqa: E402


class FinancialConditionTests(unittest.TestCase):
    def test_base_is_deployed_definition(self):
        self.assertEqual(definition_for("BASE30"), load_strategy_definition(STRATEGY30))

    def test_financial_guard_preserves_existing_rules_and_priority(self):
        base = definition_for("BASE30")
        candidate = definition_for("FIN_PRELEAD_WINDOW")
        for state in ("credit_guard", "deep_guard", "defense_mode"):
            self.assertEqual(candidate["state"][state], base["state"][state])
        self.assertEqual(candidate["rebalance"], base["rebalance"])
        conditions = [rule.get("when") for rule in candidate["target"]]
        finance = conditions.index("state.financial_guard == 'TRUE'")
        self.assertGreater(finance, conditions.index("state.defense_mode == 'DEFENSE'"))
        self.assertLess(finance, conditions.index("state.credit_guard == 'TRUE'"))
        self.assertEqual(candidate["target"][finance]["weights"]["QQQ"], "35%")

    def test_prelead_window_uses_only_sessions_five_to_ten_before_onset(self):
        index = pd.bdate_range("2024-01-01", periods=20)
        weak = pd.Series(False, index=index)
        mild = pd.Series(False, index=index)
        weak.iloc[3] = True
        mild.iloc[10] = True
        self.assertEqual(prelead_window_signal(weak, mild).iloc[10], 1)
        weak.iloc[3] = False
        weak.iloc[9] = True
        self.assertEqual(prelead_window_signal(weak, mild).iloc[10], 0)
        weak.iloc[9] = False
        weak.iloc[10] = True
        self.assertEqual(prelead_window_signal(weak, mild).iloc[10], 0)

    def test_ongoing_baa_warning_does_not_retrigger(self):
        index = pd.bdate_range("2024-01-01", periods=20)
        weak = pd.Series(False, index=index)
        mild = pd.Series(False, index=index)
        weak.iloc[3] = True
        mild.iloc[10:13] = True
        self.assertEqual(prelead_window_signal(weak, mild).iloc[10:13].tolist(),
                         [1, 0, 0])


if __name__ == "__main__":
    unittest.main()

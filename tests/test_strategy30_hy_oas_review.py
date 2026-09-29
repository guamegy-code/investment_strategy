"""The HY experiment must preserve every deployed non-credit rule."""

import sys
import unittest
from pathlib import Path

legacy = Path(__file__).resolve().parents[1] / "src" / "legacy-python"
sys.path.insert(0, str(legacy))
sys.path.insert(0, str(legacy / "validation"))

from strategy_dsl import load_strategy_definition  # noqa: E402
from strategy30_hy_oas_review import STRATEGY30, definition_for  # noqa: E402


class Strategy30HyOasDefinitionTests(unittest.TestCase):
    def test_baseline_is_the_deployed_yaml(self):
        self.assertEqual(definition_for("BASE30"), load_strategy_definition(STRATEGY30))

    def test_entry_experiment_keeps_targets_and_release_rules(self):
        base = definition_for("BASE30")
        hy = definition_for("HY_ENTRY")
        self.assertEqual(hy["target"], base["target"])
        self.assertEqual(hy["rebalance"], base["rebalance"])
        self.assertEqual(
            hy["state"]["credit_guard"]["rules"][1],
            base["state"]["credit_guard"]["rules"][1],
        )
        self.assertEqual(hy["state"]["deep_guard"], base["state"]["deep_guard"])
        self.assertIn("BAMLH0A0HYM2.close >= 4", hy["state"]["credit_guard"]["rules"][0]["when"])
        self.assertNotIn("BAMLH0A0HYM2", base["assets"]["observations"])

    def test_full_hy_changes_credit_release_but_not_portfolio_rules(self):
        base = definition_for("BASE30")
        hy = definition_for("FULL_HY")
        self.assertEqual(hy["target"], base["target"])
        self.assertEqual(hy["rebalance"], base["rebalance"])
        self.assertIn("BAMLH0A0HYM2.close < 4.5", hy["state"]["credit_guard"]["rules"][1]["when"])
        self.assertIn("BAMLH0A0HYM2.close < 4", hy["state"]["deep_guard"]["rules"][1]["when"])
        self.assertEqual(base, load_strategy_definition(STRATEGY30))


if __name__ == "__main__":
    unittest.main()

"""Research variants keep the deployed allocation and recovery policy intact."""

import sys
import unittest
from pathlib import Path

legacy = Path(__file__).resolve().parents[1] / "src" / "legacy-python"
sys.path.insert(0, str(legacy))
sys.path.insert(0, str(legacy / "validation"))

from strategy30_signal_combination_review import (  # noqa: E402
    STRATEGY30, definition_for,
)
from strategy_dsl import load_strategy_definition  # noqa: E402


class CombinationDefinitionTests(unittest.TestCase):
    def test_baseline_matches_deployed_definition(self):
        self.assertEqual(definition_for("BASE30"), load_strategy_definition(STRATEGY30))

    def test_credit_and_market_variants_keep_allocations_and_release(self):
        base = definition_for("BASE30")
        for name in ("MARKET", "FAST_CREDIT", "MARKET_FAST", "SEQUENTIAL"):
            with self.subTest(name=name):
                changed = definition_for(name)
                self.assertEqual(changed["target"], base["target"])
                self.assertEqual(changed["rebalance"], base["rebalance"])
                for state in ("credit_guard", "deep_guard"):
                    self.assertEqual(changed["state"][state]["rules"][1],
                                     base["state"][state]["rules"][1])

    def test_price_guard_sits_below_existing_defenses(self):
        definition = definition_for("PRICE70")
        conditions = [item.get("when") for item in definition["target"]]
        shock = conditions.index("state.shock_guard == 'TRUE'")
        self.assertGreater(shock, conditions.index("state.credit_guard == 'TRUE'"))
        self.assertLess(shock, conditions.index("state.trend_mode == 'RECOVERY'"))
        self.assertEqual(definition["target"][shock]["weights"]["QQQ"], "70%")
        self.assertEqual(definition["state"]["shock_guard"]["rules"][1]["confirm"], 3)


if __name__ == "__main__":
    unittest.main()

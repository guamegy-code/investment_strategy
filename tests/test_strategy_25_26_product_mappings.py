import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src" / "legacy-python"))

from strategy_dsl import ProductMappedStrategy, load_strategy_directory


class Strategy2526ProductMappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.strategies = {
            strategy.strategy_id: strategy
            for strategy in load_strategy_directory(PROJECT_ROOT / "strategies")
        }

    def test_strategy_25_product_mapping(self):
        mapped = self.strategies[
            "dsl:qqq-valuation-breakdown-balanced-kodex-koact"
        ]

        self.assertIsInstance(mapped, ProductMappedStrategy)
        self.assertEqual(mapped.source_strategy_id, "qqq-valuation-breakdown-balanced")
        self.assertEqual(mapped.products["QQQ"], {
            "379810.KS": 0.5,
            "0015B0.KS": 0.5,
        })
        self.assertEqual(mapped.products["BIL"], {"488770.KS": 1.0})
        self.assertNotIn("notifications", mapped.definition)
        self.assertIn("notifications", mapped.source_strategy.definition)

    def test_strategy_26_product_mapping(self):
        mapped = self.strategies[
            "dsl:band-7030-tdf-valuation-defense-kodex-koact"
        ]

        self.assertIsInstance(mapped, ProductMappedStrategy)
        self.assertEqual(mapped.source_strategy_id, "band-7030-tdf-valuation-defense")
        self.assertEqual(mapped.products["QQQ"], {
            "379810.KS": 0.5,
            "0015B0.KS": 0.5,
        })
        self.assertEqual(
            mapped.products["TDF2050_PROXY"], {"434060.KS": 1.0}
        )
        self.assertEqual(mapped.products["BIL"], {"488770.KS": 1.0})
        self.assertNotIn("notifications", mapped.definition)
        self.assertIn("notifications", mapped.source_strategy.definition)


if __name__ == "__main__":
    unittest.main()

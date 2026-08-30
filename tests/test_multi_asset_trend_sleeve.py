import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "src" / "legacy-python"
if str(LEGACY) not in sys.path:
    sys.path.insert(0, str(LEGACY))

from validation.multi_asset_trend_sleeve import (  # noqa: E402
    ASSETS,
    BASE_COST,
    GROUPS,
    Scenario,
    _assert_lock,
    _group_weights,
    build_targets,
    run_stream,
)


class MultiAssetTrendSleeveTests(unittest.TestCase):
    def _prices(self, periods=420):
        index = pd.bdate_range("2020-01-01", periods=periods)
        data = {}
        for ordinal, ticker in enumerate((*ASSETS, "KRGOVT3"), 1):
            daily = 0.0001 + ordinal * 0.00001
            data[ticker] = 100.0 * np.cumprod(np.full(periods, 1.0 + daily))
        return pd.DataFrame(data, index=index)

    def test_lock_matches_implementation(self):
        lock = _assert_lock()
        self.assertEqual(lock["construction"]["base_one_way_cost"], BASE_COST)

    def test_group_weights_use_equal_group_budgets(self):
        volatility = pd.Series(0.20, index=ASSETS)
        weights = _group_weights(volatility, equal_within_group=False)
        for members in GROUPS.values():
            self.assertAlmostEqual(weights.loc[list(members)].sum(), 0.25)
        self.assertAlmostEqual(weights.sum(), 1.0)

    def test_long_flat_never_has_negative_exposure(self):
        targets = build_targets(self._prices(), "TSMOM_LONG_FLAT")
        self.assertTrue((targets >= 0.0).all().all())
        self.assertTrue((targets.abs().sum(axis=1) <= 1.0 + 1e-12).all())

    def test_long_short_is_lagged_and_costed(self):
        prices = self._prices()
        result = run_stream(prices, "TSMOM_LONG_SHORT", Scenario("TEST"))
        history = result["history"]
        positions = result["positions"]
        self.assertGreater(len(history), 0)
        self.assertGreater(float(history["TransactionCosts"].iloc[-1]), 0.0)
        self.assertTrue((positions.abs().sum(axis=1) <= 1.0 + 1e-12).all())


if __name__ == "__main__":
    unittest.main()

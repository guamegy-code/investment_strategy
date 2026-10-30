"""Causal market clocks, protected stock units, and bounded defense allocation."""
from tests import PROJECT_ROOT
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(PROJECT_ROOT / "src/legacy-python/validation"))
from pension_defense_review import DefensePortfolio, feature_frame, select_mix, simulate


class PensionDefenseTests(unittest.TestCase):
    def test_defense_switch_preserves_both_risk_products_at_open(self):
        portfolio = DefensePortfolio(("R1", "R2"), commission=.00015, slippage=.0002)
        portfolio.cash = 100_000_000.
        first = {"R1": 100., "R2": 100., "CASH": 100., "GOLD": 100.}
        portfolio.execute(first, .8, {"R1": .5, "R2": .5}, {"CASH": 1.}, defense_only=False, date="first")
        before = {asset: portfolio.positions[asset] for asset in ("R1", "R2")}
        changed = {"R1": 160., "R2": 70., "CASH": 101., "GOLD": 140.}
        portfolio.execute(changed, .8, {"R1": .5, "R2": .5}, {"CASH": .7, "GOLD": .3}, defense_only=True, date="switch")
        for asset in before:
            self.assertAlmostEqual(portfolio.positions[asset], before[asset])
        self.assertFalse(any(t["Ticker"] in before for t in portfolio.trades if t["Date"] == "switch"))
        self.assertGreaterEqual(portfolio.cash, -1e-6)

    def test_rotation_has_one_asset_per_group_and_limited_gold(self):
        date = pd.Timestamp("2025-01-02")
        def row(score, volatility):
            return pd.DataFrame({"Close": [120.], "EMA200": [100.], "VOL60": [volatility],
                "ROC60": [score], "ROC120": [score], "ROC252": [score]}, index=[date])
        features = {"CASH": row(.01, .001), "GOLD": row(.10, .02),
                    "BOND1": row(.05, .01), "BOND2": row(.03, .01)}
        mix = select_mix(features, date, "CASH", "CASH",
                         {"GOLD": "GOLD", "BOND1": "BOND", "BOND2": "BOND"})
        self.assertNotIn("BOND2", mix)
        self.assertLessEqual(mix["GOLD"], .3)
        self.assertLessEqual(mix["BOND1"], .5)
        self.assertAlmostEqual(sum(mix.values()), 1.)

    def test_feature_history_is_prefix_invariant(self):
        dates = pd.bdate_range("2020-01-01", periods=400)
        data = pd.DataFrame({"Close": range(100, 500)}, index=dates)
        pd.testing.assert_frame_equal(feature_frame(data).iloc[:300], feature_frame(data.iloc[:300]))
        self.assertTrue(feature_frame(data).ROC252.iloc[:252].isna().all())

    def test_local_open_never_uses_same_date_us_close_and_prefix_is_stable(self):
        dates = pd.bdate_range("2025-01-01", periods=8)
        prices = {asset: pd.DataFrame({"Open": [100.]*8, "Close": [100.]*8}, index=dates)
                  for asset in ("R1", "R2", "MM")}
        source = pd.DataFrame({"RiskTarget": [1., .5, 0.], "Rebalance": [True]*3},
                             index=pd.to_datetime(["2024-12-31", "2025-01-02", "2025-01-03"]))
        history, _, activity, _ = simulate(prices, {"R1": .5, "R2": .5}, source, "MM",
                                          ("2025-01-02", "2025-01-10"))
        first = activity.iloc[0]
        self.assertEqual(first.USSourceDate, pd.Timestamp("2024-12-31"))
        self.assertAlmostEqual(history.loc["2025-01-02", "RiskWeight"], 1., places=3)
        self.assertAlmostEqual(history.loc["2025-01-03", "RiskWeight"], .5, places=3)
        self.assertAlmostEqual(history.loc["2025-01-06", "RiskWeight"], 0., places=6)
        prefix, _, _, _ = simulate({s: f.loc[:"2025-01-07"] for s, f in prices.items()},
            {"R1": .5, "R2": .5}, source.loc[:"2025-01-07"], "MM", ("2025-01-02", "2025-01-07"))
        pd.testing.assert_frame_equal(history.loc[:"2025-01-07"], prefix)

    def test_delayed_source_order_waits_a_local_session(self):
        dates = pd.bdate_range("2025-01-01", periods=5)
        prices = {asset: pd.DataFrame({"Open": [100.]*5, "Close": [100.]*5}, index=dates)
                  for asset in ("R1", "R2", "MM")}
        source = pd.DataFrame({"RiskTarget": [1.], "Rebalance": [True]},
                             index=pd.to_datetime(["2024-12-31"]))
        history, _, _, _ = simulate(prices, {"R1": .5, "R2": .5}, source, "MM",
                                    ("2025-01-02", "2025-01-07"), delay=1)
        self.assertEqual(history.loc["2025-01-02", "RiskWeight"], 0.)
        self.assertGreater(history.loc["2025-01-03", "RiskWeight"], .99)

    def test_monthly_defense_switch_cannot_cancel_pending_stock_reentry(self):
        dates = pd.bdate_range("2025-01-27", periods=10)
        prices = {asset: pd.DataFrame({"Open": [100.]*10, "Close": [100.]*10}, index=dates)
                  for asset in ("R1", "R2", "MM", "SHORT")}
        source = pd.DataFrame({"RiskTarget": [.5, 1.], "Rebalance": [True, True]},
                             index=pd.to_datetime(["2025-01-27", "2025-01-30"]))
        def policy(profile, features, date, fx):
            return {"SHORT" if date >= pd.Timestamp("2025-01-31") else "MM": 1.}
        with patch("pension_defense_review.profile_policy", side_effect=policy):
            history, _, activity, _ = simulate(prices, {"R1": .5, "R2": .5}, source,
                "TEST", ("2025-01-28", "2025-02-07"), delay=1)
        self.assertAlmostEqual(history.loc["2025-01-30", "RiskWeight"], .5, places=3)
        self.assertGreater(history.loc["2025-02-03", "RiskWeight"], .99)
        execution = activity[activity.Date.eq(pd.Timestamp("2025-02-03"))].iloc[0]
        self.assertTrue(execution.DefenseSwitch)
        self.assertTrue(execution.Executed)
        self.assertFalse(execution.DefenseOnly)

    def test_identical_core_signals_keep_original_execution_deadline(self):
        dates = pd.bdate_range("2025-01-01", periods=6)
        prices = {asset: pd.DataFrame({"Open": [100.]*6, "Close": [100.]*6}, index=dates)
                  for asset in ("R1", "R2", "MM")}
        source = pd.DataFrame({"RiskTarget": [1.]*6, "Rebalance": [True]*6}, index=dates)
        history, _, _, _ = simulate(prices, {"R1": .5, "R2": .5}, source, "MM",
                                    ("2025-01-02", "2025-01-08"), delay=1)
        self.assertEqual(history.loc["2025-01-02", "RiskWeight"], 0.)
        self.assertGreater(history.loc["2025-01-03", "RiskWeight"], .99)


if __name__ == "__main__":
    unittest.main()

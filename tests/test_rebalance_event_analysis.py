import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from validation.rebalance_event_analysis import (  # noqa: E402
    classify_rebalance,
    fixed_weight_path,
    major_drawdown_episodes,
    path_return_and_drawdown,
    weights_with_total_risk,
)


class RebalanceEventAnalysisTests(unittest.TestCase):
    def test_classification_uses_actual_pre_and_target_risk_weights(self):
        self.assertEqual(classify_rebalance("BULL->BEAR", 0.70, 0.20), "RISK_DOWN")
        self.assertEqual(classify_rebalance("BEAR->RECOVERY", 0.20, 0.50), "RISK_UP")
        self.assertEqual(
            classify_rebalance("MONTHLY_5PCT_BAND", 0.74, 0.70),
            "BAND_REBALANCE",
        )
        self.assertEqual(
            classify_rebalance("SAFE_ROTATION_BND->BIL", 0.74, 0.70, 0.70),
            "SAFE_ROTATION",
        )
        self.assertEqual(
            classify_rebalance("BULL->CAUTION", 0.66, 0.70, 0.70),
            "STATE_LATERAL",
        )
        self.assertEqual(
            classify_rebalance(
                "TRAILING_STOP_DEFENSIVE_MODE_QQQ(-25%)", 0.72, 0.40, 0.70
            ),
            "RISK_DOWN",
        )
        self.assertEqual(
            classify_rebalance("DOWNTREND_TAKE_PROFIT_QQQ", 0.79, 0.70, 0.70),
            "BAND_SELL",
        )

    def test_fixed_weight_path_keeps_residual_cash_flat(self):
        prices = pd.DataFrame({
            "QQQ": [100.0, 110.0],
            "BND": [100.0, 100.0],
        })

        path = fixed_weight_path(prices, {"QQQ": 0.50, "BND": 0.25})

        self.assertAlmostEqual(path.iloc[0], 1.0)
        self.assertAlmostEqual(path.iloc[-1], 1.05)

    def test_total_risk_rescaling_treats_gld_as_risk_when_requested(self):
        adjusted = weights_with_total_risk(
            {"QQQ": 0.65, "GLD": 0.05, "BND": 0.30},
            ("QQQ", "GLD"),
            0.40,
        )

        self.assertAlmostEqual(adjusted["QQQ"] + adjusted["GLD"], 0.40)
        self.assertAlmostEqual(adjusted["BND"], 0.60)

    def test_lower_risk_target_improves_drawdown_in_falling_market(self):
        prices = pd.DataFrame({
            "QQQ": [100.0, 90.0, 80.0],
            "BND": [100.0, 100.0, 100.0],
        })
        hold = fixed_weight_path(prices, {"QQQ": 0.70, "BND": 0.30})
        target = fixed_weight_path(prices, {"QQQ": 0.30, "BND": 0.70})

        _, hold_mdd = path_return_and_drawdown(hold)
        _, target_mdd = path_return_and_drawdown(target)

        self.assertGreater(target_mdd, hold_mdd)

    def test_major_drawdown_episode_ends_when_prior_peak_is_recovered(self):
        dates = pd.date_range("2020-01-01", periods=6, freq="D")
        market = pd.DataFrame(
            {"QQQ_Close": [100.0, 95.0, 89.0, 85.0, 96.0, 101.0]},
            index=dates,
        )

        episodes = major_drawdown_episodes(market)

        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes.iloc[0]["StartDate"], dates[0])
        self.assertEqual(episodes.iloc[0]["TroughDate"], dates[3])
        self.assertEqual(episodes.iloc[0]["EndDate"], dates[5])
        self.assertAlmostEqual(episodes.iloc[0]["QQQDrawdown"], -0.15)


if __name__ == "__main__":
    unittest.main()

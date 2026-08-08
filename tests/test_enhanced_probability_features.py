import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from validation.enhanced_probability_features import (  # noqa: E402
    FEATURE_PROFILES,
    build_enhanced_features,
)


class EnhancedProbabilityFeatureTests(unittest.TestCase):
    def test_feature_profiles_are_nested(self):
        for smaller, larger in zip(FEATURE_PROFILES, FEATURE_PROFILES[1:]):
            self.assertTrue(set(smaller.columns).issubset(larger.columns))

    def test_enhanced_features_use_expected_market_columns(self):
        periods = 260
        index = pd.bdate_range("2020-01-01", periods=periods)
        data = pd.DataFrame(index=index)
        base = 100.0 + np.arange(periods) * 0.1
        for ticker, multiplier in (
            ("QQQ", 1.0),
            ("SPY", 0.9),
            ("IWM", 0.8),
            ("BND", 0.5),
            ("BIL", 0.3),
            ("GLD", 0.6),
        ):
            close = base * multiplier
            data[f"{ticker}_Close"] = close
            data[f"{ticker}_ROC20"] = pd.Series(close, index=index).pct_change(20) * 100
        data["QQQ_High"] = data["QQQ_Close"] * 1.01
        data["QQQ_Low"] = data["QQQ_Close"] * 0.99
        data["QQQ_VOL60"] = 0.20
        data["QQQ_DRAWDOWN120"] = -0.05
        data["QQQ_ROC5"] = 1.0
        data["QQQ_ROC60"] = 3.0
        data["QQQ_ROC120"] = 6.0
        data["QQQ_RSI14"] = 50.0
        data["QQQ_Volume"] = np.arange(periods) + 1000.0
        data["SPY_EMA200"] = data["SPY_Close"] * 0.95
        data["IWM_EMA200"] = data["IWM_Close"] * 0.95

        features = build_enhanced_features(data)

        for column in FEATURE_PROFILES[-1].columns:
            if column not in features and column != "QQQ_EMA200_DISTANCE":
                self.fail(f"missing enhanced feature: {column}")
        self.assertAlmostEqual(
            features["BROAD_MARKET_CONFIRMATION"].iloc[-1], 1.0
        )


if __name__ == "__main__":
    unittest.main()

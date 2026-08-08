import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from validation.external_probability_features import (  # noqa: E402
    PROFILES,
    build_external_features,
)


class ExternalProbabilityFeatureTests(unittest.TestCase):
    def test_profiles_add_vix_and_credit_separately(self):
        names = {profile.name: set(profile.columns) for profile in PROFILES}

        self.assertIn("VIX_TERM_STRUCTURE", names["LOCAL_PLUS_VIX"])
        self.assertNotIn("HYG_LQD_REL20", names["LOCAL_PLUS_VIX"])
        self.assertIn("HYG_LQD_REL20", names["LOCAL_PLUS_CREDIT"])
        self.assertNotIn("VIX_TERM_STRUCTURE", names["LOCAL_PLUS_CREDIT"])

    def test_external_feature_formulas(self):
        periods = 260
        index = pd.bdate_range("2020-01-01", periods=periods)
        data = pd.DataFrame(index=index)
        base = 100.0 + np.arange(periods) * 0.1
        for ticker, multiplier in (
            ("QQQ", 1.0), ("SPY", 0.9), ("IWM", 0.8),
            ("BND", 0.5), ("BIL", 0.3), ("GLD", 0.6),
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
        data["VIX_Close"] = 20.0
        data["VIX3M_Close"] = 25.0
        data["HYG_Close"] = base * 0.8
        data["LQD_Close"] = base * 0.7

        features = build_external_features(data)

        self.assertAlmostEqual(features["VIX_TERM_STRUCTURE"].iloc[-1], -0.2)
        self.assertAlmostEqual(features["HYG_LQD_REL20"].iloc[-1], 0.0)


if __name__ == "__main__":
    unittest.main()

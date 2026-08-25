import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd


sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "src" / "legacy-python"),
)

from validation.cnn_fear_greed_reconstruction import (  # noqa: E402
    build_four_component_proxy,
    causal_percentile,
    fit_affine_calibration,
    parse_official_payload,
)


class CnnFearGreedReconstructionTests(unittest.TestCase):
    def test_official_parser_deduplicates_dates(self):
        timestamp = int(pd.Timestamp("2024-01-02", tz="UTC").timestamp() * 1000)
        payload = {
            "fear_and_greed_historical": {
                "data": [
                    {"x": timestamp, "y": 10},
                    {"x": timestamp, "y": 20},
                ]
            }
        }

        result = parse_official_payload(payload)

        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["Value"], 20)

    def test_percentile_uses_only_current_and_prior_values(self):
        original_minimum = 252
        index = pd.bdate_range("2020-01-01", periods=260)
        values = pd.Series(np.arange(260, dtype=float), index=index)

        result = causal_percentile(values)

        self.assertTrue(result.iloc[: original_minimum - 1].isna().all())
        self.assertEqual(result.iloc[original_minimum - 1], 1.0)

    def test_affine_calibration_recovers_linear_mapping(self):
        index = pd.bdate_range("2021-02-01", periods=800)
        proxy = pd.Series(np.linspace(0, 100, len(index)), index=index)
        official = 10.0 + 0.8 * proxy

        intercept, slope, overlap = fit_affine_calibration(
            proxy,
            official,
            train_end=index[500],
        )

        self.assertAlmostEqual(intercept, 10.0)
        self.assertAlmostEqual(slope, 0.8)
        self.assertLess(
            (overlap["Official"] - overlap["Reconstructed"]).abs().max(),
            1e-10,
        )

    def test_component_windows_are_calculated_on_native_calendars(self):
        etf_dates = pd.bdate_range("2020-01-01", periods=400)
        vix_dates = pd.date_range(etf_dates.min(), etf_dates.max(), freq="D")
        assets = {
            ticker: pd.DataFrame(
                {"Close": np.linspace(100.0, 150.0, len(etf_dates))},
                index=etf_dates,
            )
            for ticker in ("SPY", "BND", "HYG", "LQD")
        }
        assets["VIX"] = pd.DataFrame(
            {"Close": np.linspace(30.0, 15.0, len(vix_dates))},
            index=vix_dates,
        )

        with patch(
            "validation.cnn_fear_greed_reconstruction._load_asset",
            side_effect=lambda ticker: assets[ticker],
        ):
            result = build_four_component_proxy()

        self.assertEqual(
            result["RawComposite"].dropna().index.max(),
            etf_dates.max(),
        )


if __name__ == "__main__":
    unittest.main()

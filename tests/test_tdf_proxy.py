import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tdf_proxy import build_proxy_frame
from strategy_dsl import ProductMappedStrategy, load_strategy_directory


class TdfProxyTests(unittest.TestCase):
    def test_proxy_uses_target_weights_for_daily_return(self):
        dates = pd.to_datetime(["2025-01-02", "2025-01-03"])
        components = {
            "STOCK": pd.DataFrame(
                {"Open": [100, 110], "High": [100, 110], "Low": [100, 110], "Close": [100, 110]},
                index=dates,
            ),
            "BOND": pd.DataFrame(
                {"Open": [100, 100], "High": [100, 100], "Low": [100, 100], "Close": [100, 100]},
                index=dates,
            ),
        }

        proxy = build_proxy_frame(
            components, {"STOCK": 0.75, "BOND": 0.25}, observation_lag=0
        )

        self.assertAlmostEqual(proxy.iloc[-1]["Close"], 107.5)
        self.assertLessEqual(proxy.iloc[-1]["Low"], proxy.iloc[-1]["Close"])
        self.assertGreaterEqual(proxy.iloc[-1]["High"], proxy.iloc[-1]["Close"])

    def test_proxy_rebalances_at_the_start_of_each_month(self):
        dates = pd.to_datetime(["2025-01-30", "2025-01-31", "2025-02-03"])
        components = {
            "STOCK": pd.DataFrame(
                {"Open": [100, 200, 200], "High": [100, 200, 200], "Low": [100, 200, 200], "Close": [100, 200, 200]},
                index=dates,
            ),
            "BOND": pd.DataFrame(
                {"Open": [100, 100, 200], "High": [100, 100, 200], "Low": [100, 100, 200], "Close": [100, 100, 200]},
                index=dates,
            ),
        }

        proxy = build_proxy_frame(
            components, {"STOCK": 0.5, "BOND": 0.5}, observation_lag=0
        )

        self.assertAlmostEqual(proxy.iloc[1]["Close"], 150.0)
        self.assertAlmostEqual(proxy.iloc[2]["Close"], 225.0)

    def test_proxy_uses_the_prior_session_for_korean_market_alignment(self):
        dates = pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06"])
        component = pd.DataFrame(
            {
                "Open": [100, 110, 121],
                "High": [100, 110, 121],
                "Low": [100, 110, 121],
                "Close": [100, 110, 121],
            },
            index=dates,
        )

        proxy = build_proxy_frame({"STOCK": component}, {"STOCK": 1.0})

        self.assertEqual(proxy.index[0], dates[1])
        self.assertAlmostEqual(proxy.iloc[0]["Close"], 100.0)
        self.assertAlmostEqual(proxy.iloc[1]["Close"], 110.0)

    def test_tdf_strategy_and_product_mapping_load_together(self):
        source = PROJECT_ROOT / "strategies" / "06_profit_band_tdf2050.yaml"
        product = PROJECT_ROOT / "strategies" / "06P_profit_band_time_tdf2050.yaml"
        with TemporaryDirectory() as directory:
            Path(directory, source.name).write_text(
                source.read_text(encoding="utf-8"), encoding="utf-8"
            )
            Path(directory, product.name).write_text(
                product.read_text(encoding="utf-8"), encoding="utf-8"
            )
            loaded = load_strategy_directory(directory)

        self.assertEqual(len(loaded), 2)
        mapped = next(item for item in loaded if isinstance(item, ProductMappedStrategy))
        self.assertEqual(mapped.products["QQQ"], {"426030.KS": 1.0})
        self.assertEqual(mapped.products["TDF2050_PROXY"], {"434060.KS": 1.0})


if __name__ == "__main__":
    unittest.main()

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from downloader import _build_krw_adjusted_asset, ensure_data_files  # noqa: E402


class EnsureDataFilesTests(unittest.TestCase):
    def test_krw_adjusted_asset_uses_underlying_ohlc_and_fx_close(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            dates = pd.date_range("2025-01-02", periods=3, freq="D")
            pd.DataFrame({
                "Open": [10.0, 11.0, 12.0],
                "High": [11.0, 12.0, 13.0],
                "Low": [9.0, 10.0, 11.0],
                "Close": [10.5, 11.5, 12.5],
                "Volume": [100, 200, 300],
            }, index=dates).rename_axis("Date").to_csv(data_dir / "QQQ.csv")
            pd.DataFrame({"Close": [1300.0, 1310.0, 1320.0]}, index=dates).rename_axis(
                "Date"
            ).to_csv(data_dir / "KRW=X.csv")

            with patch("downloader.ensure_data_files") as ensure:
                result = _build_krw_adjusted_asset("QQQ_KRW", data_dir)

            ensure.assert_called_once_with(("QQQ", "KRW=X"), data_dir=data_dir)
            self.assertEqual(result["Close"].tolist(), [13650.0, 15065.0, 16500.0])
            self.assertEqual(result["Volume"].tolist(), [100, 200, 300])
            self.assertTrue((data_dir / "QQQ_KRW.csv").is_file())

    def test_existing_files_are_not_downloaded(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            (data_dir / "QQQ.csv").write_text("Date,Close\n", encoding="utf-8")

            with patch("downloader.download_one") as download:
                downloaded = ensure_data_files(("QQQ", "QQQ"), data_dir)

        self.assertEqual(downloaded, ())
        download.assert_not_called()

    def test_only_missing_unique_tickers_are_downloaded_in_order(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            (data_dir / "QQQ.csv").write_text("Date,Close\n", encoding="utf-8")

            with patch("downloader.download_one") as download:
                downloaded = ensure_data_files(
                    ("QQQ", "BND", "BND", "BIL"), data_dir
                )

        self.assertEqual(downloaded, ("BND", "BIL"))
        self.assertEqual(
            [call.args[0] for call in download.call_args_list],
            ["BND", "BIL"],
        )
        self.assertTrue(all(
            call.kwargs["output_dir"] == data_dir
            for call in download.call_args_list
        ))

    def test_existing_file_is_refreshed_when_indicator_warmup_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            (data_dir / "QQQ.csv").write_text(
                "Date,Close,ROC40\n2012-01-03,100,\n",
                encoding="utf-8",
            )

            with patch("downloader.download_one") as download:
                refreshed = ensure_data_files(
                    ("QQQ",),
                    data_dir,
                    required_market_fields={"QQQ": {"CLOSE", "ROC40"}},
                )

        self.assertEqual(refreshed, ("QQQ",))
        download.assert_called_once_with("QQQ", output_dir=data_dir)

    def test_download_failures_name_the_failed_ticker(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "downloader.download_one",
                side_effect=ValueError("empty response"),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "VXUS: empty response"
                ):
                    ensure_data_files(("VXUS",), Path(directory))


if __name__ == "__main__":
    unittest.main()

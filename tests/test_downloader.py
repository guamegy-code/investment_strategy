import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from downloader import ensure_data_files  # noqa: E402


class EnsureDataFilesTests(unittest.TestCase):
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

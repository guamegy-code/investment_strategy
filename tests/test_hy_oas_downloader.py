"""Macro loader regression checks; fixtures are synthetic, not ICE data."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "legacy-python"))

from downloader import (  # noqa: E402
    _align_macro_to_qqq_sessions,
    _load_fred_series,
    _load_hy_oas_observations,
    _macro_market_frame,
    _merge_historical_and_live_fred,
    download_one,
)


class MacroSeriesTests(unittest.TestCase):
    def test_baa10y_output_unchanged_after_macro_refactor(self):
        dates = pd.bdate_range("2025-01-02", periods=5)
        observations = pd.Series([2.0, 2.2, 2.3], index=dates[[0, 2, 4]])
        expected = observations.reindex(dates, method="ffill").shift(1)
        expected.index.name = "Date"
        frame = _macro_market_frame(observations, dates)
        pd.testing.assert_series_equal(frame["Close"], expected.dropna().rename("Close"))
        for field in ("Open", "High", "Low"):
            pd.testing.assert_series_equal(frame[field], frame["Close"], check_names=False)

    def test_hy_oas_merges_historical_and_live_without_duplicates(self):
        old = pd.Series([4.0, 4.1], index=pd.to_datetime(["2023-01-02", "2023-01-03"]))
        live = pd.Series([4.1, 4.2], index=pd.to_datetime(["2023-01-03", "2023-01-04"]))
        merged = _merge_historical_and_live_fred(old, live)
        self.assertTrue(merged.index.is_monotonic_increasing)
        self.assertTrue(merged.index.is_unique)
        self.assertEqual(len(merged), 3)

    def test_hy_oas_prefers_live_value_on_overlap(self):
        day = pd.Timestamp("2023-01-03")
        with self.assertWarns(UserWarning):
            merged = _merge_historical_and_live_fred(
                pd.Series([4.0], index=[day]), pd.Series([4.2], index=[day])
            )
        self.assertEqual(merged.loc[day], 4.2)

    def test_hy_oas_aligns_to_qqq_sessions_and_default_lag_is_one(self):
        dates = pd.bdate_range("2025-01-02", periods=4)
        observations = pd.Series([4.0, 4.5], index=dates[[0, 2]])
        aligned = _align_macro_to_qqq_sessions(observations, dates)
        self.assertTrue(pd.isna(aligned.iloc[0]))
        self.assertEqual(aligned.iloc[1:].tolist(), [4.0, 4.0, 4.5])
        self.assertEqual(
            _align_macro_to_qqq_sessions(observations, dates, lag_sessions=2).dropna().tolist(),
            [4.0, 4.0],
        )

    def test_hy_oas_missing_live_source_does_not_destroy_historical_input(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "historical.csv"
            original = "DATE,BAMLH0A0HYM2\n1996-12-31,4.0\n2023-01-03,4.2\n"
            source.write_text(original, encoding="utf-8")
            with patch("downloader._load_fred_series", wraps=_load_fred_series) as read:
                read.side_effect = [_load_fred_series(source), OSError("offline")]
                with self.assertWarns(UserWarning):
                    result = _load_hy_oas_observations(source)
            self.assertEqual(result.index.min(), pd.Timestamp("1996-12-31"))
            self.assertEqual(source.read_text(encoding="utf-8"), original)

    def test_hy_oas_rejects_recent_only_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "historical.csv"
            source.write_text("DATE,VALUE\n2023-09-26,4.04\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not reach"):
                _load_hy_oas_observations(source)

    def test_hy_oas_uses_cached_tail_when_live_is_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "BAMLH0A0HYM2_historical.csv"
            source.write_text(
                "DATE,BAMLH0A0HYM2\n1996-12-31,3.13\n2024-01-02,4.0\n",
                encoding="utf-8",
            )
            (Path(directory) / "BAMLH0A0HYM2.csv").write_text(
                "observation_date,BAMLH0A0HYM2\n2024-01-02,4.1\n2024-01-03,4.2\n",
                encoding="utf-8",
            )
            with patch("downloader._load_fred_series", wraps=_load_fred_series) as read:
                original = _load_fred_series
                def offline_remote(value):
                    if isinstance(value, str) and value.startswith("https:"):
                        raise OSError("offline")
                    return original(value)
                read.side_effect = offline_remote
                with self.assertWarns(UserWarning):
                    merged = _load_hy_oas_observations(source)
            self.assertEqual(merged.loc[pd.Timestamp("2024-01-02")], 4.1)
            self.assertEqual(merged.loc[pd.Timestamp("2024-01-03")], 4.2)

    def test_download_one_writes_lagged_hy_pseudo_market_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            dates = pd.bdate_range("2025-01-02", periods=260)
            pd.DataFrame({"Close": 100.0}, index=dates).rename_axis("Date").to_csv(
                data_dir / "QQQ.csv"
            )
            values = pd.Series(range(len(dates)), index=dates, dtype=float)
            with patch("downloader.ensure_data_files"), patch(
                "downloader._load_hy_oas_observations", return_value=values
            ):
                frame = download_one("BAMLH0A0HYM2", output_dir=data_dir)
            self.assertEqual(frame.iloc[0]["Close"], 0.0)
            self.assertEqual(frame.iloc[1]["Close"], 1.0)
            self.assertTrue((data_dir / "BAMLH0A0HYM2.csv").is_file())


if __name__ == "__main__":
    unittest.main()

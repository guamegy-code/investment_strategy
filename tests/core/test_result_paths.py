from tests import PROJECT_ROOT

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from config import RESULT_DIR
from result_paths import research_result_path


class ResultPathTests(unittest.TestCase):
    def test_research_producer_and_reader_share_topic_directory(self):
        with TemporaryDirectory() as directory:
            with patch("result_paths.RESEARCH_RESULT_DIR", Path(directory)):
                target = research_result_path("strategy30_summary.csv")
                target.write_text("value\n1\n", encoding="utf-8")
                self.assertEqual(target.parent, Path(directory) / "strategy30")
                self.assertEqual(
                    research_result_path("strategy30_summary.csv").read_text(encoding="utf-8"),
                    "value\n1\n",
                )

    def test_reports_cannot_escape_research_directory(self):
        for filename in ("../outside.csv", "nested/report.csv", str(PROJECT_ROOT / "outside.csv")):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                research_result_path(filename)

    def test_backtests_keep_project_strategy_catalog(self):
        from main import STRATEGY_DIRECTORY

        self.assertEqual(RESULT_DIR, PROJECT_ROOT / "results" / "backtests")
        self.assertEqual(STRATEGY_DIRECTORY, PROJECT_ROOT / "strategies")

    def test_partial_run_preserves_existing_static_comparison(self):
        from main import save_results

        with TemporaryDirectory() as directory:
            target = Path(directory) / "static_benchmark_comparison.csv"
            target.write_text("Strategy,CAGR\nSTATIC_example,0.1\n", encoding="utf-8")
            with patch("main.RESULT_DIR", Path(directory)):
                save_results([], all_results=[])
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "Strategy,CAGR\nSTATIC_example,0.1\n",
            )

    def test_static_comparison_is_written_when_benchmarks_are_available(self):
        from types import SimpleNamespace

        import pandas as pd
        from main import save_results

        benchmark = {
            "strategy": SimpleNamespace(display_name="STATIC_example"),
            "history": pd.DataFrame(),
            "summary": {"Strategy": "STATIC_example", "CAGR": 0.1},
        }
        with TemporaryDirectory() as directory:
            with patch("main.RESULT_DIR", Path(directory)):
                save_results([], all_results=[benchmark])
            report = pd.read_csv(Path(directory) / "static_benchmark_comparison.csv")
            self.assertEqual(report["Strategy"].tolist(), ["STATIC_example"])
            self.assertEqual(report["CAGR"].tolist(), [0.1])

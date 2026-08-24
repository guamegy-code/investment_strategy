import base64
import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src" / "legacy-python"))

from backtest import Backtest
from offline_export import WEB_SOURCE_DIR, build_bundle, export_html, export_static_site
from strategy_dsl import DeclarativeStrategy


class OfflineExportTests(unittest.TestCase):
    def test_editable_web_sources_are_used_as_the_export_template(self):
        self.assertTrue((WEB_SOURCE_DIR / "index.html").is_file())
        self.assertTrue((WEB_SOURCE_DIR / "app.js").is_file())
        self.assertIn("__BUNDLE__", (WEB_SOURCE_DIR / "index.html").read_text(
            encoding="utf-8"
        ))

    def test_dashboard_results_exist_before_async_bootstrap_finishes(self):
        runtime = (WEB_SOURCE_DIR / "app.js").read_text(encoding="utf-8")

        declaration = "let dashboardResults = new Map();"
        self.assertIn(declaration, runtime)
        self.assertLess(runtime.index(declaration), runtime.index("async function runDashboard()"))

    def test_static_site_rebuilds_the_tdf_proxy_from_market_components(self):
        runtime = (WEB_SOURCE_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn("TDF2050_PROXY_COMPONENT_WEIGHTS = {SPY:.4081,VXUS:.3339,BND:.258}", runtime)
        self.assertIn("function buildTdf2050Proxy(components)", runtime)
        self.assertIn("await ensureTdf2050Proxy(data,tickers)", runtime)

    def test_browser_refreshes_only_the_cached_market_data_tail(self):
        runtime = (WEB_SOURCE_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn("function latestCachedDate(rows)", runtime)
        self.assertIn("function mergeTickerRows(existing,incoming)", runtime)
        self.assertIn("endpoint.searchParams.set('start',startDate)", runtime)
        self.assertIn("end.value=last;", runtime)

    def test_strategy_buttons_show_the_yaml_filename(self):
        runtime = (WEB_SOURCE_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn("function strategyYamlFilename(definition)", runtime)
        self.assertIn("function syncStrategyButtonMetadata()", runtime)
        self.assertIn("new MutationObserver(syncStrategyButtonMetadata)", runtime)
        self.assertIn("def._yaml_file=file.name", runtime)

    def test_bundle_embeds_yaml_definitions_and_compressed_market_csv(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            data_dir = root / "data"
            strategy_dir = root / "strategies"
            data_dir.mkdir()
            strategy_dir.mkdir()
            (data_dir / "QQQ.csv").write_text(
                "Date,Open,Close\n2024-01-01,100,101\n", encoding="utf-8"
            )
            (strategy_dir / "sample.yaml").write_text(
                "strategy:\n  id: sample\n  name: Sample\n  version: 1\n"
                "assets:\n  required: [QQQ]\n"
                "target:\n  - weights:\n      QQQ: 100%\n",
                encoding="utf-8",
            )

            bundle = build_bundle(data_dir, strategy_dir)
            market = json.loads(gzip.decompress(
                base64.b64decode(bundle["data"])
            ))

            self.assertEqual(bundle["strategies"][0]["strategy"]["id"], "sample")
            self.assertEqual(bundle["strategies"][0]["_yaml_file"], "sample.yaml")
            self.assertEqual(market["QQQ"], "Date,Open,Close\n2024-01-01,100,101\n")
            self.assertRegex(bundle["market_data_version"], r"^[0-9a-f]{16}$")

    def test_bundle_includes_optional_data_proxy_url(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            data_dir = root / "data"
            strategy_dir = root / "strategies"
            data_dir.mkdir()
            strategy_dir.mkdir()

            bundle = build_bundle(
                data_dir, strategy_dir,
                data_proxy="https://strategy-data.example.workers.dev",
            )

            self.assertEqual(
                bundle["data_proxy"], "https://strategy-data.example.workers.dev"
            )

    def test_precomputed_results_replace_missing_state_with_empty_text(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            data_dir = root / "data"
            strategy_dir = root / "strategies"
            result_dir = root / "results"
            data_dir.mkdir()
            strategy_dir.mkdir()
            result_dir.mkdir()
            (strategy_dir / "sample.yaml").write_text(
                "strategy:\n  id: sample\n  name: Sample\n  version: 1\n",
                encoding="utf-8",
            )
            (result_dir / "Sample_history.csv").write_text(
                "Date,Portfolio,Weights,StrategyState\n"
                "2024-01-02,1.0,{'QQQ': 1.0},\n",
                encoding="utf-8",
            )

            with patch("offline_export.RESULT_DIR", result_dir):
                bundle = build_bundle(data_dir, strategy_dir)
            results = json.loads(gzip.decompress(
                base64.b64decode(bundle["precomputed_results"])
            ))

            self.assertEqual(results["sample"][0]["state"], "")

    def test_stale_precomputed_results_are_excluded_after_yaml_changes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            data_dir = root / "data"
            strategy_dir = root / "strategies"
            result_dir = root / "results"
            data_dir.mkdir()
            strategy_dir.mkdir()
            result_dir.mkdir()
            strategy_path = strategy_dir / "sample.yaml"
            strategy_path.write_text(
                "strategy:\n  id: sample\n  name: Sample\n  version: 1\n",
                encoding="utf-8",
            )
            history_path = result_dir / "Sample_history.csv"
            history_path.write_text(
                "Date,Portfolio,Weights,StrategyState\n"
                "2024-01-02,1.0,{'QQQ': 1.0},\n",
                encoding="utf-8",
            )
            os.utime(history_path, (1, 1))

            with patch("offline_export.RESULT_DIR", result_dir):
                bundle = build_bundle(data_dir, strategy_dir)
            results = json.loads(gzip.decompress(
                base64.b64decode(bundle["precomputed_results"])
            ))

            self.assertNotIn("sample", results)

    def test_precomputed_results_include_rebalance_targets(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            data_dir = root / "data"
            strategy_dir = root / "strategies"
            result_dir = root / "results"
            data_dir.mkdir()
            strategy_dir.mkdir()
            result_dir.mkdir()
            (strategy_dir / "sample.yaml").write_text(
                "strategy:\n  id: sample\n  name: Sample\n  version: 1\n",
                encoding="utf-8",
            )
            (result_dir / "Sample_history.csv").write_text(
                "Date,Portfolio,Weights,StrategyState\n"
                "2024-01-02,1.0,{'QQQ': 1.0},\n",
                encoding="utf-8",
            )
            (result_dir / "Sample_rebalances.json").write_text(
                json.dumps([{
                    "Date": "2024-01-02",
                    "Target": {"QQQ": 0.75, "BIL": 0.25},
                    "PreWeights": {"QQQ": 1.0, "BIL": 0.0},
                    "ExecutionDays": 3,
                }]),
                encoding="utf-8",
            )

            with patch("offline_export.RESULT_DIR", result_dir):
                bundle = build_bundle(data_dir, strategy_dir)
            results = json.loads(gzip.decompress(
                base64.b64decode(bundle["precomputed_results"])
            ))

            row = results["sample"][0]
            self.assertEqual(row["target"], {"QQQ": 0.75, "BIL": 0.25})
            self.assertEqual(row["preWeights"], {"QQQ": 1.0, "BIL": 0.0})
            self.assertEqual(row["executionDays"], 3)

    def test_precomputed_results_place_rebalance_on_execution_date(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            data_dir = root / "data"
            strategy_dir = root / "strategies"
            result_dir = root / "results"
            data_dir.mkdir()
            strategy_dir.mkdir()
            result_dir.mkdir()
            (strategy_dir / "sample.yaml").write_text(
                "strategy:\n  id: sample\n  name: Sample\n  version: 1\n",
                encoding="utf-8",
            )
            (result_dir / "Sample_history.csv").write_text(
                "Date,Portfolio,Weights,StrategyState\n"
                "2024-01-02,1.0,{'QQQ': 1.0},\n"
                "2024-01-03,1.0,{'QQQ': 0.8},\n",
                encoding="utf-8",
            )
            (result_dir / "Sample_rebalances.json").write_text(
                json.dumps([{
                    "Date": "2024-01-02",
                    "ExecutionDate": "2024-01-03",
                    "Target": {"QQQ": 0.8},
                }]),
                encoding="utf-8",
            )

            with patch("offline_export.RESULT_DIR", result_dir):
                bundle = build_bundle(data_dir, strategy_dir)
            results = json.loads(gzip.decompress(
                base64.b64decode(bundle["precomputed_results"])
            ))["sample"]

            self.assertIsNone(results[0]["target"])
            self.assertEqual(results[1]["target"], {"QQQ": 0.8})

    def test_export_is_a_single_offline_html_document(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            data_dir = root / "data"
            strategy_dir = root / "strategies"
            data_dir.mkdir()
            strategy_dir.mkdir()
            (data_dir / "QQQ.csv").write_text(
                "Date,Open,Close\n2024-01-01,100,101\n", encoding="utf-8"
            )
            (strategy_dir / "sample.yaml").write_text(
                "strategy:\n  id: sample\n  name: Sample\n  version: 1\n"
                "assets:\n  required: [QQQ]\n"
                "target:\n  - weights:\n      QQQ: 100%\n",
                encoding="utf-8",
            )
            output = export_html(root / "offline.html", data_dir=data_dir, strategy_dir=strategy_dir)
            document = output.read_text(encoding="utf-8")

            self.assertIn("Investment Strategy Research", document)
            self.assertIn("sample", document)
            self.assertIn("DecompressionStream", document)
            self.assertIn('id="reset"', document)
            self.assertIn("async function resetUserState()", document)
            self.assertIn("transaction.objectStore(name).clear()", document)
            self.assertIn("function indicatorTickerData(data)", document)
            self.assertIn("if(!ticker.endsWith('=X'))visible.add(ticker)", document)
            self.assertNotIn("<script src=", document)

    def test_static_site_uses_manifest_and_excludes_market_data(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            strategy_dir = root / "strategies"
            strategy_dir.mkdir()
            (strategy_dir / "sample.yaml").write_text(
                "strategy:\n  id: sample\n  name: Sample\n  version: 1\n"
                "assets:\n  required: [QQQ]\n"
                "target:\n  - weights:\n      QQQ: 100%\n",
                encoding="utf-8",
            )
            legacy_proxy = root / "site" / "data" / "TDF2050_PROXY.csv"
            legacy_proxy.parent.mkdir(parents=True)
            legacy_proxy.write_text("legacy", encoding="utf-8")

            output = export_static_site(
                root / "site",
                strategy_dir=strategy_dir,
                data_proxy="https://strategy-data.example.workers.dev",
            )
            document = output.read_text(encoding="utf-8")
            manifest = json.loads((root / "site" / "strategies" / "manifest.json").read_text(
                encoding="utf-8"
            ))

            self.assertTrue((root / "site" / "app.js").is_file())
            self.assertTrue((root / "site" / "assets" / "app.css").is_file())
            self.assertTrue((root / "site" / "assets" / "plotly.min.js").is_file())
            self.assertTrue((root / "site" / "strategies" / "sample.yaml").is_file())
            self.assertEqual(manifest["strategies"], [{"id": "sample", "path": "sample.yaml", "version": 1}])
            self.assertIn('"strategy_manifest_url": "./strategies/manifest.json"', document)
            self.assertIn('"static_site": true', document)
            self.assertNotIn('"data": "H4sI', document)
            self.assertFalse(legacy_proxy.exists())

    def test_export_can_write_market_data_as_a_sidecar_file(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            data_dir = root / "data"
            strategy_dir = root / "strategies"
            data_dir.mkdir()
            strategy_dir.mkdir()
            (data_dir / "QQQ.csv").write_text(
                "Date,Open,Close\n2024-01-01,100,101\n", encoding="utf-8"
            )
            (strategy_dir / "sample.yaml").write_text(
                "strategy:\n  id: sample\n  name: Sample\n  version: 1\n",
                encoding="utf-8",
            )

            output = export_html(
                root / "offline.html",
                data_dir=data_dir,
                strategy_dir=strategy_dir,
                external_market_data=True,
            )
            document = output.read_text(encoding="utf-8")
            sidecar = root / "offline.market-data.json.gz"

            self.assertTrue(sidecar.is_file())
            self.assertIn('"data_url": "offline.market-data.json.gz"', document)
            self.assertNotIn('"data": "H4sI', document)

    @unittest.skipUnless(
        os.environ.get("RUN_OFFLINE_BROWSER_TESTS")
        and (shutil.which("msedge") or Path(
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
        ).exists()),
        "set RUN_OFFLINE_BROWSER_TESTS=1 to run the local Microsoft Edge validation",
    )
    def test_browser_runtime_matches_python_for_a_fixed_weight_strategy(self):
        """Run the generated file in a real browser, without a web server."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            data_dir = root / "data"
            strategy_dir = root / "strategies"
            data_dir.mkdir()
            strategy_dir.mkdir()
            csv = (
                "Date,Open,High,Low,Close,Volume\n"
                "2024-01-02,100,100,100,100,1\n"
                "2024-01-03,110,110,110,110,1\n"
                "2024-01-04,121,121,121,121,1\n"
            )
            (data_dir / "QQQ.csv").write_text(csv, encoding="utf-8")
            definition = {
                "strategy": {"id": "sample", "name": "Sample", "version": 1},
                "assets": {"required": ["QQQ"]},
                "target": [{"weights": {"QQQ": "100%"}}],
            }
            (strategy_dir / "sample.yaml").write_text(
                yaml.safe_dump(definition, sort_keys=False), encoding="utf-8"
            )
            strategy = DeclarativeStrategy(definition)
            expected = Backtest(strategy, data_dir=data_dir, tickers=["QQQ"]).run()
            expected_value = expected.iloc[-1]["Portfolio"]

            output = export_html(
                root / "offline.html", data_dir=data_dir, strategy_dir=strategy_dir
            )
            probe = """<script>(async()=>{try{const h=runWithData(definitions[0],await loadData());document.body.dataset.offlineResult=h.at(-1).value;}catch(e){document.body.dataset.offlineError=e.message;}})();</script></html>"""
            output.write_text(
                output.read_text(encoding="utf-8").replace("</html>", probe),
                encoding="utf-8",
            )
            edge = shutil.which("msedge") or r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
            result = subprocess.run(
                [
                    edge,
                    "--headless=new",
                    "--disable-gpu",
                    "--disable-software-rasterizer",
                    f"--user-data-dir={root / 'edge-profile'}",
                    "--virtual-time-budget=3000",
                    "--dump-dom",
                    output.as_uri(),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr[-1000:])
            self.assertNotIn("data-offline-error=", result.stdout)
            marker = 'data-offline-result="'
            self.assertIn(marker, result.stdout)
            actual_value = float(result.stdout.split(marker, 1)[1].split('"', 1)[0])
            self.assertAlmostEqual(actual_value, expected_value, places=10)

    @unittest.skipUnless(
        os.environ.get("RUN_OFFLINE_BROWSER_TESTS")
        and (shutil.which("msedge") or Path(
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
        ).exists()),
        "set RUN_OFFLINE_BROWSER_TESTS=1 to run the local Microsoft Edge validation",
    )
    def test_browser_runtime_matches_python_state_and_rebalance_dates(self):
        """Imported DSL state changes keep the same dates and weights in-browser."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            data_dir = root / "data"
            strategy_dir = root / "strategies"
            data_dir.mkdir()
            strategy_dir.mkdir()
            rows = ["Date,Open,High,Low,Close,Volume"]
            closes = [100, 101, 99, 95, 94, 93, 110, 111]
            for day, close in enumerate(closes, start=2):
                date = f"2024-01-{day:02d}"
                rows.append(f"{date},{close},{close},{close},{close},1")
            for ticker in ("QQQ", "BIL"):
                (data_dir / f"{ticker}.csv").write_text(
                    "\n".join(rows) + "\n", encoding="utf-8"
                )
            definition = {
                "strategy": {"id": "stateful", "name": "Stateful", "version": 1},
                "assets": {"required": ["QQQ", "BIL"]},
                "state": {
                    "mode": {
                        "initial": "BULL",
                        "check": "daily",
                        "rules": [
                            {"when": "QQQ.Close < 98", "set": "BEAR", "confirm": 2},
                            {"otherwise": True, "set": "BULL", "confirm": 2},
                        ],
                    }
                },
                "target": [
                    {"when": "state.mode == 'BEAR'", "weights": {"BIL": "100%"}},
                    {"weights": {"QQQ": "100%"}},
                ],
                "rebalance": [{"when": "changed(state.mode)", "check": "daily", "days": 2}],
            }
            (strategy_dir / "stateful.yaml").write_text(
                yaml.safe_dump(definition, sort_keys=False), encoding="utf-8"
            )
            expected = Backtest(
                DeclarativeStrategy(definition), data_dir=data_dir,
                tickers=["QQQ", "BIL"],
            ).run()
            output = export_html(
                root / "offline.html", data_dir=data_dir, strategy_dir=strategy_dir
            )
            probe = """<script>(async()=>{try{const h=runWithData(definitions[0],await loadData());document.body.dataset.offlineHistory=JSON.stringify(h.map(row=>({date:row.date,weights:row.weights})));}catch(e){document.body.dataset.offlineError=e.message;}})();</script></html>"""
            output.write_text(
                output.read_text(encoding="utf-8").replace("</html>", probe),
                encoding="utf-8",
            )
            edge = shutil.which("msedge") or r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
            result = subprocess.run(
                [
                    edge, "--headless=new", "--disable-gpu",
                    "--disable-software-rasterizer",
                    f"--user-data-dir={root / 'edge-profile'}",
                    "--virtual-time-budget=3000", "--dump-dom", output.as_uri(),
                ],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=30, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr[-1000:])
            self.assertNotIn("data-offline-error=", result.stdout)
            marker = 'data-offline-history="'
            self.assertIn(marker, result.stdout)
            encoded = result.stdout.split(marker, 1)[1].split('"', 1)[0]
            actual = json.loads(encoded.replace("&quot;", '"'))
            self.assertEqual(
                [item["date"] for item in actual],
                [str(date.date()) for date in expected.index],
            )
            for browser_row, (_, python_row) in zip(actual, expected.iterrows()):
                python_weights = ast.literal_eval(python_row["Weights"])
                self.assertEqual(set(browser_row["weights"]), set(python_weights))
                for ticker, weight in python_weights.items():
                    self.assertAlmostEqual(
                        browser_row["weights"][ticker], weight, places=10
                    )


if __name__ == "__main__":
    unittest.main()

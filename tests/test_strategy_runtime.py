from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from strategy_runtime import (
    IncrementalStrategyResults,
    ReloadableStrategyResults,
    StrategyResultDiskCache,
    strategy_calculation_fingerprint,
    strategy_files_fingerprint,
)


class FakeStrategy:
    def __init__(self, identity, name, rule, *, source=None):
        self.strategy_id = identity
        self.display_name = name
        self.STRATEGY_VERSION = "1"
        self.definition = {
            "strategy": {
                "id": identity,
                "name": name,
                "version": 1,
                "enabled": True,
            },
            "parameters": {"rule": rule},
        }
        if source is not None:
            self.source_strategy = source


def fake_result(strategy, value):
    return {
        "strategy": strategy,
        "value": value,
        "summary": {"Strategy": strategy.display_name},
    }


class ReloadableStrategyResultsTests(unittest.TestCase):
    def test_disk_cache_restores_result_with_current_display_metadata(self):
        with TemporaryDirectory() as directory:
            cache = StrategyResultDiskCache(directory)
            original = FakeStrategy("cached", "Old Name", 1)
            renamed = FakeStrategy("cached", "New Name", 1)
            cache.save(fake_result(original, "completed"), "valid-key")

            restored = cache.load(renamed, "valid-key")

            self.assertEqual(restored["value"], "completed")
            self.assertIs(restored["strategy"], renamed)
            self.assertEqual(restored["summary"]["Strategy"], "New Name")
            self.assertIsNone(cache.load(renamed, "different-key"))

    def test_incremental_reload_reuses_metadata_changes_and_runs_new_logic(self):
        first = FakeStrategy("first", "First", 1)
        removed = FakeStrategy("removed", "Removed", 1)
        initial_results = (
            fake_result(first, "first cached"),
            fake_result(removed, "removed cached"),
        )
        renamed = FakeStrategy("first", "First Renamed", 1)
        added = FakeStrategy("added", "Added", 2)
        executions = []
        publications = []

        def execute(strategies):
            strategies = tuple(strategies)
            executions.append([item.strategy_id for item in strategies])
            return tuple(fake_result(item, "executed") for item in strategies)

        incremental = IncrementalStrategyResults(
            (first, removed),
            initial_results,
            strategy_loader=lambda: (renamed, added),
            executor=execute,
            publisher=lambda changed, merged: publications.append(
                (tuple(changed), tuple(merged))
            ),
        )

        reloaded = incremental.reload()

        self.assertEqual(executions, [["added"]])
        self.assertEqual(
            [item["strategy"].display_name for item in reloaded],
            ["First Renamed", "Added"],
        )
        self.assertEqual(reloaded[0]["value"], "first cached")
        self.assertEqual(reloaded[0]["summary"]["Strategy"], "First Renamed")
        self.assertEqual(len(publications[0][0]), 2)

    def test_calculation_fingerprint_includes_resolved_source_logic(self):
        source_one = FakeStrategy("source", "Source", 1)
        source_two = FakeStrategy("source", "Source", 2)
        mapped_one = FakeStrategy("mapped", "Mapped", 1, source=source_one)
        mapped_two = FakeStrategy("mapped", "Mapped", 1, source=source_two)
        renamed = FakeStrategy("mapped", "Renamed", 1, source=source_one)

        self.assertNotEqual(
            strategy_calculation_fingerprint(mapped_one),
            strategy_calculation_fingerprint(mapped_two),
        )
        self.assertEqual(
            strategy_calculation_fingerprint(mapped_one),
            strategy_calculation_fingerprint(renamed),
        )

    def test_fingerprint_changes_for_edit_add_and_remove(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.yaml"
            first.write_text("strategy: one\n", encoding="utf-8")
            initial = strategy_files_fingerprint(root)

            first.write_text("strategy: two\n", encoding="utf-8")
            edited = strategy_files_fingerprint(root)
            second = root / "second.yml"
            second.write_text("strategy: three\n", encoding="utf-8")
            added = strategy_files_fingerprint(root)
            second.unlink()
            removed = strategy_files_fingerprint(root)

            self.assertNotEqual(initial, edited)
            self.assertNotEqual(edited, added)
            self.assertEqual(edited, removed)

    def test_unchanged_files_do_not_run_loader(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "strategy.yaml").write_text("version: 1\n", encoding="utf-8")
            calls = []
            store = ReloadableStrategyResults(
                ({"value": "old"},),
                strategy_directory=root,
                loader=lambda: calls.append(True) or ({"value": "new"},),
            )

            snapshot = store.refresh_if_changed()

            self.assertEqual(snapshot.results[0]["value"], "old")
            self.assertEqual(snapshot.version, 1)
            self.assertEqual(calls, [])

    def test_changed_files_publish_new_results_once(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "strategy.yaml"
            source.write_text("version: 1\n", encoding="utf-8")
            calls = []
            store = ReloadableStrategyResults(
                ({"value": "old"},),
                strategy_directory=root,
                loader=lambda: calls.append(True) or ({"value": "new"},),
            )
            source.write_text("version: 2\n", encoding="utf-8")

            first = store.refresh_if_changed()
            second = store.refresh_if_changed()

            self.assertEqual(first.results[0]["value"], "new")
            self.assertEqual(first.version, 2)
            self.assertIsNone(first.error)
            self.assertEqual(second, first)
            self.assertEqual(len(calls), 1)

    def test_invalid_change_keeps_last_results_until_yaml_changes_again(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "strategy.yaml"
            source.write_text("version: 1\n", encoding="utf-8")
            calls = []

            def fail():
                calls.append(True)
                raise ValueError("invalid target")

            store = ReloadableStrategyResults(
                ({"value": "old"},), strategy_directory=root, loader=fail,
            )
            source.write_text("version: broken\n", encoding="utf-8")

            failed = store.refresh_if_changed()
            repeated = store.refresh_if_changed()

            self.assertEqual(failed.results[0]["value"], "old")
            self.assertEqual(failed.version, 1)
            self.assertIn("invalid target", failed.error)
            self.assertEqual(repeated, failed)
            self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()

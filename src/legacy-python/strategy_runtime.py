"""Reload strategy results when declarative strategy files change."""

from __future__ import annotations

from dataclasses import dataclass
import gzip
from hashlib import sha256
import json
from pathlib import Path
import pickle
from threading import Lock, RLock
from typing import Any, Callable, Iterable

from strategy_domain import (
    strategy_display_name,
    strategy_identity,
    strategy_version,
)


@dataclass(frozen=True)
class StrategyResultSnapshot:
    """One atomically published set of completed strategy results."""

    results: tuple[dict[str, Any], ...]
    version: int
    error: str | None = None


def strategy_files_fingerprint(directory: str | Path) -> str:
    """Hash YAML names and contents so additions, edits, and removals are detected."""
    root = Path(directory)
    digest = sha256()
    paths = sorted((*root.glob("*.yaml"), *root.glob("*.yml")))
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def strategy_calculation_fingerprint(strategy: Any) -> str:
    """Hash only fields that can change a strategy's calculated result."""

    def calculation_definition(item: Any) -> dict[str, Any]:
        definition = getattr(item, "definition", None)
        if not isinstance(definition, dict):
            return {"identity": strategy_identity(item)}
        normalized = json.loads(json.dumps(definition, default=str))
        metadata = normalized.get("strategy", {})
        for key in ("name", "version", "enabled"):
            metadata.pop(key, None)
        source = getattr(item, "source_strategy", None)
        if source is not None:
            normalized["resolved_source"] = calculation_definition(source)
        return normalized

    encoded = json.dumps(
        calculation_definition(strategy),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


class IncrementalStrategyResults:
    """Reuse unchanged backtests and execute only changed strategy definitions."""

    def __init__(
        self,
        strategies: Iterable[Any],
        results: Iterable[dict[str, Any]],
        *,
        strategy_loader: Callable[[], Iterable[Any]],
        executor: Callable[[Iterable[Any]], Iterable[dict[str, Any]]],
        publisher: Callable[
            [Iterable[dict[str, Any]], Iterable[dict[str, Any]]], None
        ] | None = None,
    ):
        strategies = tuple(strategies)
        results = tuple(results)
        self.strategy_loader = strategy_loader
        self.executor = executor
        self.publisher = publisher
        self._results = {
            strategy_identity(result["strategy"]): result for result in results
        }
        self._fingerprints = {
            strategy_identity(strategy): strategy_calculation_fingerprint(strategy)
            for strategy in strategies
        }

    @staticmethod
    def _reuse_result(
        previous: dict[str, Any], strategy: Any
    ) -> tuple[dict[str, Any], bool]:
        old_strategy = previous["strategy"]
        metadata_changed = (
            strategy_display_name(old_strategy) != strategy_display_name(strategy)
            or strategy_version(old_strategy) != strategy_version(strategy)
        )
        reused = {**previous, "strategy": strategy}
        summary = dict(previous.get("summary", {}))
        summary["Strategy"] = strategy_display_name(strategy)
        reused["summary"] = summary
        return reused, metadata_changed

    def reload(self) -> tuple[dict[str, Any], ...]:
        strategies = tuple(self.strategy_loader())
        fingerprints = {
            strategy_identity(strategy): strategy_calculation_fingerprint(strategy)
            for strategy in strategies
        }
        pending = []
        reused_by_id: dict[str, dict[str, Any]] = {}
        publish_results: list[dict[str, Any]] = []
        for strategy in strategies:
            identity = strategy_identity(strategy)
            previous = self._results.get(identity)
            if (
                previous is not None
                and self._fingerprints.get(identity) == fingerprints[identity]
            ):
                reused, metadata_changed = self._reuse_result(previous, strategy)
                reused_by_id[identity] = reused
                if metadata_changed:
                    publish_results.append(reused)
            else:
                pending.append(strategy)

        executed = tuple(self.executor(pending)) if pending else ()
        executed_by_id = {
            strategy_identity(result["strategy"]): result for result in executed
        }
        publish_results.extend(executed)
        merged = tuple(
            executed_by_id.get(strategy_identity(strategy))
            or reused_by_id[strategy_identity(strategy)]
            for strategy in strategies
        )
        if self.publisher is not None:
            self.publisher(tuple(publish_results), merged)
        self._results = {
            strategy_identity(result["strategy"]): result for result in merged
        }
        self._fingerprints = fingerprints
        return merged


class StrategyResultDiskCache:
    """Persist completed backtest results between application launches."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    def _path(self, strategy: Any) -> Path:
        identity_hash = sha256(
            strategy_identity(strategy).encode("utf-8")
        ).hexdigest()[:24]
        return self.directory / f"{identity_hash}.pickle.gz"

    def load(self, strategy: Any, cache_key: str) -> dict[str, Any] | None:
        path = self._path(strategy)
        if not path.exists():
            return None
        try:
            with gzip.open(path, "rb") as stream:
                payload = pickle.load(stream)
        except (OSError, EOFError, pickle.PickleError, AttributeError, ValueError):
            return None
        if not isinstance(payload, dict) or payload.get("cache_key") != cache_key:
            return None
        cached = payload.get("result")
        if not isinstance(cached, dict):
            return None
        result = {**cached, "strategy": strategy}
        summary = dict(result.get("summary", {}))
        summary["Strategy"] = strategy_display_name(strategy)
        result["summary"] = summary
        return result

    def save(
        self, result: dict[str, Any], cache_key: str
    ) -> None:
        strategy = result["strategy"]
        self.directory.mkdir(parents=True, exist_ok=True)
        destination = self._path(strategy)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        cached = {key: value for key, value in result.items() if key != "strategy"}
        payload = {"cache_key": cache_key, "result": cached}
        with gzip.open(temporary, "wb", compresslevel=1) as stream:
            pickle.dump(payload, stream, protocol=pickle.HIGHEST_PROTOCOL)
        temporary.replace(destination)


class ReloadableStrategyResults:
    """Keep the last valid results and reload them after YAML content changes."""

    def __init__(
        self,
        results: Iterable[dict[str, Any]],
        *,
        strategy_directory: str | Path,
        loader: Callable[[], Iterable[dict[str, Any]]] | None = None,
    ):
        self.strategy_directory = Path(strategy_directory)
        self.loader = loader
        self._state_lock = RLock()
        self._reload_lock = Lock()
        self._fingerprint = strategy_files_fingerprint(self.strategy_directory)
        self._attempted_fingerprint = self._fingerprint
        self._snapshot = StrategyResultSnapshot(tuple(results), version=1)

    def snapshot(self) -> StrategyResultSnapshot:
        with self._state_lock:
            return self._snapshot

    def refresh_if_changed(self) -> StrategyResultSnapshot:
        """Publish newly calculated results, or retain the previous valid snapshot."""
        if self.loader is None:
            return self.snapshot()
        fingerprint = strategy_files_fingerprint(self.strategy_directory)
        with self._state_lock:
            if fingerprint == self._attempted_fingerprint:
                return self._snapshot

        with self._reload_lock:
            fingerprint = strategy_files_fingerprint(self.strategy_directory)
            with self._state_lock:
                if fingerprint == self._attempted_fingerprint:
                    return self._snapshot
                self._attempted_fingerprint = fingerprint
            try:
                results = tuple(self.loader())
                if not results:
                    raise ValueError("활성화된 YAML 전략이 없습니다.")
            except Exception as exc:
                with self._state_lock:
                    current = self._snapshot
                    self._snapshot = StrategyResultSnapshot(
                        current.results,
                        version=current.version,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    return self._snapshot

            with self._state_lock:
                current = self._snapshot
                self._fingerprint = fingerprint
                self._snapshot = StrategyResultSnapshot(
                    results,
                    version=current.version + 1,
                )
                return self._snapshot

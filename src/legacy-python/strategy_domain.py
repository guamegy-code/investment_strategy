"""UI-independent contracts for evaluating allocation strategies.

The existing strategy classes are stateful because a backtest advances one
trading day at a time.  This module makes that state and each decision explicit
so the same strategy can later be called safely from an API or a scheduled job.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from math import isfinite
from types import MappingProxyType
from typing import Any


def strategy_identity(strategy: object) -> str:
    """Return a stable code identity independent of a display class name."""
    explicit = getattr(strategy, "strategy_id", None)
    if explicit:
        return str(explicit)
    strategy_class = strategy.__class__
    return f"{strategy_class.__module__}.{strategy_class.__qualname__}"


def strategy_version(strategy: object) -> str:
    """Return the explicitly declared strategy contract version."""
    return str(getattr(strategy, "STRATEGY_VERSION", "1"))


def strategy_display_name(strategy: object) -> str:
    """Return a human-facing name for Python and declarative strategies."""
    return str(
        getattr(strategy, "display_name", None)
        or strategy.__class__.__name__
    )


def _state_label(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _timestamp_text(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())
    return str(value)


@dataclass(frozen=True)
class MarketSnapshot:
    """Market observations evaluated together at one point in time."""

    as_of: Any
    _assets: Mapping[str, Mapping[str, Any]] = field(repr=False)

    def __init__(self, as_of: Any, assets: Mapping[str, Mapping[str, Any]]):
        if as_of is None:
            raise ValueError("as_of is required")
        if not isinstance(assets, Mapping) or not assets:
            raise ValueError("assets must be a non-empty mapping")
        copied = {
            str(ticker): MappingProxyType(deepcopy(dict(observations)))
            for ticker, observations in assets.items()
        }
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "_assets", MappingProxyType(copied))

    @property
    def assets(self) -> dict[str, dict[str, Any]]:
        """Return an isolated legacy mapping for existing strategy classes."""
        return {
            ticker: deepcopy(dict(observations))
            for ticker, observations in self._assets.items()
        }

    def to_dict(self) -> dict[str, Any]:
        return {"as_of": _timestamp_text(self.as_of), "assets": self.assets}


@dataclass(frozen=True)
class StrategyRuntimeState:
    """An isolated snapshot of one versioned strategy instance.

    Strategy-specific fields are intentionally retained instead of flattening
    every strategy into the retirement strategy's state model.  That preserves
    online-learning buffers and experimental strategy state without coupling
    the service layer to individual subclasses.
    """

    strategy_id: str
    strategy_version: str
    _values: Mapping[str, Any] = field(repr=False)

    def __init__(
        self,
        strategy_id: str,
        strategy_version: str,
        values: Mapping[str, Any],
    ):
        object.__setattr__(self, "strategy_id", str(strategy_id))
        object.__setattr__(self, "strategy_version", str(strategy_version))
        object.__setattr__(
            self,
            "_values",
            MappingProxyType(deepcopy(dict(values))),
        )

    @classmethod
    def capture(cls, strategy: object) -> "StrategyRuntimeState":
        return cls(
            strategy_identity(strategy),
            strategy_version(strategy),
            getattr(strategy, "__dict__", {}),
        )

    @property
    def values(self) -> dict[str, Any]:
        """Return a copy so callers cannot mutate the saved state."""
        return deepcopy(dict(self._values))

    def apply_to(self, strategy: object) -> None:
        """Restore this snapshot after verifying strategy identity and version."""
        actual_id = strategy_identity(strategy)
        actual_version = strategy_version(strategy)
        if actual_id != self.strategy_id:
            raise ValueError(
                f"runtime state belongs to {self.strategy_id}, not {actual_id}"
            )
        if actual_version != self.strategy_version:
            raise ValueError(
                "runtime state strategy version does not match evaluator version"
            )
        strategy.__dict__.clear()
        strategy.__dict__.update(self.values)


@dataclass(frozen=True)
class StrategyEvaluation:
    """A versioned allocation decision returned by the strategy engine."""

    strategy_id: str
    strategy_version: str
    evaluated_at: Any
    previous_state: str | None
    next_state: str | None
    rebalance_required: bool
    target_weights: dict[str, float]
    execution_days: int
    reason: str | None = None

    @classmethod
    def from_signal(
        cls,
        *,
        strategy: object,
        evaluated_at: Any,
        previous_state: Any,
        signal: Mapping[str, Any],
    ) -> "StrategyEvaluation":
        if not isinstance(signal, Mapping):
            raise TypeError("strategy evaluate() must return a mapping")
        target = {
            str(ticker): float(weight)
            for ticker, weight in dict(signal.get("target", {})).items()
        }
        if not target:
            raise ValueError("strategy evaluation target must not be empty")
        if any(not isfinite(weight) or weight < 0.0 for weight in target.values()):
            raise ValueError("strategy target weights must be finite and non-negative")
        execution_days = int(signal.get("days", 1))
        if execution_days <= 0:
            raise ValueError("strategy execution days must be positive")
        reason = signal.get("reason")
        if reason is not None:
            reason = str(reason)
        return cls(
            strategy_id=strategy_identity(strategy),
            strategy_version=strategy_version(strategy),
            evaluated_at=evaluated_at,
            previous_state=_state_label(previous_state),
            next_state=_state_label(getattr(strategy, "state", None)),
            rebalance_required=bool(signal.get("rebalance", False)),
            target_weights=target,
            execution_days=execution_days,
            reason=reason,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the stable API-facing representation of this decision."""
        return {
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "evaluated_at": _timestamp_text(self.evaluated_at),
            "previous_state": self.previous_state,
            "next_state": self.next_state,
            "rebalance_required": self.rebalance_required,
            "target_weights": self.target_weights.copy(),
            "execution_days": self.execution_days,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class StrategyStep:
    """One decision together with the state needed for the next evaluation."""

    evaluation: StrategyEvaluation
    next_runtime_state: StrategyRuntimeState | None


class StrategyIsolationError(RuntimeError):
    """Raised when a legacy strategy cannot support isolated evaluation."""


class StrategyEngine:
    """Evaluate a stateful strategy through explicit state/result contracts."""

    def __init__(self, strategy: object):
        if not callable(getattr(strategy, "evaluate", None)):
            raise TypeError("strategy must define evaluate(date, market, portfolio)")
        self.strategy = strategy
        self._template = None
        self._initial_runtime_state = None
        try:
            template = deepcopy(strategy)
            initial_state = StrategyRuntimeState.capture(template)
        except Exception:
            # Sequential backtests historically did not require strategies to
            # be deepcopy-compatible. Keep that path available to third-party
            # strategies that own locks, sessions, or other external handles.
            pass
        else:
            self._template = template
            self._initial_runtime_state = initial_state

    @property
    def initial_runtime_state(self) -> StrategyRuntimeState:
        if self._initial_runtime_state is None:
            raise StrategyIsolationError(
                "strategy state cannot be isolated; sequential evaluation remains available"
            )
        return StrategyRuntimeState(
            self._initial_runtime_state.strategy_id,
            self._initial_runtime_state.strategy_version,
            self._initial_runtime_state.values,
        )

    @property
    def runtime_state(self) -> StrategyRuntimeState:
        return StrategyRuntimeState.capture(self.strategy)

    @staticmethod
    def _evaluate_strategy(
        strategy: object,
        market: MarketSnapshot,
        portfolio: object,
        *,
        capture_runtime_state: bool,
    ) -> StrategyStep:
        previous_state = getattr(strategy, "state", None)
        signal = strategy.evaluate(market.as_of, market.assets, portfolio)
        evaluation = StrategyEvaluation.from_signal(
            strategy=strategy,
            evaluated_at=market.as_of,
            previous_state=previous_state,
            signal=signal,
        )
        next_runtime_state = None
        if capture_runtime_state:
            try:
                next_runtime_state = StrategyRuntimeState.capture(strategy)
            except Exception:
                # A typed decision is still useful for a legacy strategy whose
                # internal resources cannot be copied. Persistence support can
                # be opted into later without breaking its backtest contract.
                pass
        return StrategyStep(evaluation, next_runtime_state)

    def evaluate(
        self,
        market: MarketSnapshot,
        portfolio: object,
        runtime_state: StrategyRuntimeState | None = None,
    ) -> StrategyStep:
        """Evaluate an isolated copy without mutating the engine strategy."""
        if self._template is None:
            raise StrategyIsolationError(
                "strategy does not support isolated evaluation; use advance()"
            )
        evaluator = deepcopy(self._template)
        (runtime_state or self.initial_runtime_state).apply_to(evaluator)
        return self._evaluate_strategy(
            evaluator,
            market,
            portfolio,
            capture_runtime_state=True,
        )

    def advance(self, market: MarketSnapshot, portfolio: object) -> StrategyStep:
        """Advance a legacy-compatible instance without requiring a deepcopy."""
        return self._evaluate_strategy(
            self.strategy,
            market,
            portfolio,
            capture_runtime_state=True,
        )

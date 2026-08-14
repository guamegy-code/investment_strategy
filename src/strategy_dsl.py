"""Declarative, self-contained allocation strategies loaded from YAML.

The DSL deliberately has a small core.  Strategy authors may name arbitrary
calculated variables and persistent state values; only genuinely new
calculations require a registered operator or a Python strategy.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
import re
from typing import Any

import yaml


class StrategyDefinitionError(ValueError):
    """Raised when a declarative strategy is invalid."""


class StrategyExpressionError(StrategyDefinitionError):
    """Raised when a DSL expression is invalid or cannot be evaluated."""


_PERCENT_LITERAL = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)%")
_MISSING = object()


def _percentage_expression(text: str) -> str:
    return _PERCENT_LITERAL.sub(lambda match: f"({match.group(1)}/100)", text)


def _number(value: Any, *, field: str) -> float:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.endswith("%"):
            stripped = stripped[:-1].strip()
            try:
                return float(stripped) / 100.0
            except ValueError as exc:
                raise StrategyDefinitionError(f"{field} must be numeric") from exc
    if isinstance(value, bool):
        raise StrategyDefinitionError(f"{field} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise StrategyDefinitionError(f"{field} must be numeric") from exc
    if not isfinite(result):
        raise StrategyDefinitionError(f"{field} must be finite")
    return result


class _Namespace:
    def __init__(self, values: Mapping[str, Any], label: str):
        self._values = values
        self._label = label

    def resolve(self, name: str) -> Any:
        if name in self._values:
            value = self._values[name]
        else:
            folded = {str(key).casefold(): key for key in self._values}
            key = folded.get(name.casefold())
            if key is None:
                raise StrategyExpressionError(f"unknown {self._label}: {name}")
            value = self._values[key]
        if isinstance(value, Mapping):
            return _Namespace(value, f"{self._label}.{name}")
        return value


@dataclass(frozen=True)
class OperatorDefinition:
    evaluator: Callable[..., Any]
    version: str = "1"


class OperatorRegistry:
    """Versioned extension boundary for calculations not in the core DSL."""

    def __init__(self):
        self._operators: dict[str, OperatorDefinition] = {}

    def register(
        self, name: str, evaluator: Callable[..., Any], *, version: str = "1"
    ) -> None:
        if not name or not name.replace("_", "").isalnum():
            raise ValueError("operator name must be an identifier")
        if name in self._operators:
            raise ValueError(f"operator already registered: {name}")
        self._operators[name] = OperatorDefinition(evaluator, str(version))

    def get(self, name: str) -> OperatorDefinition | None:
        return self._operators.get(name)


DEFAULT_OPERATOR_REGISTRY = OperatorRegistry()


class _ExpressionEvaluator:
    _binary = {
        ast.Add: lambda left, right: left + right,
        ast.Sub: lambda left, right: left - right,
        ast.Mult: lambda left, right: left * right,
        ast.Div: lambda left, right: left / right,
        ast.Mod: lambda left, right: left % right,
        ast.Pow: lambda left, right: left**right,
    }
    _compare = {
        ast.Eq: lambda left, right: left == right,
        ast.NotEq: lambda left, right: left != right,
        ast.Gt: lambda left, right: left > right,
        ast.GtE: lambda left, right: left >= right,
        ast.Lt: lambda left, right: left < right,
        ast.LtE: lambda left, right: left <= right,
        ast.In: lambda left, right: left in right,
        ast.NotIn: lambda left, right: left not in right,
    }

    def __init__(
        self,
        context: Mapping[str, Any],
        *,
        previous_state: Mapping[str, Any],
        changed_state: set[str],
        operators: OperatorRegistry,
    ):
        self.context = context
        self.previous_state = previous_state
        self.changed_state = changed_state
        self.operators = operators

    def evaluate(self, expression: Any) -> Any:
        if not isinstance(expression, str):
            return expression
        text = expression.strip()
        if not text:
            raise StrategyExpressionError("expression must not be empty")
        try:
            node = ast.parse(_percentage_expression(text), mode="eval").body
        except SyntaxError as exc:
            raise StrategyExpressionError(f"invalid expression: {text}") from exc
        try:
            return self._eval(node)
        except StrategyExpressionError:
            raise
        except Exception as exc:
            raise StrategyExpressionError(
                f"failed to evaluate expression: {text}: {exc}"
            ) from exc

    def _state_path(self, node: ast.AST) -> str:
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "state"
        ):
            return node.attr
        raise StrategyExpressionError(
            "state helper requires a state variable, for example changed(state.mode)"
        )

    def _eval_call(self, node: ast.Call) -> Any:
        if not isinstance(node.func, ast.Name):
            raise StrategyExpressionError("only named functions are allowed")
        name = node.func.id
        if name in {"changed", "previous"}:
            if len(node.args) != 1 or node.keywords:
                raise StrategyExpressionError(f"{name}() accepts one state variable")
            state_name = self._state_path(node.args[0])
            if name == "changed":
                return state_name in self.changed_state
            if state_name not in self.previous_state:
                raise StrategyExpressionError(f"unknown state variable: {state_name}")
            return self.previous_state[state_name]

        args = [self._eval(argument) for argument in node.args]
        kwargs = {item.arg: self._eval(item.value) for item in node.keywords}
        core = {
            "abs": abs,
            "min": min,
            "max": max,
            "sum": lambda *values: sum(values),
            "count": lambda *values: sum(bool(value) for value in values),
            "all": lambda *values: all(values),
            "any": lambda *values: any(values),
            "round": round,
            "clamp": lambda value, low, high: max(low, min(high, value)),
        }
        function = core.get(name)
        if function is None:
            definition = self.operators.get(name)
            if definition is None:
                raise StrategyExpressionError(f"unknown function: {name}")
            function = definition.evaluator
        return function(*args, **kwargs)

    def _eval(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in {"true", "True"}:
                return True
            if node.id in {"false", "False"}:
                return False
            if node.id in {"null", "None"}:
                return None
            if node.id not in self.context:
                raise StrategyExpressionError(f"unknown name: {node.id}")
            value = self.context[node.id]
            return _Namespace(value, node.id) if isinstance(value, Mapping) else value
        if isinstance(node, ast.Attribute):
            base = self._eval(node.value)
            if not isinstance(base, _Namespace):
                raise StrategyExpressionError("attribute access is limited to DSL data")
            return base.resolve(node.attr)
        if isinstance(node, (ast.List, ast.Tuple)):
            values = [self._eval(item) for item in node.elts]
            return values if isinstance(node, ast.List) else tuple(values)
        if isinstance(node, ast.UnaryOp):
            value = self._eval(node.operand)
            if isinstance(node.op, ast.Not):
                return not value
            if isinstance(node.op, ast.USub):
                return -value
            if isinstance(node.op, ast.UAdd):
                return +value
            raise StrategyExpressionError("unsupported unary operator")
        if isinstance(node, ast.BinOp):
            operation = self._binary.get(type(node.op))
            if operation is None:
                raise StrategyExpressionError("unsupported arithmetic operator")
            return operation(self._eval(node.left), self._eval(node.right))
        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                return all(self._eval(value) for value in node.values)
            if isinstance(node.op, ast.Or):
                return any(self._eval(value) for value in node.values)
            raise StrategyExpressionError("unsupported boolean operator")
        if isinstance(node, ast.Compare):
            left = self._eval(node.left)
            for operator, comparator in zip(node.ops, node.comparators):
                right = self._eval(comparator)
                comparison = self._compare.get(type(operator))
                if comparison is None:
                    raise StrategyExpressionError("unsupported comparison operator")
                if not comparison(left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            return self._eval(node.body if self._eval(node.test) else node.orelse)
        if isinstance(node, ast.Call):
            return self._eval_call(node)
        raise StrategyExpressionError(
            f"unsupported expression element: {type(node).__name__}"
        )


def _require_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise StrategyDefinitionError(f"{field} must be a mapping")
    return deepcopy(dict(value))


def _validate_definition(raw: Any, source: str) -> dict[str, Any]:
    definition = _require_mapping(raw, source)
    metadata = _require_mapping(definition.get("strategy"), "strategy")
    for field in ("id", "name", "version"):
        if metadata.get(field) in (None, ""):
            raise StrategyDefinitionError(f"strategy.{field} is required")
    if "target" not in definition:
        raise StrategyDefinitionError("target is required")
    assets = _require_mapping(definition.get("assets", {}), "assets")
    required = assets.get("required")
    if not isinstance(required, list) or not required:
        raise StrategyDefinitionError("assets.required must be a non-empty list")
    if len({str(item) for item in required}) != len(required):
        raise StrategyDefinitionError("assets.required must not contain duplicates")
    state = _require_mapping(definition.get("state", {}), "state")
    for name, config in state.items():
        config = _require_mapping(config, f"state.{name}")
        if "initial" not in config:
            raise StrategyDefinitionError(f"state.{name}.initial is required")
        rules = config.get("rules", [])
        if not isinstance(rules, list):
            raise StrategyDefinitionError(f"state.{name}.rules must be a list")
        for index, rule in enumerate(rules):
            rule = _require_mapping(rule, f"state.{name}.rules[{index}]")
            if "set" not in rule:
                raise StrategyDefinitionError(
                    f"state.{name}.rules[{index}].set is required"
                )
            if "when" not in rule and not rule.get("otherwise"):
                raise StrategyDefinitionError(
                    f"state.{name}.rules[{index}] needs when or otherwise"
                )
            confirm = int(rule.get("confirm", 1))
            if confirm < 1:
                raise StrategyDefinitionError("confirm must be at least 1")
    return definition


def load_strategy_definition(path: str | Path) -> dict[str, Any]:
    """Load and validate one UTF-8 YAML strategy definition."""
    source = Path(path)
    try:
        with source.open("r", encoding="utf-8") as stream:
            raw = yaml.safe_load(stream)
    except yaml.YAMLError as exc:
        raise StrategyDefinitionError(f"invalid YAML in {source}: {exc}") from exc
    return _validate_definition(raw, str(source))


class DeclarativeStrategy:
    """A BaseStrategy-compatible evaluator backed by a flat YAML definition."""

    def __init__(
        self,
        definition: Mapping[str, Any],
        *,
        operators: OperatorRegistry | None = None,
    ):
        self.definition = _validate_definition(definition, "strategy definition")
        metadata = self.definition["strategy"]
        self.strategy_id = f"dsl:{metadata['id']}"
        self.display_name = str(metadata["name"])
        self.STRATEGY_VERSION = str(metadata["version"])
        self.dsl_version = str(metadata.get("dsl_version", "1"))
        self.operators = operators or DEFAULT_OPERATOR_REGISTRY

        assets = self.definition["assets"]
        self.required_tickers = tuple(str(item) for item in assets["required"])
        risk = assets.get("risk", ())
        if isinstance(risk, str):
            risk = [risk]
        self.risk_asset_tickers = tuple(str(item) for item in risk)

        self.parameters = deepcopy(self.definition.get("parameters", {}))
        self._state_values = {
            name: deepcopy(config["initial"])
            for name, config in self.definition.get("state", {}).items()
        }
        self._state_candidates: dict[str, dict[str, Any]] = {}
        self._previous_state_values = deepcopy(self._state_values)
        self._changed_state: set[str] = set()
        self._last_rebalance_period: Any = None
        self._evaluated_once = False
        self.variables: dict[str, Any] = {}
        self.target: dict[str, float] | None = None
        self.state = next(iter(self._state_values.values()), None)

    @classmethod
    def from_yaml(
        cls, path: str | Path, *, operators: OperatorRegistry | None = None
    ) -> "DeclarativeStrategy":
        return cls(load_strategy_definition(path), operators=operators)

    def _context(self, market: Mapping[str, Any], portfolio: Any) -> dict[str, Any]:
        prices = {
            ticker: observations.get("Close")
            for ticker, observations in market.items()
            if observations.get("Close") is not None
        }
        weights = portfolio.weights(prices) if prices else {}
        return {
            **market,
            "parameters": self.parameters,
            "variables": self.variables,
            "state": self._state_values,
            "portfolio": {"weight": weights},
        }

    def _evaluator(self, market: Mapping[str, Any], portfolio: Any) -> _ExpressionEvaluator:
        return _ExpressionEvaluator(
            self._context(market, portfolio),
            previous_state=self._previous_state_values,
            changed_state=self._changed_state,
            operators=self.operators,
        )

    def _calculate_variables(self, market: Mapping[str, Any], portfolio: Any) -> None:
        self.variables = {}
        for name, expression in self.definition.get("variables", {}).items():
            self.variables[name] = self._evaluator(market, portfolio).evaluate(expression)

    def _update_state(self, market: Mapping[str, Any], portfolio: Any) -> None:
        self._previous_state_values = deepcopy(self._state_values)
        self._changed_state = set()
        for name, config in self.definition.get("state", {}).items():
            evaluator = self._evaluator(market, portfolio)
            selected = None
            for rule in config.get("rules", []):
                if rule.get("otherwise") or bool(evaluator.evaluate(rule.get("when"))):
                    selected = rule
                    break
            if selected is None:
                self._state_candidates.pop(name, None)
                continue
            assignment = selected["set"]
            desired = (
                evaluator.evaluate(assignment[1:])
                if isinstance(assignment, str) and assignment.startswith("=")
                else deepcopy(assignment)
            )
            current = self._state_values[name]
            if desired == current:
                self._state_candidates.pop(name, None)
                continue
            candidate = self._state_candidates.get(name)
            days = candidate["days"] + 1 if candidate and candidate["value"] == desired else 1
            required_days = int(selected.get("confirm", 1))
            if days >= required_days:
                self._state_values[name] = desired
                self._changed_state.add(name)
                self._state_candidates.pop(name, None)
            else:
                self._state_candidates[name] = {"value": desired, "days": days}
        self.state = next(iter(self._state_values.values()), None)

    def _selected_target(self, market: Mapping[str, Any], portfolio: Any) -> Mapping[str, Any]:
        target = self.definition["target"]
        if isinstance(target, Mapping):
            return target
        if not isinstance(target, list):
            raise StrategyDefinitionError("target must be a mapping or rule list")
        evaluator = self._evaluator(market, portfolio)
        for rule in target:
            rule = _require_mapping(rule, "target rule")
            if rule.get("otherwise") or bool(evaluator.evaluate(rule.get("when"))):
                return _require_mapping(rule.get("weights"), "target rule weights")
        raise StrategyDefinitionError("no target rule matched")

    def _target_weights(self, market: Mapping[str, Any], portfolio: Any) -> dict[str, float]:
        raw = self._selected_target(market, portfolio)
        evaluator = self._evaluator(market, portfolio)
        prices = {ticker: market[ticker]["Close"] for ticker in raw}
        current = portfolio.weights(prices)
        target: dict[str, float] = {}
        remaining_tickers: list[str] = []
        for ticker, expression in raw.items():
            if expression == "keep_current":
                target[str(ticker)] = float(current.get(ticker, 0.0))
            elif expression == "remaining":
                remaining_tickers.append(str(ticker))
            else:
                value = (
                    _number(expression, field=f"target.{ticker}")
                    if not isinstance(expression, str)
                    or expression.strip().endswith("%")
                    and re.fullmatch(r"\s*\d+(?:\.\d+)?%\s*", expression)
                    else _number(
                        evaluator.evaluate(expression), field=f"target.{ticker}"
                    )
                )
                target[str(ticker)] = value
        if len(remaining_tickers) > 1:
            raise StrategyDefinitionError("only one target may use remaining")
        if remaining_tickers:
            target[remaining_tickers[0]] = round(1.0 - sum(target.values()), 10)
        if set(target) != set(self.required_tickers):
            missing = sorted(set(self.required_tickers) - set(target))
            extra = sorted(set(target) - set(self.required_tickers))
            raise StrategyDefinitionError(
                f"target assets must match assets.required; missing={missing}, extra={extra}"
            )
        if any(weight < 0.0 for weight in target.values()):
            raise StrategyDefinitionError("target weights must be non-negative")
        if abs(sum(target.values()) - 1.0) > 1e-8:
            raise StrategyDefinitionError("target weights must sum to 100%")
        return target

    @staticmethod
    def _period(date: Any, schedule: str) -> Any:
        if schedule == "daily":
            return date
        if schedule == "weekly":
            return date.to_period("W")
        if schedule == "monthly":
            return date.to_period("M")
        if schedule == "quarterly":
            return date.to_period("Q")
        raise StrategyDefinitionError(f"unsupported rebalance check: {schedule}")

    def _should_rebalance(
        self, date: Any, market: Mapping[str, Any], portfolio: Any, target: Mapping[str, float]
    ) -> tuple[bool, str | None]:
        config = self.definition.get("rebalance", {})
        if not config or not self._evaluated_once:
            return False, None
        evaluator = self._evaluator(market, portfolio)
        condition = config.get("when")
        if condition is not None and bool(evaluator.evaluate(condition)):
            return True, "DECLARATIVE_CONDITION"

        drift_limit = config.get("drift")
        if drift_limit is None:
            return False, None
        schedule = str(config.get("check", "daily"))
        period = self._period(date, schedule)
        if period == self._last_rebalance_period:
            return False, None
        self._last_rebalance_period = period
        prices = {ticker: market[ticker]["Close"] for ticker in target}
        weights = portfolio.weights(prices)
        limit = _number(drift_limit, field="rebalance.drift")
        outside = any(
            abs(float(weights.get(ticker, 0.0)) - goal) >= limit
            for ticker, goal in target.items()
        )
        return outside, f"{schedule.upper()}_{limit:.1%}_DRIFT" if outside else None

    def _execution_days(self, market: Mapping[str, Any], portfolio: Any) -> int:
        execution = self.definition.get("execution", {})
        if isinstance(execution, int):
            days = execution
        else:
            execution = _require_mapping(execution, "execution")
            days = self._evaluator(market, portfolio).evaluate(execution.get("days", 1))
        days = int(days)
        if days < 1:
            raise StrategyDefinitionError("execution days must be at least 1")
        return days

    def evaluate(self, date: Any, market: Mapping[str, Any], portfolio: Any) -> dict[str, Any]:
        self._calculate_variables(market, portfolio)
        self._update_state(market, portfolio)
        target = self._target_weights(market, portfolio)
        rebalance, reason = self._should_rebalance(date, market, portfolio, target)
        self.target = target
        self._evaluated_once = True
        return {
            "rebalance": rebalance,
            "target": target.copy(),
            "days": self._execution_days(market, portfolio),
            "reason": reason,
        }


def load_strategy_directory(
    directory: str | Path,
    *,
    enabled_only: bool = True,
    operators: OperatorRegistry | None = None,
) -> list[DeclarativeStrategy]:
    """Load independent YAML strategies from a directory in filename order."""
    root = Path(directory)
    if not root.exists():
        return []
    strategies = []
    seen: set[str] = set()
    for path in sorted((*root.glob("*.yaml"), *root.glob("*.yml"))):
        definition = load_strategy_definition(path)
        if enabled_only and not definition["strategy"].get("enabled", True):
            continue
        strategy = DeclarativeStrategy(definition, operators=operators)
        if strategy.strategy_id in seen:
            raise StrategyDefinitionError(f"duplicate strategy id: {strategy.strategy_id}")
        seen.add(strategy.strategy_id)
        strategies.append(strategy)
    return strategies

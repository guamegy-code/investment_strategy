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
_PRODUCT_FX_RATE_BY_SUFFIX = {
    ".KS": "KRW=X",
    ".KQ": "KRW=X",
}
_MARKET_MODES = {"BULL", "CAUTION", "BEAR", "RECOVERY"}
_UNINITIALIZED_MARKET_MODE = "UNINITIALIZED"


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


def _state_literal(value: Any) -> Any:
    """Normalize readable percentage state values while preserving labels."""
    if isinstance(value, str) and re.fullmatch(
        r"\s*-?\d+(?:\.\d+)?%\s*", value
    ):
        return _number(value, field="state value")
    return deepcopy(value)


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
            runtime_function = self.context.get(name)
            if callable(runtime_function):
                function = runtime_function
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
        if isinstance(node, ast.Call):
            return self._eval_call(node)
        raise StrategyExpressionError(
            f"unsupported expression element: {type(node).__name__}"
        )


def _require_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise StrategyDefinitionError(f"{field} must be a mapping")
    return deepcopy(dict(value))


def _reject_unknown(
    value: Mapping[str, Any], allowed: set[str], field: str
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise StrategyDefinitionError(
            f"{field} contains unsupported keys: {', '.join(unknown)}"
        )


def _required_market_fields(
    definition: Mapping[str, Any], tickers: tuple[str, ...]
) -> dict[str, tuple[str, ...]]:
    ticker_names = {ticker.casefold(): ticker for ticker in tickers}
    found = {ticker: {"CLOSE"} for ticker in tickers}

    def inspect(value: Any) -> None:
        if isinstance(value, Mapping):
            for nested in value.values():
                inspect(nested)
            return
        if isinstance(value, list):
            for nested in value:
                inspect(nested)
            return
        if not isinstance(value, str):
            return
        expression = value.strip()
        if expression.startswith("="):
            expression = expression[1:].strip()
        try:
            tree = ast.parse(_percentage_expression(expression), mode="eval")
        except SyntaxError:
            return
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
            ):
                continue
            ticker = ticker_names.get(node.value.id.casefold())
            if ticker is not None:
                found[ticker].add(node.attr.upper())

    for section in ("variables", "state", "target", "rebalance", "execution"):
        inspect(definition.get(section, {}))
    return {
        ticker: tuple(sorted(fields))
        for ticker, fields in found.items()
    }


def _validate_definition(raw: Any, source: str) -> dict[str, Any]:
    definition = _require_mapping(raw, source)
    _reject_unknown(
        definition,
        {
            "strategy", "assets", "parameters", "variables", "state",
            "target", "rebalance", "execution", "source", "products",
        },
        "strategy definition",
    )
    metadata = _require_mapping(definition.get("strategy"), "strategy")
    _reject_unknown(
        metadata, {"id", "name", "version", "dsl_version", "enabled"},
        "strategy",
    )
    for field in ("id", "name", "version"):
        if metadata.get(field) in (None, ""):
            raise StrategyDefinitionError(f"strategy.{field} is required")

    product_definition = "source" in definition or "products" in definition
    if product_definition:
        _reject_unknown(
            definition, {"strategy", "source", "products"},
            "product strategy definition",
        )
        source_strategy = definition.get("source")
        if not isinstance(source_strategy, str) or not source_strategy.strip():
            raise StrategyDefinitionError("source must be a strategy ID")
        products = _require_mapping(definition.get("products"), "products")
        if not products:
            raise StrategyDefinitionError("products must not be empty")
        product_owners: dict[str, str] = {}
        for source_asset, configured_products in products.items():
            if not str(source_asset):
                raise StrategyDefinitionError("products source asset must not be empty")
            configured_products = _require_mapping(
                configured_products, f"products.{source_asset}"
            )
            if not configured_products:
                raise StrategyDefinitionError(
                    f"products.{source_asset} must not be empty"
                )
            total = 0.0
            for product, share in configured_products.items():
                product = str(product)
                if not product:
                    raise StrategyDefinitionError("product ticker must not be empty")
                owner = product_owners.get(product)
#                if owner is not None and owner != str(source_asset):
#                    raise StrategyDefinitionError(
#                        f"product {product} is mapped from both {owner} and {source_asset}"
#                    )
                product_owners[product] = str(source_asset)
                value = _number(share, field=f"products.{source_asset}.{product}")
                if value <= 0.0:
                    raise StrategyDefinitionError("product shares must be positive")
                total += value
            if abs(total - 1.0) > 1e-8:
                raise StrategyDefinitionError(
                    f"products.{source_asset} shares must sum to 100%"
                )
        return definition

    if "target" not in definition:
        raise StrategyDefinitionError("target is required")
    assets = _require_mapping(definition.get("assets", {}), "assets")
    _reject_unknown(assets, {"required", "observations", "risk"}, "assets")
    required = assets.get("required")
    if not isinstance(required, list) or not required:
        raise StrategyDefinitionError("assets.required must be a non-empty list")
    if len({str(item) for item in required}) != len(required):
        raise StrategyDefinitionError("assets.required must not contain duplicates")
    observations = assets.get("observations", [])
    if not isinstance(observations, list):
        raise StrategyDefinitionError("assets.observations must be a list")
    if len({str(item) for item in observations}) != len(observations):
        raise StrategyDefinitionError("assets.observations must not contain duplicates")
    overlap = sorted({str(item) for item in required} & {str(item) for item in observations})
    if overlap:
        raise StrategyDefinitionError(
            "assets.observations must not overlap assets.required: "
            + ", ".join(overlap)
        )
    state = _require_mapping(definition.get("state", {}), "state")
    for name, config in state.items():
        config = _require_mapping(config, f"state.{name}")
        _reject_unknown(config, {"initial", "check", "rules"}, f"state.{name}")
        if "initial" not in config:
            raise StrategyDefinitionError(f"state.{name}.initial is required")
        check = config.get("check", "daily")
        if check not in {"daily", "weekly", "monthly", "quarterly"}:
            raise StrategyDefinitionError(
                f"unsupported state.{name}.check: {check}"
            )
        rules = config.get("rules", [])
        if not isinstance(rules, list):
            raise StrategyDefinitionError(f"state.{name}.rules must be a list")
        for index, rule in enumerate(rules):
            rule = _require_mapping(rule, f"state.{name}.rules[{index}]")
            _reject_unknown(
                rule, {"when", "otherwise", "set", "confirm"},
                f"state.{name}.rules[{index}]",
            )
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
    market_mode = state.get("market_mode")
    if market_mode is not None:
        allowed_modes = _MARKET_MODES | {_UNINITIALIZED_MARKET_MODE}
        initial_mode = str(market_mode["initial"])
        if initial_mode not in allowed_modes:
            raise StrategyDefinitionError(
                "state.market_mode.initial must be BULL, CAUTION, BEAR, "
                "RECOVERY, or UNINITIALIZED"
            )
        for index, rule in enumerate(market_mode.get("rules", [])):
            assigned = rule["set"]
            if (
                isinstance(assigned, str)
                and not assigned.startswith("=")
                and assigned not in allowed_modes
            ):
                raise StrategyDefinitionError(
                    f"state.market_mode.rules[{index}].set has unsupported "
                    f"market mode: {assigned}"
                )

    target = definition["target"]
    if not isinstance(target, list) or not target:
        raise StrategyDefinitionError("target must be a non-empty rule list")
    unconditional = 0
    for index, rule in enumerate(target):
        rule = _require_mapping(rule, f"target[{index}]")
        _reject_unknown(rule, {"when", "weights"}, f"target[{index}]")
        weights = _require_mapping(rule.get("weights"), f"target[{index}].weights")
        if not weights:
            raise StrategyDefinitionError(f"target[{index}].weights must not be empty")
        if "when" not in rule:
            unconditional += 1
            if index != len(target) - 1:
                raise StrategyDefinitionError(
                    "an unconditional target must be the final target rule"
                )
    if unconditional > 1:
        raise StrategyDefinitionError("target may have only one unconditional rule")

    rebalance = definition.get("rebalance", [])
    if not isinstance(rebalance, list):
        raise StrategyDefinitionError("rebalance must be a rule list")
    for index, rule in enumerate(rebalance):
        rule = _require_mapping(rule, f"rebalance[{index}]")
        _reject_unknown(rule, {"when", "check", "days"}, f"rebalance[{index}]")
        if "when" not in rule:
            raise StrategyDefinitionError(f"rebalance[{index}].when is required")
        check = rule.get("check", "daily")
        if check not in {"daily", "weekly", "monthly", "quarterly"}:
            raise StrategyDefinitionError(
                f"unsupported rebalance[{index}].check: {check}"
            )

    execution = definition.get("execution", {})
    if not isinstance(execution, Mapping):
        raise StrategyDefinitionError("execution must be a mapping")
    _reject_unknown(execution, {"days"}, "execution")
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
        if "source" in self.definition:
            raise StrategyDefinitionError(
                "product strategy definitions must be loaded from a strategy directory"
            )
        metadata = self.definition["strategy"]
        self.strategy_id = f"dsl:{metadata['id']}"
        self.display_name = str(metadata["name"])
        self.STRATEGY_VERSION = str(metadata["version"])
        self.dsl_version = str(metadata.get("dsl_version", "1"))
        self.operators = operators or DEFAULT_OPERATOR_REGISTRY

        assets = self.definition["assets"]
        self.holding_tickers = tuple(str(item) for item in assets["required"])
        self.observation_tickers = tuple(
            str(item) for item in assets.get("observations", [])
        )
        self.required_tickers = tuple(
            dict.fromkeys((*self.holding_tickers, *self.observation_tickers))
        )
        self.required_market_fields = _required_market_fields(
            self.definition, self.required_tickers
        )
        risk = assets.get("risk", ())
        if isinstance(risk, str):
            risk = [risk]
        self.risk_asset_tickers = tuple(str(item) for item in risk)

        self.parameters = deepcopy(self.definition.get("parameters", {}))
        self._state_values = {
            name: _state_literal(config["initial"])
            for name, config in self.definition.get("state", {}).items()
        }
        self._state_candidates: dict[str, dict[str, Any]] = {}
        self._last_state_periods: dict[str, Any] = {}
        self._previous_state_values = deepcopy(self._state_values)
        self._changed_state: set[str] = set()
        self._last_rebalance_periods: dict[int, Any] = {}
        self._evaluated_once = False
        self.variables: dict[str, Any] = {}
        self.target: dict[str, float] | None = None
        self.state = self._representative_state(allow_uninitialized=True)

    def __getattr__(self, name: str) -> Any:
        variables = self.__dict__.get("variables", {})
        if name in variables:
            return variables[name]
        state_values = self.__dict__.get("_state_values", {})
        if name in state_values:
            return state_values[name]
        raise AttributeError(name)

    def _representative_state(self, *, allow_uninitialized: bool = False) -> Any:
        if "market_mode" not in self._state_values:
            return next(iter(self._state_values.values()), None)
        value = self._state_values["market_mode"]
        allowed = _MARKET_MODES | (
            {_UNINITIALIZED_MARKET_MODE} if allow_uninitialized else set()
        )
        if value not in allowed:
            expected = ", ".join(sorted(allowed))
            raise StrategyDefinitionError(
                f"state.market_mode must be one of: {expected}"
            )
        return value

    @classmethod
    def from_yaml(
        cls, path: str | Path, *, operators: OperatorRegistry | None = None
    ) -> "DeclarativeStrategy":
        return cls(load_strategy_definition(path), operators=operators)

    def _context(
        self,
        market: Mapping[str, Any],
        portfolio: Any,
        target: Mapping[str, float] | None = None,
    ) -> dict[str, Any]:
        prices = {
            ticker: market[ticker].get("Close")
            for ticker in self.holding_tickers
            if ticker in market and market[ticker].get("Close") is not None
        }
        weights = portfolio.weights(prices) if prices else {}
        context = {
            **market,
            "parameters": self.parameters,
            "variables": self.variables,
            "state": self._state_values,
            "portfolio": {"weight": weights},
        }
        if target is not None:
            deviation = max(
                (
                    abs(float(weights.get(ticker, 0.0)) - goal)
                    for ticker, goal in target.items()
                ),
                default=0.0,
            )
            context["target_deviation"] = lambda: deviation
        return context

    def _evaluator(
        self,
        market: Mapping[str, Any],
        portfolio: Any,
        target: Mapping[str, float] | None = None,
    ) -> _ExpressionEvaluator:
        return _ExpressionEvaluator(
            self._context(market, portfolio, target),
            previous_state=self._previous_state_values,
            changed_state=self._changed_state,
            operators=self.operators,
        )

    def _calculate_variables(self, market: Mapping[str, Any], portfolio: Any) -> None:
        self.variables = {}
        for name, expression in self.definition.get("variables", {}).items():
            self.variables[name] = self._evaluator(market, portfolio).evaluate(expression)

    def _update_state(
        self, date: Any, market: Mapping[str, Any], portfolio: Any
    ) -> None:
        self._previous_state_values = deepcopy(self._state_values)
        self._changed_state = set()
        for name, config in self.definition.get("state", {}).items():
            period = self._period(date, str(config.get("check", "daily")))
            if self._last_state_periods.get(name) == period:
                continue
            self._last_state_periods[name] = period
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
            desired = _state_literal(
                evaluator.evaluate(assignment[1:])
                if isinstance(assignment, str) and assignment.startswith("=")
                else assignment
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
        self.state = self._representative_state()

    def _selected_target(self, market: Mapping[str, Any], portfolio: Any) -> Mapping[str, Any]:
        target = self.definition["target"]
        evaluator = self._evaluator(market, portfolio)
        for rule in target:
            rule = _require_mapping(rule, "target rule")
            if "when" not in rule or bool(evaluator.evaluate(rule["when"])):
                return _require_mapping(rule.get("weights"), "target rule weights")
        raise StrategyDefinitionError("no target rule matched")

    def _target_weights(self, market: Mapping[str, Any], portfolio: Any) -> dict[str, float]:
        raw = self._selected_target(market, portfolio)
        evaluator = self._evaluator(market, portfolio)
        target: dict[str, float] = {}
        for ticker, expression in raw.items():
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
        if set(target) != set(self.holding_tickers):
            missing = sorted(set(self.holding_tickers) - set(target))
            extra = sorted(set(target) - set(self.holding_tickers))
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
        raise StrategyDefinitionError(f"unsupported check period: {schedule}")

    def _should_rebalance(
        self, date: Any, market: Mapping[str, Any], portfolio: Any, target: Mapping[str, float]
    ) -> tuple[bool, str | None, int | None]:
        rules = self.definition.get("rebalance", [])
        if not rules:
            return False, None, None
        evaluator = self._evaluator(market, portfolio, target)
        eligible: list[tuple[int, Mapping[str, Any]]] = []
        for index, rule in enumerate(rules):
            schedule = str(rule.get("check", "daily"))
            period = self._period(date, schedule)
            previous_period = self._last_rebalance_periods.get(index)
            self._last_rebalance_periods[index] = period
            if not self._evaluated_once or period == previous_period:
                continue
            eligible.append((index, rule))
        for index, rule in eligible:
            if not bool(evaluator.evaluate(rule["when"])):
                continue
            days = None
            if "days" in rule:
                days = int(evaluator.evaluate(rule["days"]))
                if days < 1:
                    raise StrategyDefinitionError(
                        f"rebalance[{index}].days must be at least 1"
                    )
            return True, self._rebalance_reason(index), days
        return False, None, None

    def _rebalance_reason(self, rule_index: int) -> str:
        primary_state = (
            "market_mode"
            if "market_mode" in self._state_values
            else next(iter(self._state_values), None)
        )
        if primary_state in self._changed_state:
            previous = self._previous_state_values[primary_state]
            current = self._state_values[primary_state]
            details = []
            for name in ("risk_off_score", "recovery_score"):
                if name in self.variables:
                    label = name.removesuffix("_score")
                    details.append(f"{label}={self.variables[name]}")
            suffix = f"({','.join(details)})" if details else ""
            return f"{previous}->{current}{suffix}"
        return f"DECLARATIVE_RULE_{rule_index + 1}"

    def _execution_days(self, market: Mapping[str, Any], portfolio: Any) -> int:
        execution = self.definition.get("execution", {})
        execution = _require_mapping(execution, "execution")
        days = self._evaluator(market, portfolio).evaluate(execution.get("days", 1))
        days = int(days)
        if days < 1:
            raise StrategyDefinitionError("execution days must be at least 1")
        return days

    def evaluate(self, date: Any, market: Mapping[str, Any], portfolio: Any) -> dict[str, Any]:
        self._calculate_variables(market, portfolio)
        self._update_state(date, market, portfolio)
        target = self._target_weights(market, portfolio)
        rebalance, reason, rule_days = self._should_rebalance(
            date, market, portfolio, target
        )
        self.target = target
        self._evaluated_once = True
        return {
            "rebalance": rebalance,
            "target": target.copy(),
            "days": rule_days or self._execution_days(market, portfolio),
            "reason": reason,
        }


class _MappedPortfolioView:
    """Present actual product holdings as source-asset weights."""

    def __init__(
        self,
        portfolio: Any,
        market: Mapping[str, Mapping[str, Any]],
        products: Mapping[str, Mapping[str, float]],
    ):
        self._portfolio = portfolio
        self._products = products
        prices = {
            ticker: observations.get("Close")
            for ticker, observations in market.items()
            if observations.get("Close") is not None
        }
        self._actual_weights = portfolio.weights(prices)

    def weights(self, prices: Mapping[str, Any]) -> dict[str, float]:
        result = {}
        for source_asset in prices:
            mapped = self._products.get(source_asset)
            if mapped is None:
                result[source_asset] = float(
                    self._actual_weights.get(source_asset, 0.0)
                )
            else:
                result[source_asset] = sum(
                    float(self._actual_weights.get(product, 0.0))
                    for product in mapped
                )
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._portfolio, name)


class ProductMappedStrategy:
    """Delegate decisions to a source strategy and map targets to products."""

    def __init__(self, definition: Mapping[str, Any], source_strategy: Any):
        self.definition = _validate_definition(definition, "product strategy definition")
        if "source" not in self.definition:
            raise StrategyDefinitionError("source is required for a product strategy")
        if not callable(getattr(source_strategy, "evaluate", None)):
            raise StrategyDefinitionError("source strategy must define evaluate()")

        metadata = self.definition["strategy"]
        self.strategy_id = f"dsl:{metadata['id']}"
        self.display_name = str(metadata["name"])
        self.STRATEGY_VERSION = str(metadata["version"])
        self.dsl_version = str(metadata.get("dsl_version", "1"))
        self.source_strategy_id = str(self.definition["source"])
        self.source_strategy = source_strategy
        self.products = {
            str(source_asset): {
                str(product): _number(
                    share, field=f"products.{source_asset}.{product}"
                )
                for product, share in configured_products.items()
            }
            for source_asset, configured_products in self.definition["products"].items()
        }

        source_tickers = tuple(
            str(ticker)
            for ticker in getattr(source_strategy, "required_tickers", ())
        )
        if not source_tickers:
            raise StrategyDefinitionError("source strategy must declare required_tickers")
        source_holding_tickers = tuple(
            str(ticker)
            for ticker in getattr(source_strategy, "holding_tickers", source_tickers)
        )
        unknown_sources = sorted(set(self.products) - set(source_holding_tickers))
        if unknown_sources:
            raise StrategyDefinitionError(
                "products contains assets not used by the source strategy: "
                + ", ".join(unknown_sources)
            )
        product_tickers = tuple(
            product
            for configured_products in self.products.values()
            for product in configured_products
        )
        self.observation_tickers = tuple(
            str(ticker)
            for ticker in getattr(source_strategy, "observation_tickers", ())
        )
        self.holding_tickers = tuple(dict.fromkeys(
            product
            for source_asset in source_holding_tickers
            for product in self.products.get(source_asset, {source_asset: 1.0})
        ))
        self.required_tickers = tuple(dict.fromkeys((*source_tickers, *product_tickers)))
        fx_rates = {
            rate
            for ticker in product_tickers
            for suffix, rate in _PRODUCT_FX_RATE_BY_SUFFIX.items()
            if ticker.upper().endswith(suffix)
        }
        if len(fx_rates) > 1:
            raise StrategyDefinitionError(
                "mapped products require more than one exchange rate"
            )
        if fx_rates:
            self.FX_RATE_TICKER = next(iter(fx_rates))

        source_risk = tuple(
            str(ticker)
            for ticker in getattr(source_strategy, "risk_asset_tickers", ())
        )
        self.risk_asset_tickers = tuple(dict.fromkeys(
            product
            for source_asset in source_risk
            for product in self.products.get(source_asset, {source_asset: 1.0})
        ))
        self.target: dict[str, float] | None = None

    def __getattr__(self, name: str) -> Any:
        source = self.__dict__.get("source_strategy")
        if source is None:
            raise AttributeError(name)
        return getattr(source, name)

    def _map_target(self, source_target: Mapping[str, Any]) -> dict[str, float]:
        mapped_target: dict[str, float] = {}
        for source_asset, raw_weight in source_target.items():
            weight = _number(raw_weight, field=f"source target.{source_asset}")
            configured_products = self.products.get(
                str(source_asset), {str(source_asset): 1.0}
            )
            allocated = 0.0
            items = list(configured_products.items())
            for index, (product, share) in enumerate(items):
                product_weight = (
                    round(weight - allocated, 10)
                    if index == len(items) - 1
                    else round(weight * share, 10)
                )
                mapped_target[product] = round(
                    mapped_target.get(product, 0.0) + product_weight, 10
                )
                allocated += product_weight
        if abs(sum(mapped_target.values()) - 1.0) > 1e-8:
            raise StrategyDefinitionError("mapped target weights must sum to 100%")
        return mapped_target

    def evaluate(
        self, date: Any, market: Mapping[str, Any], portfolio: Any
    ) -> dict[str, Any]:
        source_portfolio = _MappedPortfolioView(portfolio, market, self.products)
        source_signal = self.source_strategy.evaluate(date, market, source_portfolio)
        if not isinstance(source_signal, Mapping):
            raise StrategyDefinitionError("source strategy evaluation must be a mapping")
        mapped_target = self._map_target(
            _require_mapping(source_signal.get("target"), "source target")
        )
        self.target = mapped_target
        return {
            **dict(source_signal),
            "target": mapped_target.copy(),
        }


def load_strategy_directory(
    directory: str | Path,
    *,
    enabled_only: bool = True,
    operators: OperatorRegistry | None = None,
    strategy_resolver: Callable[[str], Any] | None = None,
) -> list[Any]:
    """Load calculation and product-mapped strategies from a directory."""
    root = Path(directory)
    if not root.exists():
        return []
    definitions = [
        load_strategy_definition(path)
        for path in sorted((*root.glob("*.yaml"), *root.glob("*.yml")))
    ]
    seen: set[str] = set()
    for definition in definitions:
        strategy_id = f"dsl:{definition['strategy']['id']}"
        if strategy_id in seen:
            raise StrategyDefinitionError(f"duplicate strategy id: {strategy_id}")
        seen.add(strategy_id)

    local_sources: dict[str, Any] = {}
    for definition in definitions:
        if "source" in definition:
            continue
        strategy = DeclarativeStrategy(definition, operators=operators)
        local_sources[strategy.strategy_id] = strategy
        local_sources[strategy.strategy_id.removeprefix("dsl:")] = strategy

    loaded: list[Any] = []
    for definition in definitions:
        enabled = definition["strategy"].get("enabled", True)
        if enabled_only and not enabled:
            continue
        if "source" not in definition:
            strategy_id = f"dsl:{definition['strategy']['id']}"
            loaded.append(local_sources[strategy_id])
            continue

        source_id = str(definition["source"])
        source_strategy = local_sources.get(source_id)
        if source_strategy is not None:
            source_strategy = deepcopy(source_strategy)
        elif strategy_resolver is not None:
            try:
                source_strategy = strategy_resolver(source_id)
            except (KeyError, ValueError) as exc:
                raise StrategyDefinitionError(
                    f"unknown source strategy: {source_id}"
                ) from exc
        else:
            raise StrategyDefinitionError(f"unknown source strategy: {source_id}")
        loaded.append(ProductMappedStrategy(definition, source_strategy))
    return loaded

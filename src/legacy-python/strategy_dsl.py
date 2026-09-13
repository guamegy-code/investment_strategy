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
import json
from math import isfinite
from pathlib import Path
import re
from typing import Any

import yaml


class _StrategyYamlLoader(yaml.SafeLoader):
    """Safe YAML loader with YAML 1.2-style boolean words.

    PyYAML's default YAML 1.1 resolver treats the DSL key ``on`` as boolean
    true.  The DSL documents use ``on: changed`` naturally, so only literal
    true/false values should resolve as booleans.
    """


_StrategyYamlLoader.yaml_implicit_resolvers = deepcopy(
    yaml.SafeLoader.yaml_implicit_resolvers
)
for _resolver_key, _resolver_entries in list(
    _StrategyYamlLoader.yaml_implicit_resolvers.items()
):
    _StrategyYamlLoader.yaml_implicit_resolvers[_resolver_key] = [
        entry for entry in _resolver_entries
        if entry[0] != "tag:yaml.org,2002:bool"
    ]
_StrategyYamlLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


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
_SIMPLE_ROTATION_ASSET_CLASSES = {
    "GLD": "GOLD",
    "069500.KS": "EQUITY",
    "VEA": "EQUITY",
    "VWO": "EQUITY",
}
_SIMPLE_ROTATION_DEFAULTS = {
    "top_n": 2,
    "max_single_sleeve_share": 0.50,
    "max_gold_sleeve_share": 0.30,
    "max_equity_sleeve_share": 0.30,
    "switch_score_margin": 3.0,
    "minimum_hold_periods": 3,
    "minimum_weight_change": 0.10,
}
_ROTATION_RISK_CAP = 0.70


def _percentage_expression(text: str) -> str:
    return _PERCENT_LITERAL.sub(lambda match: f"({match.group(1)}/100)", text)


def _is_krw_ticker(ticker: str) -> bool:
    return str(ticker).upper().endswith((".KS", ".KQ"))


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


def _normalize_simple_rotation(definition: dict[str, Any]) -> None:
    """Expand the author-facing rotation shorthand into the engine schema."""

    rotation = definition.get("rotation")
    if not isinstance(rotation, Mapping) or "replace" not in rotation:
        return
    allowed = {"replace", "review", "assets", "suspend_when"}
    unknown = sorted(set(rotation) - allowed)
    if unknown:
        raise StrategyDefinitionError(
            "simple rotation contains unsupported keys: " + ", ".join(unknown)
        )
    assets = rotation.get("assets")
    if not isinstance(assets, list) or not assets or not all(
        isinstance(ticker, str) and ticker for ticker in assets
    ):
        raise StrategyDefinitionError("rotation.assets must be a non-empty ticker list")
    definition["rotation"] = {
        "sleeve": str(rotation["replace"]),
        "check": str(rotation.get("review", "monthly")),
        **(
            {"suspend_when": deepcopy(rotation["suspend_when"])}
            if "suspend_when" in rotation
            else {}
        ),
        "candidates": [
            {
                "ticker": ticker,
                "group": ticker,
                "asset_class": _SIMPLE_ROTATION_ASSET_CLASSES.get(ticker, "BOND"),
            }
            for ticker in assets
        ],
        **_SIMPLE_ROTATION_DEFAULTS,
    }


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
            f"{field} contains unsupported keys: {', '.join(map(str, unknown))}"
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

    for section in ("variables", "state", "target", "rebalance", "execution", "notifications"):
        inspect(definition.get(section, {}))
    rotation = definition.get("rotation")
    if isinstance(rotation, Mapping):
        sleeve = str(rotation.get("sleeve", ""))
        if sleeve in found:
            found[sleeve].update({"ROC60", "ROC120", "ROC252"})
        for candidate in rotation.get("candidates", []):
            if not isinstance(candidate, Mapping):
                continue
            ticker = str(candidate.get("ticker", ""))
            if ticker in found:
                found[ticker].update({
                    "CLOSE", "EMA200", "ROC60", "ROC120", "ROC252", "VOL60",
                })
    return {
        ticker: tuple(sorted(fields))
        for ticker, fields in found.items()
    }


def _validate_definition(raw: Any, source: str) -> dict[str, Any]:
    definition = _require_mapping(raw, source)
    _normalize_simple_rotation(definition)
    _reject_unknown(
        definition,
        {
            "strategy", "assets", "parameters", "variables", "state",
            "target", "rebalance", "execution", "notifications", "rotation", "source", "products",
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
    risk = assets.get("risk", [])
    if isinstance(risk, str):
        risk = [risk]
    if not isinstance(risk, list):
        raise StrategyDefinitionError("assets.risk must be a list")
    unknown_risk = sorted({str(item) for item in risk} - {str(item) for item in required})
    if unknown_risk:
        raise StrategyDefinitionError(
            "assets.risk must be configured assets: " + ", ".join(unknown_risk)
        )
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
    rotation = definition.get("rotation")
    if rotation is not None:
        rotation = _require_mapping(rotation, "rotation")
        _reject_unknown(
            rotation,
            {
                "sleeve", "check", "candidates", "top_n",
                "max_single_sleeve_share", "max_gold_sleeve_share",
                "max_equity_sleeve_share", "switch_score_margin",
                "minimum_hold_periods", "minimum_weight_change", "suspend_when",
            },
            "rotation",
        )
        sleeve = str(rotation.get("sleeve", ""))
        if sleeve not in {str(item) for item in required}:
            raise StrategyDefinitionError(
                "rotation.sleeve must be one of assets.required"
            )
        check = rotation.get("check", "monthly")
        if check not in {"daily", "weekly", "monthly", "quarterly"}:
            raise StrategyDefinitionError("rotation.check has an unsupported period")
        candidates = rotation.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise StrategyDefinitionError("rotation.candidates must be a non-empty list")
        candidate_tickers = []
        for index, candidate in enumerate(candidates):
            candidate = _require_mapping(candidate, f"rotation.candidates[{index}]")
            _reject_unknown(
                candidate, {"ticker", "group", "asset_class"},
                f"rotation.candidates[{index}]",
            )
            ticker = str(candidate.get("ticker", ""))
            if ticker not in {str(item) for item in required}:
                raise StrategyDefinitionError(
                    f"rotation.candidates[{index}].ticker must be in assets.required"
                )
            if ticker == sleeve:
                raise StrategyDefinitionError("rotation.sleeve cannot be a candidate")
            if not str(candidate.get("group", "")):
                raise StrategyDefinitionError(
                    f"rotation.candidates[{index}].group is required"
                )
            if str(candidate.get("asset_class", "")) not in {"BOND", "EQUITY", "GOLD"}:
                raise StrategyDefinitionError(
                    f"rotation.candidates[{index}].asset_class must be BOND, EQUITY, or GOLD"
                )
            candidate_tickers.append(ticker)
        if len(set(candidate_tickers)) != len(candidate_tickers):
            raise StrategyDefinitionError("rotation.candidates must not repeat a ticker")
        top_n = int(rotation.get("top_n", 2))
        if top_n < 1 or top_n > len(candidate_tickers):
            raise StrategyDefinitionError("rotation.top_n must be within candidate count")
        for field, default in (
            ("max_single_sleeve_share", 0.50),
            ("max_gold_sleeve_share", 0.30),
            ("max_equity_sleeve_share", 0.30),
        ):
            value = _number(rotation.get(field, default), field=f"rotation.{field}")
            if value <= 0.0 or value > 1.0:
                raise StrategyDefinitionError(f"rotation.{field} must be in (0, 1]")
        switch_margin = _number(
            rotation.get("switch_score_margin", 0.0),
            field="rotation.switch_score_margin",
        )
        if switch_margin < 0.0:
            raise StrategyDefinitionError("rotation.switch_score_margin must be non-negative")
        minimum_hold = int(rotation.get("minimum_hold_periods", 0))
        if minimum_hold < 0:
            raise StrategyDefinitionError("rotation.minimum_hold_periods must be non-negative")
        minimum_weight_change = _number(
            rotation.get("minimum_weight_change", 0.0),
            field="rotation.minimum_weight_change",
        )
        if minimum_weight_change < 0.0 or minimum_weight_change > 1.0:
            raise StrategyDefinitionError(
                "rotation.minimum_weight_change must be in [0, 1]"
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
    notifications = definition.get("notifications", {})
    if not isinstance(notifications, Mapping):
        raise StrategyDefinitionError("notifications must be a mapping")
    _reject_unknown(
        notifications,
        {"weekly", "states", "variables", "market", "prealerts"},
        "notifications",
    )
    if "weekly" in notifications and not isinstance(notifications["weekly"], bool):
        raise StrategyDefinitionError("notifications.weekly must be true or false")
    for section in ("states", "variables"):
        configured = _require_mapping(notifications.get(section, {}), f"notifications.{section}")
        for name, item in configured.items():
            item = _require_mapping(item, f"notifications.{section}.{name}")
            allowed = {"label", "alerts"} if section == "states" else {"label", "max", "decimals"}
            _reject_unknown(item, allowed, f"notifications.{section}.{name}")
            if not str(item.get("label", "")).strip():
                raise StrategyDefinitionError(f"notifications.{section}.{name}.label is required")
            known = state if section == "states" else definition.get("variables", {})
            if name not in known:
                raise StrategyDefinitionError(f"notifications.{section}.{name} is not defined")
            if section == "states" and "alerts" in item:
                alerts = item["alerts"]
                if not isinstance(alerts, list):
                    raise StrategyDefinitionError(f"notifications.states.{name}.alerts must be a list")
                for index, alert in enumerate(alerts):
                    alert = _require_mapping(alert, f"notifications.states.{name}.alerts[{index}]")
                    _reject_unknown(alert, {"on", "from", "to", "message"}, f"notifications.states.{name}.alerts[{index}]")
                    if "to" not in alert:
                        raise StrategyDefinitionError(f"notifications.states.{name}.alerts[{index}].to is required")
                    event = str(alert.get("on", "changed"))
                    if event not in {"changed", "confirmation_started"}:
                        raise StrategyDefinitionError(
                            f"notifications.states.{name}.alerts[{index}].on must be changed or confirmation_started"
                        )
                    targets = alert["to"] if isinstance(alert["to"], list) else [alert["to"]]
                    if not targets:
                        raise StrategyDefinitionError(
                            f"notifications.states.{name}.alerts[{index}].to must not be empty"
                        )
                    if event == "confirmation_started" and "from" in alert:
                        raise StrategyDefinitionError(
                            f"notifications.states.{name}.alerts[{index}].from is not valid for confirmation_started"
                        )
                    if "message" in alert and not str(alert["message"]).strip():
                        raise StrategyDefinitionError(f"notifications.states.{name}.alerts[{index}].message must not be empty")
    market_notifications = notifications.get("market", [])
    if not isinstance(market_notifications, list):
        raise StrategyDefinitionError("notifications.market must be a list")
    for index, item in enumerate(market_notifications):
        item = _require_mapping(item, f"notifications.market[{index}]")
        _reject_unknown(item, {"ticker", "field", "label", "format", "decimals"}, f"notifications.market[{index}]")
        for field in ("ticker", "field", "label"):
            if not str(item.get(field, "")).strip():
                raise StrategyDefinitionError(f"notifications.market[{index}].{field} is required")
        known_tickers = {str(value) for value in required + observations}
        if str(item["ticker"]) not in known_tickers:
            raise StrategyDefinitionError(f"notifications.market[{index}].ticker is not configured")
    prealerts = notifications.get("prealerts", [])
    if not isinstance(prealerts, list):
        raise StrategyDefinitionError("notifications.prealerts must be a list")
    ids = set()
    for index, item in enumerate(prealerts):
        item = _require_mapping(item, f"notifications.prealerts[{index}]")
        _reject_unknown(item, {"id", "when", "reset_when", "message"}, f"notifications.prealerts[{index}]")
        for field in ("id", "when", "reset_when", "message"):
            if not str(item.get(field, "")).strip():
                raise StrategyDefinitionError(f"notifications.prealerts[{index}].{field} is required")
        if item["id"] in ids:
            raise StrategyDefinitionError("notifications.prealerts ids must be unique")
        ids.add(item["id"])
    return definition


def load_strategy_yaml(path: str | Path) -> dict[str, Any]:
    """Load one UTF-8 strategy YAML file using the DSL's YAML rules."""
    source = Path(path)
    try:
        with source.open("r", encoding="utf-8") as stream:
            raw = yaml.load(stream, Loader=_StrategyYamlLoader)
    except yaml.YAMLError as exc:
        raise StrategyDefinitionError(f"invalid YAML in {source}: {exc}") from exc
    return raw


def load_strategy_definition(path: str | Path) -> dict[str, Any]:
    """Load and validate one UTF-8 YAML strategy definition."""
    source = Path(path)
    raw = load_strategy_yaml(source)
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
        # Cross-asset rotation uses local prices for signals and KRW values for
        # portfolio weights.  This is an engine default, not YAML input.
        rotation_config = self.definition.get("rotation")
        self.foreign_asset_tickers = tuple(
            ticker for ticker in self.holding_tickers
            if ticker != "KRW=X" and not _is_krw_ticker(ticker)
        ) if rotation_config else ()
        self.valuation_currency = "KRW" if self.foreign_asset_tickers else None
        self.valuation_fx_ticker = (
            "KRW=X" if self.foreign_asset_tickers else None
        )
        self.valuation_signal_currency = (
            "LOCAL" if self.foreign_asset_tickers else "KRW"
        )
        self.required_tickers = tuple(
            dict.fromkeys((
                *self.holding_tickers, *self.observation_tickers,
                *((self.valuation_fx_ticker,) if self.valuation_fx_ticker else ()),
            ))
        )
        self.required_market_fields = _required_market_fields(
            self.definition, self.required_tickers
        )
        risk = assets.get("risk", ())
        if isinstance(risk, str):
            risk = [risk]
        self.risk_asset_tickers = tuple(str(item) for item in risk)

        self.parameters = deepcopy(self.definition.get("parameters", {}))
        self.rotation = deepcopy(rotation_config)
        self.rotation_candidate_tickers = tuple(
            str(candidate["ticker"])
            for candidate in (self.rotation or {}).get("candidates", [])
        )
        self._state_values = {
            name: _state_literal(config["initial"])
            for name, config in self.definition.get("state", {}).items()
        }
        self._state_candidates: dict[str, dict[str, Any]] = {}
        self._previous_state_candidates: dict[str, dict[str, Any]] = {}
        self._last_state_periods: dict[str, Any] = {}
        self._previous_state_values = deepcopy(self._state_values)
        self._changed_state: set[str] = set()
        self._last_rebalance_periods: dict[int, Any] = {}
        self._evaluated_once = False
        self.variables: dict[str, Any] = {}
        self.target: dict[str, float] | None = None
        self.rotation_decision: dict[str, Any] | None = None
        self.notification_context: dict[str, Any] | None = None
        self._rotation_last_period: Any = None
        self._rotation_active = False
        self._rotation_mix: dict[str, float] = {}
        self._rotation_selected: tuple[str, ...] = ()
        self._rotation_hold_periods = 0
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
            def weight_deviation(ticker: Any) -> float:
                ticker = str(ticker)
                if ticker not in target:
                    raise StrategyExpressionError(
                        f"weight_deviation() target asset is unknown: {ticker}"
                    )
                return float(weights.get(ticker, 0.0)) - float(target[ticker])

            deviation = max(
                (
                    abs(float(weights.get(ticker, 0.0)) - goal)
                    for ticker, goal in target.items()
                ),
                default=0.0,
            )
            context["target_deviation"] = lambda: deviation
            context["weight_deviation"] = weight_deviation
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
        self._previous_state_candidates = deepcopy(self._state_candidates)
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
                self._state_candidates[name] = {
                    "value": desired,
                    "days": days,
                    "required_days": required_days,
                }
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
        required = set(self.holding_tickers)
        provided = set(target)
        implicit_rotation_assets = set(self.rotation_candidate_tickers)
        missing = sorted(required - provided - implicit_rotation_assets)
        extra = sorted(provided - required)
        if missing or extra:
            raise StrategyDefinitionError(
                f"target assets must match assets.required; missing={missing}, extra={extra}"
            )
        # Rotation candidates do not have a base allocation: the rotation rule
        # determines them after selecting the declarative target. Keep the
        # execution target complete so a previously selected asset can still
        # receive an explicit 0% liquidation target.
        for ticker in self.rotation_candidate_tickers:
            target.setdefault(ticker, 0.0)
        if any(weight < 0.0 for weight in target.values()):
            raise StrategyDefinitionError("target weights must be non-negative")
        if abs(sum(target.values()) - 1.0) > 1e-8:
            raise StrategyDefinitionError("target weights must sum to 100%")
        return target

    @staticmethod
    def _rotation_number(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if isfinite(number) else None

    def _rotation_selection(
        self, market: Mapping[str, Any]
    ) -> tuple[dict[str, float], tuple[str, ...], dict[str, Any]]:
        """Choose up to two eligible assets without forcing a risk position."""

        assert self.rotation is not None
        sleeve = str(self.rotation["sleeve"])
        cash = market[sleeve]
        cash_values = {
            field: self._rotation_number(cash.get(field))
            for field in ("ROC60", "ROC120", "ROC252")
        }
        candidate_details: dict[str, Any] = {}
        if any(value is None for value in cash_values.values()):
            return {sleeve: 1.0}, (), candidate_details

        group_winners: dict[str, tuple[float, Mapping[str, Any]]] = {}
        for candidate in self.rotation["candidates"]:
            ticker = str(candidate["ticker"])
            observation = market[ticker]
            values = {
                field: self._rotation_number(observation.get(field))
                for field in ("Close", "EMA200", "ROC60", "ROC120", "ROC252", "VOL60")
            }
            reasons = []
            if any(value is None for value in values.values()):
                reasons.append("필수 추세 지표 부족")
            elif values["Close"] <= values["EMA200"]:
                reasons.append("가격이 EMA200 아래")
            elif values["ROC60"] <= cash_values["ROC60"]:
                reasons.append("3개월 수익률이 현금 슬리브 이하")
            elif values["VOL60"] <= 0.0:
                reasons.append("변동성 계산 불가")
            if reasons:
                candidate_details[ticker] = {
                    "eligible": False,
                    "reasons": reasons,
                }
                continue
            score = (
                0.50 * (values["ROC60"] - cash_values["ROC60"])
                + 0.30 * (values["ROC120"] - cash_values["ROC120"])
                + 0.20 * (values["ROC252"] - cash_values["ROC252"])
            )
            detail = {
                "eligible": True,
                "score": score,
                "volatility": values["VOL60"],
                "roc60_excess": values["ROC60"] - cash_values["ROC60"],
                "roc120_excess": values["ROC120"] - cash_values["ROC120"],
                "roc252_excess": values["ROC252"] - cash_values["ROC252"],
                "reasons": ["EMA200 상단", "3개월 수익률 현금 초과"],
            }
            candidate_details[ticker] = detail
            group = str(candidate["group"])
            previous = group_winners.get(group)
            if previous is None or score > previous[0]:
                group_winners[group] = (score, candidate)

        top_n = int(self.rotation.get("top_n", 2))
        switch_margin = _number(
            self.rotation.get("switch_score_margin", 0.0),
            field="rotation.switch_score_margin",
        )
        minimum_hold = int(self.rotation.get("minimum_hold_periods", 0))
        incumbent = set(self._rotation_selected)
        ranked = sorted(
            group_winners.values(),
            key=lambda item: (
                item[0]
                + (switch_margin if str(item[1]["ticker"]) in incumbent else 0.0)
            ),
            reverse=True,
        )
        eligible_config = {
            str(candidate["ticker"]): candidate for _, candidate in ranked
        }
        locked = []
        if self._rotation_hold_periods < minimum_hold:
            locked = [
                eligible_config[ticker]
                for ticker in self._rotation_selected
                if ticker in eligible_config
            ][:top_n]
        selected_config = list(locked)
        for _, candidate in ranked:
            if len(selected_config) >= top_n:
                break
            if str(candidate["ticker"]) not in {
                str(item["ticker"]) for item in selected_config
            }:
                selected_config.append(candidate)
        selected_tickers = {str(candidate["ticker"]) for candidate in selected_config}
        group_winner_tickers = {
            str(candidate["ticker"])
            for _, candidate in group_winners.values()
        }
        for ticker, detail in candidate_details.items():
            if not detail["eligible"]:
                continue
            detail["selection_status"] = (
                "선택"
                if ticker in selected_tickers
                else "같은 그룹 내 점수 열위"
                if ticker not in group_winner_tickers
                else "상위 선택 수 밖"
            )
        if not selected_config:
            return {sleeve: 1.0}, (), candidate_details

        selected = tuple(str(candidate["ticker"]) for candidate in selected_config)
        inverse_volatility = {
            ticker: 1.0 / float(candidate_details[ticker]["volatility"])
            for ticker in selected
        }
        total_inverse_volatility = sum(inverse_volatility.values())
        max_single = _number(
            self.rotation.get("max_single_sleeve_share", 0.50),
            field="rotation.max_single_sleeve_share",
        )
        max_gold = _number(
            self.rotation.get("max_gold_sleeve_share", 0.30),
            field="rotation.max_gold_sleeve_share",
        )
        max_equity = _number(
            self.rotation.get("max_equity_sleeve_share", 0.30),
            field="rotation.max_equity_sleeve_share",
        )
        mix: dict[str, float] = {}
        allocated = 0.0
        equity_allocated = 0.0
        for candidate in selected_config:
            ticker = str(candidate["ticker"])
            capacity = max_single
            asset_class = str(candidate["asset_class"])
            if asset_class == "GOLD":
                capacity = min(capacity, max_gold)
            elif asset_class == "EQUITY":
                capacity = min(capacity, max(0.0, max_equity - equity_allocated))
            share = min(inverse_volatility[ticker] / total_inverse_volatility, capacity)
            if share <= 1e-12:
                continue
            mix[ticker] = share
            allocated += share
            if asset_class == "EQUITY":
                equity_allocated += share
        mix[sleeve] = max(0.0, 1.0 - allocated)
        return mix, tuple(mix_ticker for mix_ticker in selected if mix_ticker in mix), candidate_details

    def _rotation_explanation(
        self,
        sleeve: str,
        previous: tuple[str, ...],
        selected: tuple[str, ...],
        mix: Mapping[str, float],
        details: Mapping[str, Any],
    ) -> str:
        previous_text = ", ".join(previous) if previous else sleeve
        if not selected:
            return (
                f"자동 자산 검토: 적격 후보 없음 — {sleeve} 유지 "
                "(EMA200 상단 및 3개월 현금 초과 조건 필요)"
            )
        selected_text = ", ".join(selected)
        weights = ", ".join(
            f"{ticker} {mix[ticker] * 100:.1f}%"
            for ticker in selected
        )
        scores = ", ".join(
            f"{ticker} 점수 {details[ticker]['score']:.2f}"
            for ticker in selected
        )
        return (
            f"자동 자산 교체: {previous_text} → {selected_text}; "
            f"배분={weights}; 근거=EMA200 상단·3개월 현금 초과·복합 모멘텀 ({scores})"
        )

    def _apply_rotation(
        self,
        date: Any,
        target: Mapping[str, float],
        market: Mapping[str, Any],
        *,
        suspended: bool = False,
    ) -> tuple[dict[str, float], bool, bool, str | None]:
        """Replace only the configured cash sleeve and retain every base target."""

        assert self.rotation is not None
        sleeve = str(self.rotation["sleeve"])
        sleeve_weight = float(target[sleeve])
        if suspended:
            previous = self._rotation_selected
            changed = any(
                self._rotation_mix.get(str(candidate["ticker"]), 0.0) > 1e-12
                for candidate in self.rotation["candidates"]
            )
            explanation = f"rotation suspended: {sleeve} retained"
            self._rotation_mix = {sleeve: 1.0}
            self._rotation_selected = ()
            self._rotation_last_period = self._period(
                date, str(self.rotation.get("check", "monthly"))
            )
            self._rotation_active = True
            self._rotation_hold_periods = 0
            self.rotation_decision = {
                "date": str(date),
                "reviewed": False,
                "suspended": True,
                "sleeve": sleeve,
                "sleeve_weight": sleeve_weight,
                "previous_selected": previous,
                "selected": (),
                "mix": {sleeve: 1.0},
                "candidates": {},
                "explanation": explanation,
            }
            return dict(target), False, changed, explanation
        active = sleeve_weight > 1e-12
        reviewed = False
        changed = False
        explanation = None
        if active:
            period = self._period(date, str(self.rotation.get("check", "monthly")))
            if self._rotation_last_period is None or period != self._rotation_last_period:
                previous = self._rotation_selected
                mix, selected, details = self._rotation_selection(market)
                selection_changed = selected != previous
                minimum_weight_change = _number(
                    self.rotation.get("minimum_weight_change", 0.0),
                    field="rotation.minimum_weight_change",
                )
                weight_change = max(
                    (
                        abs(mix.get(ticker, 0.0) - self._rotation_mix.get(ticker, 0.0))
                        for ticker in set(mix) | set(self._rotation_mix)
                    ),
                    default=0.0,
                )
                changed = selection_changed or weight_change > max(
                    minimum_weight_change, 1e-12
                )
                if not selection_changed and not changed and self._rotation_mix:
                    mix = dict(self._rotation_mix)
                self._rotation_hold_periods = (
                    self._rotation_hold_periods + 1
                    if not selection_changed and previous
                    else 1 if selected else 0
                )
                self._rotation_mix = mix
                self._rotation_selected = selected
                self._rotation_last_period = period
                self._rotation_active = True
                reviewed = True
                explanation = self._rotation_explanation(
                    sleeve, previous, selected, mix, details
                )
                self.rotation_decision = {
                    "date": str(date),
                    "reviewed": True,
                    "sleeve": sleeve,
                    "sleeve_weight": sleeve_weight,
                    "previous_selected": previous,
                    "selected": selected,
                    "mix": dict(mix),
                    "candidates": details,
                    "explanation": explanation,
                }
        elif self._rotation_active:
            previous = self._rotation_selected
            changed = False
            self._rotation_active = False
            explanation = f"자동 자산 교체 종료: 전략 기본 목표에 따라 {sleeve} 슬리브가 0%"
            self.rotation_decision = {
                "date": str(date),
                "reviewed": False,
                "sleeve": sleeve,
                "sleeve_weight": sleeve_weight,
                "previous_selected": previous,
                "selected": previous,
                "mix": dict(self._rotation_mix),
                "candidates": {},
                "explanation": explanation,
            }

        if not active:
            return dict(target), reviewed, changed, explanation
        adjusted = dict(target)
        adjusted[sleeve] = sleeve_weight * self._rotation_mix.get(sleeve, 0.0)
        for candidate in self.rotation["candidates"]:
            ticker = str(candidate["ticker"])
            adjusted[ticker] = sleeve_weight * self._rotation_mix.get(ticker, 0.0)
        adjusted, risk_cap_applied = self._apply_rotation_risk_cap(adjusted, sleeve)
        if risk_cap_applied:
            explanation = (explanation or "자동 자산 교체") + (
                "; 위험자산 합계가 70%를 넘지 않도록 초과분을 "
                f"{sleeve}에 유지"
            )
            if self.rotation_decision is not None:
                self.rotation_decision["explanation"] = explanation
                self.rotation_decision["risk_cap"] = _ROTATION_RISK_CAP
                self.rotation_decision["risk_weight"] = sum(
                    adjusted.get(ticker, 0.0) for ticker in self.risk_asset_tickers
                )
        if abs(sum(adjusted.values()) - 1.0) > 1e-8:
            raise StrategyDefinitionError("rotation target weights must sum to 100%")
        return adjusted, reviewed, changed, explanation

    def _apply_rotation_risk_cap(
        self, target: Mapping[str, float], sleeve: str
    ) -> tuple[dict[str, float], bool]:
        """Keep a rotation from lifting declared risk assets above 70%."""

        adjusted = dict(target)
        risk_assets = set(self.risk_asset_tickers)
        risk_weight = sum(adjusted.get(ticker, 0.0) for ticker in risk_assets)
        if risk_weight <= _ROTATION_RISK_CAP + 1e-12:
            return adjusted, False
        assert self.rotation is not None
        rotation_risk_assets = [
            str(candidate["ticker"])
            for candidate in self.rotation["candidates"]
            if str(candidate["ticker"]) in risk_assets
        ]
        reducible = sum(adjusted.get(ticker, 0.0) for ticker in rotation_risk_assets)
        excess = risk_weight - _ROTATION_RISK_CAP
        if reducible + 1e-12 < excess:
            raise StrategyDefinitionError(
                "base target risk assets exceed the 70% rotation risk limit"
            )
        scale = max(0.0, (reducible - excess) / reducible)
        for ticker in rotation_risk_assets:
            adjusted[ticker] = adjusted.get(ticker, 0.0) * scale
        adjusted[sleeve] = adjusted.get(sleeve, 0.0) + excess
        return adjusted, True

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

    def _notification_policy(self) -> dict[str, Any]:
        configured = self.definition.get("notifications")
        if configured:
            return deepcopy(dict(configured))
        threshold = None
        for rule in self.definition.get("rebalance", []):
            match = re.search(
                r"target_deviation\(\)\s*>=\s*(\d+(?:\.\d+)?)%",
                str(rule.get("when", "")),
            )
            if match:
                threshold = float(match.group(1)) / 100.0
                break
        policy: dict[str, Any] = {
            "states": {
                name: {"label": name}
                for name, config in self.definition.get("state", {}).items()
                if config.get("rules")
            },
            "prealerts": [],
        }
        if threshold is not None:
            warning = threshold * 2 / 3
            reset = threshold * 8 / 15
            policy["prealerts"] = [{
                "id": "target-deviation",
                "when": f"target_deviation() >= {warning}",
                "reset_when": f"target_deviation() < {reset}",
                "message": (
                    f"목표 비중 괴리가 {warning * 100:.1f}%p에 도달 "
                    f"(리밸런싱 조건 {threshold * 100:g}%p)"
                ),
            }]
        return policy

    def _build_notification_context(
        self, date: Any, market: Mapping[str, Any], portfolio: Any,
        target: Mapping[str, float], previous_target: Mapping[str, float],
        *, rebalance: bool, execution_days: int,
    ) -> dict[str, Any]:
        policy = self._notification_policy()
        evaluator = self._evaluator(market, portfolio, target)
        prices = {
            ticker: market[ticker].get("Close")
            for ticker in self.holding_tickers
            if ticker in market and market[ticker].get("Close") is not None
        }
        current_weights = portfolio.weights(prices) if prices else {}
        target_tickers = set(previous_target) | set(target)
        target_changed = bool(previous_target) and any(
            abs(float(previous_target.get(ticker, 0.0)) - float(target.get(ticker, 0.0)))
            > 1e-12
            for ticker in target_tickers
        )
        return {
            "date": str(date),
            "state_values": deepcopy(self._state_values),
            "state_changes": [
                {
                    "name": name,
                    "previous": self._previous_state_values.get(name),
                    "current": self._state_values.get(name),
                }
                for name in self._changed_state
            ],
            "confirmations": [
                {
                    "name": name,
                    "desired": candidate["value"],
                    "days": int(candidate["days"]),
                    "required_days": int(candidate["required_days"]),
                }
                for name, candidate in self._state_candidates.items()
            ],
            "confirmation_started": [
                {
                    "name": name,
                    "desired": candidate["value"],
                    "days": int(candidate["days"]),
                    "required_days": int(candidate["required_days"]),
                }
                for name, candidate in self._state_candidates.items()
                if candidate["days"] == 1
                and (
                    name not in self._previous_state_candidates
                    or self._previous_state_candidates[name].get("value")
                    != candidate["value"]
                )
            ],
            "current_weights": {
                ticker: float(current_weights.get(ticker, 0.0)) for ticker in target
            },
            "previous_target_weights": deepcopy(dict(previous_target)),
            "target_weights": deepcopy(dict(target)),
            "target_changed": target_changed,
            "rebalance_required": bool(rebalance),
            "execution_days": int(execution_days),
            "notification_policy": policy,
            "prealerts": [
                {
                    "id": str(rule["id"]),
                    "message": str(rule["message"]),
                    "matched": bool(evaluator.evaluate(rule["when"])),
                    "reset": bool(evaluator.evaluate(rule["reset_when"])),
                }
                for rule in policy.get("prealerts", [])
            ],
        }

    def evaluate(self, date: Any, market: Mapping[str, Any], portfolio: Any) -> dict[str, Any]:
        previous_target = deepcopy(self.target or {})
        self._calculate_variables(market, portfolio)
        self._update_state(date, market, portfolio)
        target = self._target_weights(market, portfolio)
        rotation_reviewed = False
        rotation_changed = False
        rotation_reason = None
        if self.rotation is not None:
            suspend_when = self.rotation.get("suspend_when")
            rotation_suspended = bool(
                self._evaluator(market, portfolio, target).evaluate(suspend_when)
            ) if suspend_when is not None else False
            target, rotation_reviewed, rotation_changed, rotation_reason = (
                self._apply_rotation(
                    date, target, market, suspended=rotation_suspended
                )
            )
        rebalance, reason, rule_days = self._should_rebalance(
            date, market, portfolio, target
        )
        if rotation_changed:
            rebalance = True
            reason = (
                f"{reason} | {rotation_reason}" if reason else rotation_reason
            )
        elif rotation_reviewed and reason is None:
            # No trade is necessary if the automatic review keeps the same
            # mix, but retaining the audit message makes that decision visible
            # to API callers and daily history consumers.
            reason = rotation_reason
        execution_days = rule_days or self._execution_days(market, portfolio)
        self.notification_context = self._build_notification_context(
            date, market, portfolio, target, previous_target,
            rebalance=rebalance, execution_days=execution_days,
        )
        self.target = target
        self._evaluated_once = True
        return {
            "rebalance": rebalance,
            "target": target.copy(),
            "days": execution_days,
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

    @property
    def actual_weights(self) -> dict[str, float]:
        return dict(self._actual_weights)

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
        self.source_risk_asset_tickers = source_risk
        source_parameters = getattr(source_strategy, "parameters", {})
        self.risk_product_rebalance_cap = float(
            source_parameters.get("canonical_risk_weight", 0.70)
        )
        self.risk_asset_tickers = tuple(dict.fromkeys(
            product
            for source_asset in source_risk
            for product in self.products.get(source_asset, {source_asset: 1.0})
        ))
        self.target: dict[str, float] | None = None
        self.notification_context: dict[str, Any] | None = None

    def __getattr__(self, name: str) -> Any:
        source = self.__dict__.get("source_strategy")
        if source is None:
            raise AttributeError(name)
        return getattr(source, name)

    def _map_target(
        self,
        source_target: Mapping[str, Any],
        actual_weights: Mapping[str, Any] | None = None,
    ) -> dict[str, float]:
        mapped_target: dict[str, float] = {}
        actual_weights = actual_weights or {}
        for source_asset, raw_weight in source_target.items():
            weight = _number(raw_weight, field=f"source target.{source_asset}")
            configured_products = self.products.get(
                str(source_asset), {str(source_asset): 1.0}
            )
            current_source_weight = sum(
                float(actual_weights.get(product, 0.0))
                for product in configured_products
            )
            preserve_product_mix = (
                len(configured_products) > 1
                and str(source_asset) in self.source_risk_asset_tickers
                and current_source_weight > self.risk_product_rebalance_cap + 1e-8
                and weight > self.risk_product_rebalance_cap + 1e-8
            )
            product_shares = (
                {
                    product: float(actual_weights.get(product, 0.0))
                    / current_source_weight
                    for product in configured_products
                }
                if preserve_product_mix
                else configured_products
            )
            allocated = 0.0
            items = list(product_shares.items())
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
            _require_mapping(source_signal.get("target"), "source target"),
            source_portfolio.actual_weights,
        )
        source_context = deepcopy(
            getattr(self.source_strategy, "notification_context", None)
        )
        if isinstance(source_context, dict):
            previous_source_target = source_context.get(
                "previous_target_weights", {}
            )
            previous_target = (
                self._map_target(previous_source_target, source_portfolio.actual_weights)
                if previous_source_target else {}
            )
            current_weights = {
                ticker: float(source_portfolio.actual_weights.get(ticker, 0.0))
                for ticker in mapped_target
            }
            source_context.update({
                "mapped_products": True,
                "source_strategy_id": self.source_strategy_id,
                "source_current_weights": source_context.get("current_weights", {}),
                "source_target_weights": source_context.get("target_weights", {}),
                "current_weights": current_weights,
                "previous_target_weights": previous_target,
                "target_weights": deepcopy(mapped_target),
            })
            self.notification_context = source_context
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
    hidden_strategy_ids: set[str] = set()
    visibility_path = root / "manifest.json"
    if visibility_path.is_file():
        try:
            visibility = json.loads(visibility_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise StrategyDefinitionError(
                f"invalid strategy manifest: {visibility_path}"
            ) from exc
        if not isinstance(visibility, Mapping):
            raise StrategyDefinitionError("strategy manifest must be a JSON object")
        hidden = visibility.get("hidden_strategy_ids", [])
        if not isinstance(hidden, list) or any(
            not isinstance(strategy_id, str) or not strategy_id
            for strategy_id in hidden
        ):
            raise StrategyDefinitionError(
                "hidden_strategy_ids must be a list of non-empty strings"
            )
        hidden_strategy_ids = set(hidden)
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
        strategy_name = str(definition["strategy"]["id"])
        if enabled_only and (
            not enabled or strategy_name in hidden_strategy_ids
        ):
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

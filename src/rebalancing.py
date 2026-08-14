"""Append-only account ledger and rebalance planning domain models."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from enum import Enum
from types import MappingProxyType
from typing import Any

from strategy_domain import StrategyEvaluation


ZERO = Decimal("0")
ONE = Decimal("1")
WEIGHT_TOLERANCE = Decimal("0.000001")


class LedgerError(ValueError):
    """Base class for invalid ledger operations."""


class DuplicateEventError(LedgerError):
    """Raised when an event ID is reused."""


class InvalidReversalError(LedgerError):
    """Raised when a reversal does not identify one active original event."""


class InsufficientCashError(LedgerError):
    """Raised when replaying an event would make account cash negative."""


class InsufficientPositionError(LedgerError):
    """Raised when replaying an event would create a short position."""


class LedgerEventType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    DIVIDEND = "DIVIDEND"
    CASH_DEPOSIT = "CASH_DEPOSIT"
    CASH_WITHDRAWAL = "CASH_WITHDRAWAL"
    FEE = "FEE"
    ASSET_TRANSFER_IN = "ASSET_TRANSFER_IN"
    ASSET_TRANSFER_OUT = "ASSET_TRANSFER_OUT"
    REVERSAL = "REVERSAL"


def _decimal(value: Any, field_name: str, *, allow_none: bool = True) -> Decimal | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool):
        raise LedgerError(f"{field_name} must be numeric")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise LedgerError(f"{field_name} must be numeric") from None
    if not result.is_finite():
        raise LedgerError(f"{field_name} must be finite")
    return result


def _timestamp(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, time.min)
    else:
        raise LedgerError("timestamp must be a date or datetime")
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _currency(value: str | None) -> str | None:
    if value is None:
        return None
    result = value.strip().upper()
    if len(result) != 3 or not result.isalpha():
        raise LedgerError("currency must be a three-letter code")
    return result


@dataclass(frozen=True)
class LedgerEvent:
    """One immutable fact in a user's account history."""

    event_id: str
    event_type: LedgerEventType
    occurred_at: datetime
    recorded_at: datetime | None = None
    ticker: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    cash_amount: Decimal | None = None
    fee: Decimal = ZERO
    currency: str | None = None
    fx_rate_to_base: Decimal = ONE
    reverses_event_id: str | None = None
    note: str | None = None

    def __post_init__(self):
        event_id = str(self.event_id).strip()
        if not event_id:
            raise LedgerError("event_id must not be empty")
        try:
            event_type = LedgerEventType(self.event_type)
        except ValueError:
            raise LedgerError(f"unsupported event type: {self.event_type}") from None
        occurred_at = _timestamp(self.occurred_at)
        recorded_at = _timestamp(self.recorded_at or occurred_at)
        ticker = self.ticker.strip().upper() if self.ticker else None
        quantity = _decimal(self.quantity, "quantity")
        unit_price = _decimal(self.unit_price, "unit_price")
        cash_amount = _decimal(self.cash_amount, "cash_amount")
        fee = _decimal(self.fee, "fee", allow_none=False)
        fx_rate = _decimal(
            self.fx_rate_to_base, "fx_rate_to_base", allow_none=False
        )
        currency = _currency(self.currency)
        reverses = (
            self.reverses_event_id.strip()
            if self.reverses_event_id
            else None
        )

        if fee < ZERO:
            raise LedgerError("fee must not be negative")
        if fx_rate <= ZERO:
            raise LedgerError("fx_rate_to_base must be positive")

        trade_types = {LedgerEventType.BUY, LedgerEventType.SELL}
        transfer_types = {
            LedgerEventType.ASSET_TRANSFER_IN,
            LedgerEventType.ASSET_TRANSFER_OUT,
        }
        cash_types = {
            LedgerEventType.DIVIDEND,
            LedgerEventType.CASH_DEPOSIT,
            LedgerEventType.CASH_WITHDRAWAL,
            LedgerEventType.FEE,
        }
        if event_type in trade_types:
            if not ticker or quantity is None or quantity <= ZERO:
                raise LedgerError("trade events require a ticker and positive quantity")
            if unit_price is None or unit_price <= ZERO or not currency:
                raise LedgerError("trade events require a positive price and currency")
            if cash_amount is not None or reverses is not None:
                raise LedgerError("trade events contain unsupported fields")
        elif event_type in transfer_types:
            if not ticker or quantity is None or quantity <= ZERO:
                raise LedgerError("asset transfers require a ticker and positive quantity")
            if any(value is not None for value in (unit_price, cash_amount, reverses)):
                raise LedgerError("asset transfers do not contain monetary fields")
            if fee != ZERO:
                raise LedgerError("asset transfers do not contain fees")
        elif event_type in cash_types:
            if cash_amount is None or cash_amount <= ZERO or not currency:
                raise LedgerError("cash events require a positive amount and currency")
            if event_type is LedgerEventType.DIVIDEND and not ticker:
                raise LedgerError("dividend events require a ticker")
            if any(value is not None for value in (quantity, unit_price, reverses)):
                raise LedgerError("cash events contain unsupported fields")
            if fee != ZERO:
                raise LedgerError("cash events do not contain a separate fee")
        elif event_type is LedgerEventType.REVERSAL:
            if not reverses:
                raise LedgerError("reversal events require reverses_event_id")
            if any(
                value is not None
                for value in (ticker, quantity, unit_price, cash_amount, currency)
            ) or fee != ZERO:
                raise LedgerError("reversal events contain only a referenced event ID")

        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "event_type", event_type)
        object.__setattr__(self, "occurred_at", occurred_at)
        object.__setattr__(self, "recorded_at", recorded_at)
        object.__setattr__(self, "ticker", ticker)
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "unit_price", unit_price)
        object.__setattr__(self, "cash_amount", cash_amount)
        object.__setattr__(self, "fee", fee)
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "fx_rate_to_base", fx_rate)
        object.__setattr__(self, "reverses_event_id", reverses)
        if self.note is not None:
            object.__setattr__(self, "note", str(self.note).strip() or None)


@dataclass(frozen=True)
class AccountSnapshot:
    """Holdings reproduced from ledger events at a specific time."""

    as_of: datetime
    base_currency: str
    cash: Decimal
    _positions: Mapping[str, Decimal] = field(repr=False)
    applied_event_ids: tuple[str, ...] = ()

    def __init__(
        self,
        *,
        as_of: date | datetime,
        base_currency: str,
        cash: Decimal,
        positions: Mapping[str, Decimal],
        applied_event_ids: Iterable[str] = (),
    ):
        object.__setattr__(self, "as_of", _timestamp(as_of))
        object.__setattr__(self, "base_currency", _currency(base_currency))
        object.__setattr__(self, "cash", _decimal(cash, "cash", allow_none=False))
        normalized = {
            str(ticker).strip().upper(): _decimal(
                quantity, "position quantity", allow_none=False
            )
            for ticker, quantity in positions.items()
            if _decimal(quantity, "position quantity", allow_none=False) != ZERO
        }
        if any(quantity < ZERO for quantity in normalized.values()):
            raise LedgerError("account snapshot cannot contain short positions")
        object.__setattr__(self, "_positions", MappingProxyType(normalized))
        object.__setattr__(
            self, "applied_event_ids", tuple(str(value) for value in applied_event_ids)
        )

    @property
    def positions(self) -> dict[str, Decimal]:
        return dict(self._positions)


class TransactionLedger:
    """Append-only event ledger that deterministically rebuilds account state."""

    def __init__(
        self,
        base_currency: str,
        events: Iterable[LedgerEvent] = (),
        *,
        allow_negative_cash: bool = False,
    ):
        self.base_currency = _currency(base_currency)
        self.allow_negative_cash = bool(allow_negative_cash)
        self._events: list[LedgerEvent] = []
        self.extend(events)

    @property
    def events(self) -> tuple[LedgerEvent, ...]:
        return tuple(self._events)

    def append(self, event: LedgerEvent) -> None:
        self.extend((event,))

    def extend(self, events: Iterable[LedgerEvent]) -> None:
        candidates = [*self._events, *events]
        self._validate_event_set(candidates)
        self._events = candidates

    @staticmethod
    def _validate_event_set(events: list[LedgerEvent]) -> None:
        by_id: dict[str, LedgerEvent] = {}
        reversed_ids: set[str] = set()
        for event in events:
            if not isinstance(event, LedgerEvent):
                raise LedgerError("ledger accepts only LedgerEvent instances")
            if event.event_id in by_id:
                raise DuplicateEventError(f"duplicate event ID: {event.event_id}")
            by_id[event.event_id] = event

        for event in events:
            if event.event_type is not LedgerEventType.REVERSAL:
                continue
            target = by_id.get(event.reverses_event_id)
            if target is None:
                raise InvalidReversalError(
                    f"reversal target does not exist: {event.reverses_event_id}"
                )
            if target.event_type is LedgerEventType.REVERSAL:
                raise InvalidReversalError("a reversal event cannot be reversed")
            if target.occurred_at > event.occurred_at:
                raise InvalidReversalError("a reversal cannot precede its target event")
            if target.event_id in reversed_ids:
                raise InvalidReversalError(
                    f"event is already reversed: {target.event_id}"
                )
            reversed_ids.add(target.event_id)

    def snapshot(self, as_of: date | datetime | None = None) -> AccountSnapshot:
        cutoff = (
            _timestamp(as_of)
            if as_of is not None
            else max(
                (event.occurred_at for event in self._events),
                default=datetime(1970, 1, 1, tzinfo=timezone.utc),
            )
        )
        relevant = [event for event in self._events if event.occurred_at <= cutoff]
        reversed_ids = {
            event.reverses_event_id
            for event in relevant
            if event.event_type is LedgerEventType.REVERSAL
        }
        ordered = sorted(
            enumerate(relevant),
            key=lambda item: (
                item[1].occurred_at,
                item[1].recorded_at,
                item[0],
            ),
        )

        cash = ZERO
        positions: dict[str, Decimal] = {}
        applied: list[str] = []
        for _, event in ordered:
            if event.event_type is LedgerEventType.REVERSAL:
                applied.append(event.event_id)
                continue
            if event.event_id in reversed_ids:
                continue
            event_type = event.event_type
            if event_type in {LedgerEventType.BUY, LedgerEventType.SELL}:
                notional = event.quantity * event.unit_price
                converted_notional = notional * event.fx_rate_to_base
                converted_fee = event.fee * event.fx_rate_to_base
                position_change = (
                    event.quantity
                    if event_type is LedgerEventType.BUY
                    else -event.quantity
                )
                cash_change = (
                    -(converted_notional + converted_fee)
                    if event_type is LedgerEventType.BUY
                    else converted_notional - converted_fee
                )
                positions[event.ticker] = (
                    positions.get(event.ticker, ZERO) + position_change
                )
            elif event_type in {
                LedgerEventType.ASSET_TRANSFER_IN,
                LedgerEventType.ASSET_TRANSFER_OUT,
            }:
                position_change = (
                    event.quantity
                    if event_type is LedgerEventType.ASSET_TRANSFER_IN
                    else -event.quantity
                )
                positions[event.ticker] = (
                    positions.get(event.ticker, ZERO) + position_change
                )
                cash_change = ZERO
            else:
                converted = event.cash_amount * event.fx_rate_to_base
                cash_change = (
                    converted
                    if event_type
                    in {LedgerEventType.CASH_DEPOSIT, LedgerEventType.DIVIDEND}
                    else -converted
                )

            if event.ticker and positions.get(event.ticker, ZERO) < ZERO:
                raise InsufficientPositionError(
                    f"event {event.event_id} creates a short {event.ticker} position"
                )
            cash += cash_change
            if not self.allow_negative_cash and cash < ZERO:
                raise InsufficientCashError(
                    f"event {event.event_id} makes cash negative"
                )
            applied.append(event.event_id)

        return AccountSnapshot(
            as_of=cutoff,
            base_currency=self.base_currency,
            cash=cash,
            positions=positions,
            applied_event_ids=applied,
        )


@dataclass(frozen=True)
class PriceQuote:
    ticker: str
    price: Decimal
    currency: str
    fx_rate_to_base: Decimal = ONE
    as_of: datetime | None = None

    def __post_init__(self):
        ticker = str(self.ticker).strip().upper()
        if not ticker:
            raise ValueError("quote ticker must not be empty")
        price = _decimal(self.price, "price", allow_none=False)
        fx_rate = _decimal(
            self.fx_rate_to_base, "fx_rate_to_base", allow_none=False
        )
        if price <= ZERO or fx_rate <= ZERO:
            raise ValueError("quote price and FX rate must be positive")
        object.__setattr__(self, "ticker", ticker)
        object.__setattr__(self, "price", price)
        object.__setattr__(self, "currency", _currency(self.currency))
        object.__setattr__(self, "fx_rate_to_base", fx_rate)
        if self.as_of is not None:
            object.__setattr__(self, "as_of", _timestamp(self.as_of))

    @property
    def base_price(self) -> Decimal:
        return self.price * self.fx_rate_to_base


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class SuggestedOrder:
    ticker: str
    side: OrderSide
    quantity: Decimal
    reference_price: Decimal
    fx_rate_to_base: Decimal
    estimated_notional_base: Decimal
    estimated_fee_base: Decimal
    weight_difference: Decimal


@dataclass(frozen=True)
class RebalancePolicy:
    min_trade_amount: Decimal = ZERO
    min_weight_deviation: Decimal = ZERO
    fee_rate: Decimal = ZERO
    allow_fractional: bool = True
    quantity_steps: Mapping[str, Decimal] = field(default_factory=dict)

    def __post_init__(self):
        min_amount = _decimal(
            self.min_trade_amount, "min_trade_amount", allow_none=False
        )
        min_deviation = _decimal(
            self.min_weight_deviation, "min_weight_deviation", allow_none=False
        )
        fee_rate = _decimal(self.fee_rate, "fee_rate", allow_none=False)
        if min_amount < ZERO or min_deviation < ZERO or fee_rate < ZERO:
            raise ValueError("rebalance policy values must not be negative")
        if min_deviation >= ONE or fee_rate >= ONE:
            raise ValueError("weight deviation and fee rate must be below one")
        steps = {
            str(ticker).strip().upper(): _decimal(
                step, "quantity step", allow_none=False
            )
            for ticker, step in self.quantity_steps.items()
        }
        if any(step <= ZERO for step in steps.values()):
            raise ValueError("quantity steps must be positive")
        object.__setattr__(self, "min_trade_amount", min_amount)
        object.__setattr__(self, "min_weight_deviation", min_deviation)
        object.__setattr__(self, "fee_rate", fee_rate)
        object.__setattr__(self, "quantity_steps", MappingProxyType(steps))

    def quantity_step(self, ticker: str) -> Decimal | None:
        configured = self.quantity_steps.get(ticker)
        if configured is not None:
            return configured
        return None if self.allow_fractional else ONE


@dataclass(frozen=True)
class RebalancePlan:
    plan_id: str
    strategy_id: str
    strategy_version: str
    evaluated_at: str
    account_as_of: str
    base_currency: str
    total_value: Decimal
    cash_weight: Decimal
    current_weights: Mapping[str, Decimal]
    target_weights: Mapping[str, Decimal]
    weight_differences: Mapping[str, Decimal]
    suggested_orders: tuple[SuggestedOrder, ...]
    strategy_rebalance_required: bool
    notification_required: bool
    reason: str | None


def _floor_to_step(quantity: Decimal, step: Decimal | None) -> Decimal:
    if step is None:
        return quantity
    return (quantity / step).to_integral_value(rounding=ROUND_DOWN) * step


class RebalancePlanner:
    """Create non-executing order suggestions from a strategy decision."""

    def __init__(self, policy: RebalancePolicy | None = None):
        self.policy = policy or RebalancePolicy()

    @staticmethod
    def _quotes(
        values: Mapping[str, PriceQuote | Decimal | int | float],
        base_currency: str,
    ) -> dict[str, PriceQuote]:
        quotes = {}
        for ticker, value in values.items():
            normalized_ticker = str(ticker).strip().upper()
            quote = (
                value
                if isinstance(value, PriceQuote)
                else PriceQuote(normalized_ticker, value, base_currency)
            )
            if quote.ticker != normalized_ticker:
                raise ValueError("price quote ticker does not match mapping key")
            quotes[normalized_ticker] = quote
        return quotes

    def create_plan(
        self,
        evaluation: StrategyEvaluation,
        account: AccountSnapshot,
        prices: Mapping[str, PriceQuote | Decimal | int | float],
        *,
        include_orders_when_not_required: bool = False,
    ) -> RebalancePlan:
        quotes = self._quotes(prices, account.base_currency)
        target_weights = {
            str(ticker).strip().upper(): _decimal(
                weight, "target weight", allow_none=False
            )
            for ticker, weight in evaluation.target_weights.items()
        }
        if any(weight < ZERO for weight in target_weights.values()):
            raise ValueError("target weights must not be negative")
        if abs(sum(target_weights.values(), ZERO) - ONE) > WEIGHT_TOLERANCE:
            raise ValueError("target weights must sum to one")

        positions = account.positions
        required_tickers = {
            ticker
            for ticker in {*positions, *target_weights}
            if positions.get(ticker, ZERO) != ZERO
            or target_weights.get(ticker, ZERO) != ZERO
        }
        missing = sorted(required_tickers - set(quotes))
        if missing:
            raise ValueError(f"missing price quotes: {', '.join(missing)}")

        current_values = {
            ticker: positions.get(ticker, ZERO) * quotes[ticker].base_price
            for ticker in required_tickers
        }
        total_value = account.cash + sum(current_values.values(), ZERO)
        if total_value <= ZERO:
            raise ValueError("account total value must be positive")

        all_tickers = sorted({*current_values, *target_weights})
        current_weights = {
            ticker: current_values.get(ticker, ZERO) / total_value
            for ticker in all_tickers
        }
        normalized_targets = {
            ticker: target_weights.get(ticker, ZERO) for ticker in all_tickers
        }
        differences = {
            ticker: normalized_targets[ticker] - current_weights[ticker]
            for ticker in all_tickers
        }
        should_plan_orders = (
            evaluation.rebalance_required or include_orders_when_not_required
        )

        sell_orders: list[SuggestedOrder] = []
        buy_candidates: list[tuple[str, Decimal]] = []
        projected_cash = account.cash
        if should_plan_orders:
            for ticker in all_tickers:
                weight_difference = differences[ticker]
                value_difference = weight_difference * total_value
                if value_difference == ZERO:
                    continue
                if (
                    abs(weight_difference) < self.policy.min_weight_deviation
                    or abs(value_difference) < self.policy.min_trade_amount
                ):
                    continue
                quote = quotes[ticker]
                if value_difference < ZERO:
                    raw_quantity = abs(value_difference) / quote.base_price
                    quantity = _floor_to_step(
                        min(raw_quantity, positions.get(ticker, ZERO)),
                        self.policy.quantity_step(ticker),
                    )
                    notional = quantity * quote.base_price
                    if quantity <= ZERO or notional < self.policy.min_trade_amount:
                        continue
                    fee = notional * self.policy.fee_rate
                    sell_orders.append(SuggestedOrder(
                        ticker=ticker,
                        side=OrderSide.SELL,
                        quantity=quantity,
                        reference_price=quote.price,
                        fx_rate_to_base=quote.fx_rate_to_base,
                        estimated_notional_base=notional,
                        estimated_fee_base=fee,
                        weight_difference=weight_difference,
                    ))
                    projected_cash += notional - fee
                elif value_difference > ZERO:
                    buy_candidates.append((ticker, value_difference))

        buy_orders: list[SuggestedOrder] = []
        for ticker, desired_notional in buy_candidates:
            quote = quotes[ticker]
            maximum_notional = projected_cash / (ONE + self.policy.fee_rate)
            notional_limit = min(desired_notional, maximum_notional)
            raw_quantity = notional_limit / quote.base_price
            quantity = _floor_to_step(
                raw_quantity, self.policy.quantity_step(ticker)
            )
            notional = quantity * quote.base_price
            if quantity <= ZERO or notional < self.policy.min_trade_amount:
                continue
            fee = notional * self.policy.fee_rate
            buy_orders.append(SuggestedOrder(
                ticker=ticker,
                side=OrderSide.BUY,
                quantity=quantity,
                reference_price=quote.price,
                fx_rate_to_base=quote.fx_rate_to_base,
                estimated_notional_base=notional,
                estimated_fee_base=fee,
                weight_difference=differences[ticker],
            ))
            projected_cash -= notional + fee

        orders = tuple([*sell_orders, *buy_orders])
        identity_payload = {
            "strategy_id": evaluation.strategy_id,
            "strategy_version": evaluation.strategy_version,
            "evaluated_at": str(evaluation.to_dict()["evaluated_at"]),
            "account_as_of": account.as_of.isoformat(),
            "target_weights": {
                ticker: str(weight) for ticker, weight in normalized_targets.items()
            },
            "current_weights": {
                ticker: str(weight) for ticker, weight in current_weights.items()
            },
            "reason": evaluation.reason,
        }
        plan_id = hashlib.sha256(
            json.dumps(identity_payload, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return RebalancePlan(
            plan_id=plan_id,
            strategy_id=evaluation.strategy_id,
            strategy_version=evaluation.strategy_version,
            evaluated_at=str(evaluation.to_dict()["evaluated_at"]),
            account_as_of=account.as_of.isoformat(),
            base_currency=account.base_currency,
            total_value=total_value,
            cash_weight=account.cash / total_value,
            current_weights=MappingProxyType(current_weights),
            target_weights=MappingProxyType(normalized_targets),
            weight_differences=MappingProxyType(differences),
            suggested_orders=orders,
            strategy_rebalance_required=evaluation.rebalance_required,
            notification_required=(
                evaluation.rebalance_required and bool(orders)
            ),
            reason=evaluation.reason,
        )

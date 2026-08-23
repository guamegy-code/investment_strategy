"""Versioned HTTP API for accounts, ledger events, and rebalance decisions."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
import pandas as pd
from pydantic import BaseModel, Field

from rebalance_service import (
    Account,
    AccountNotFoundError,
    InMemoryAccountRepository,
    RebalanceApplicationService,
    StoredEvaluation,
    StrategyNotFoundError,
)
from rebalancing import (
    DuplicateEventError,
    InvalidReversalError,
    LedgerError,
    LedgerEvent,
    LedgerEventType,
    OrderSide,
    PriceQuote,
    RebalancePlan,
    SuggestedOrder,
)
from strategy_domain import MarketSnapshot, StrategyEvaluation


API_PREFIX = "/api/v1"


class AccountCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    base_currency: str = Field(min_length=3, max_length=3)


class AccountResponse(BaseModel):
    account_id: str
    name: str
    base_currency: str
    created_at: datetime
    event_count: int


class LedgerEventRequest(BaseModel):
    event_id: str | None = Field(default=None, min_length=1, max_length=128)
    event_type: LedgerEventType
    occurred_at: datetime
    recorded_at: datetime | None = None
    ticker: str | None = Field(default=None, max_length=32)
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    cash_amount: Decimal | None = None
    fee: Decimal = Decimal("0")
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    fx_rate_to_base: Decimal = Decimal("1")
    reverses_event_id: str | None = Field(default=None, max_length=128)
    note: str | None = Field(default=None, max_length=500)

    def to_domain(self) -> LedgerEvent:
        return LedgerEvent(
            event_id=self.event_id or str(uuid4()),
            event_type=self.event_type,
            occurred_at=self.occurred_at,
            recorded_at=self.recorded_at,
            ticker=self.ticker,
            quantity=self.quantity,
            unit_price=self.unit_price,
            cash_amount=self.cash_amount,
            fee=self.fee,
            currency=self.currency,
            fx_rate_to_base=self.fx_rate_to_base,
            reverses_event_id=self.reverses_event_id,
            note=self.note,
        )


class LedgerEventResponse(BaseModel):
    event_id: str
    event_type: LedgerEventType
    occurred_at: datetime
    recorded_at: datetime
    ticker: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    cash_amount: Decimal | None
    fee: Decimal
    currency: str | None
    fx_rate_to_base: Decimal
    reverses_event_id: str | None
    note: str | None


class AccountSnapshotResponse(BaseModel):
    as_of: datetime
    base_currency: str
    cash: Decimal
    positions: dict[str, Decimal]
    applied_event_ids: list[str]


class MarketSnapshotRequest(BaseModel):
    as_of: datetime
    assets: dict[str, dict[str, Any]] = Field(min_length=1)

    def to_domain(self) -> MarketSnapshot:
        # Production allocation strategies use pandas monthly-period helpers.
        # The HTTP boundary therefore adapts ISO timestamps to the existing
        # strategy date contract without changing third-party strategy APIs.
        timestamp = pd.Timestamp(self.as_of)
        if timestamp.tzinfo is not None:
            timestamp = timestamp.tz_convert("UTC").tz_localize(None)
        return MarketSnapshot(timestamp, self.assets)


class PriceQuoteRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=32)
    price: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    fx_rate_to_base: Decimal = Field(default=Decimal("1"), gt=0)
    as_of: datetime | None = None

    def to_domain(self) -> PriceQuote:
        return PriceQuote(
            ticker=self.ticker,
            price=self.price,
            currency=self.currency,
            fx_rate_to_base=self.fx_rate_to_base,
            as_of=self.as_of,
        )


class EvaluationCreateRequest(BaseModel):
    strategy_id: str = Field(min_length=1, max_length=100)
    market: MarketSnapshotRequest
    quotes: list[PriceQuoteRequest] = Field(min_length=1)
    include_orders_when_not_required: bool = False


class EvaluationResponse(BaseModel):
    strategy_id: str
    strategy_version: str
    evaluated_at: datetime
    previous_state: str | None
    next_state: str | None
    rebalance_required: bool
    target_weights: dict[str, Decimal]
    execution_days: int
    reason: str | None


class SuggestedOrderResponse(BaseModel):
    ticker: str
    side: OrderSide
    quantity: Decimal
    reference_price: Decimal
    fx_rate_to_base: Decimal
    estimated_notional_base: Decimal
    estimated_fee_base: Decimal
    weight_difference: Decimal


class RebalancePlanResponse(BaseModel):
    plan_id: str
    strategy_id: str
    strategy_version: str
    evaluated_at: str
    account_as_of: str
    base_currency: str
    total_value: Decimal
    cash_weight: Decimal
    current_weights: dict[str, Decimal]
    target_weights: dict[str, Decimal]
    weight_differences: dict[str, Decimal]
    suggested_orders: list[SuggestedOrderResponse]
    strategy_rebalance_required: bool
    notification_required: bool
    reason: str | None


class StoredEvaluationResponse(BaseModel):
    evaluation_id: str
    account_id: str
    strategy_id: str
    created_at: datetime
    evaluation: EvaluationResponse
    plan: RebalancePlanResponse


class StrategyResponse(BaseModel):
    strategy_id: str
    display_name: str


def _account_response(account: Account) -> AccountResponse:
    return AccountResponse(
        account_id=account.account_id,
        name=account.name,
        base_currency=account.base_currency,
        created_at=account.created_at,
        event_count=len(account.ledger.events),
    )


def _event_response(event: LedgerEvent) -> LedgerEventResponse:
    return LedgerEventResponse(
        event_id=event.event_id,
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        recorded_at=event.recorded_at,
        ticker=event.ticker,
        quantity=event.quantity,
        unit_price=event.unit_price,
        cash_amount=event.cash_amount,
        fee=event.fee,
        currency=event.currency,
        fx_rate_to_base=event.fx_rate_to_base,
        reverses_event_id=event.reverses_event_id,
        note=event.note,
    )


def _snapshot_response(account: Account, as_of: datetime | None = None) -> AccountSnapshotResponse:
    snapshot = account.ledger.snapshot(as_of)
    return AccountSnapshotResponse(
        as_of=snapshot.as_of,
        base_currency=snapshot.base_currency,
        cash=snapshot.cash,
        positions=snapshot.positions,
        applied_event_ids=list(snapshot.applied_event_ids),
    )


def _evaluation_response(
    evaluation: StrategyEvaluation, *, stable_strategy_id: str | None = None
) -> EvaluationResponse:
    return EvaluationResponse(
        strategy_id=stable_strategy_id or evaluation.strategy_id,
        strategy_version=evaluation.strategy_version,
        evaluated_at=evaluation.evaluated_at,
        previous_state=evaluation.previous_state,
        next_state=evaluation.next_state,
        rebalance_required=evaluation.rebalance_required,
        target_weights={
            ticker: Decimal(str(weight))
            for ticker, weight in evaluation.target_weights.items()
        },
        execution_days=evaluation.execution_days,
        reason=evaluation.reason,
    )


def _order_response(order: SuggestedOrder) -> SuggestedOrderResponse:
    return SuggestedOrderResponse(
        ticker=order.ticker,
        side=order.side,
        quantity=order.quantity,
        reference_price=order.reference_price,
        fx_rate_to_base=order.fx_rate_to_base,
        estimated_notional_base=order.estimated_notional_base,
        estimated_fee_base=order.estimated_fee_base,
        weight_difference=order.weight_difference,
    )


def _plan_response(
    plan: RebalancePlan, *, stable_strategy_id: str | None = None
) -> RebalancePlanResponse:
    return RebalancePlanResponse(
        plan_id=plan.plan_id,
        strategy_id=stable_strategy_id or plan.strategy_id,
        strategy_version=plan.strategy_version,
        evaluated_at=plan.evaluated_at,
        account_as_of=plan.account_as_of,
        base_currency=plan.base_currency,
        total_value=plan.total_value,
        cash_weight=plan.cash_weight,
        current_weights=dict(plan.current_weights),
        target_weights=dict(plan.target_weights),
        weight_differences=dict(plan.weight_differences),
        suggested_orders=[_order_response(order) for order in plan.suggested_orders],
        strategy_rebalance_required=plan.strategy_rebalance_required,
        notification_required=plan.notification_required,
        reason=plan.reason,
    )


def _stored_evaluation_response(record: StoredEvaluation) -> StoredEvaluationResponse:
    return StoredEvaluationResponse(
        evaluation_id=record.evaluation_id,
        account_id=record.account_id,
        strategy_id=record.strategy_id,
        created_at=record.created_at,
        evaluation=_evaluation_response(
            record.evaluation, stable_strategy_id=record.strategy_id
        ),
        plan=_plan_response(record.plan, stable_strategy_id=record.strategy_id),
    )


def _development_user(
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> str:
    """Temporary identity adapter; replace with real authentication in deployment."""
    if x_user_id is None or not x_user_id.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User-Id is required",
        )
    return x_user_id.strip()


def create_app(
    service: RebalanceApplicationService | None = None,
) -> FastAPI:
    """Create an API app with a replaceable application service."""
    application_service = service or RebalanceApplicationService(
        repository=InMemoryAccountRepository()
    )
    app = FastAPI(
        title="Investment Strategy Rebalance API",
        version="1.0.0",
        description=(
            "Versioned API for append-only account ledgers and rebalance plans."
        ),
    )
    app.state.rebalance_service = application_service

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(f"{API_PREFIX}/strategies", response_model=list[StrategyResponse])
    def list_strategies() -> list[StrategyResponse]:
        return [
            StrategyResponse(
                strategy_id=descriptor.strategy_id,
                display_name=descriptor.display_name,
            )
            for descriptor in application_service.catalog.list()
        ]

    @app.post(
        f"{API_PREFIX}/accounts",
        response_model=AccountResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_account(
        request: AccountCreateRequest,
        owner_id: Annotated[str, Depends(_development_user)],
    ) -> AccountResponse:
        try:
            account = application_service.repository.create_account(
                owner_id=owner_id,
                name=request.name.strip(),
                base_currency=request.base_currency,
            )
            return _account_response(account)
        except LedgerError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None

    @app.get(f"{API_PREFIX}/accounts/{{account_id}}", response_model=AccountResponse)
    def get_account(
        account_id: str,
        owner_id: Annotated[str, Depends(_development_user)],
    ) -> AccountResponse:
        try:
            return _account_response(application_service.repository.get_account(
                owner_id=owner_id, account_id=account_id
            ))
        except AccountNotFoundError:
            raise HTTPException(status_code=404, detail="account not found") from None

    @app.get(
        f"{API_PREFIX}/accounts/{{account_id}}/events",
        response_model=list[LedgerEventResponse],
    )
    def list_events(
        account_id: str,
        owner_id: Annotated[str, Depends(_development_user)],
    ) -> list[LedgerEventResponse]:
        try:
            account = application_service.repository.get_account(
                owner_id=owner_id, account_id=account_id
            )
        except AccountNotFoundError:
            raise HTTPException(status_code=404, detail="account not found") from None
        return [_event_response(event) for event in account.ledger.events]

    @app.post(
        f"{API_PREFIX}/accounts/{{account_id}}/events",
        response_model=LedgerEventResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def append_event(
        account_id: str,
        request: LedgerEventRequest,
        owner_id: Annotated[str, Depends(_development_user)],
    ) -> LedgerEventResponse:
        try:
            event = request.to_domain()
            application_service.repository.append_event(
                owner_id=owner_id, account_id=account_id, event=event
            )
            return _event_response(event)
        except AccountNotFoundError:
            raise HTTPException(status_code=404, detail="account not found") from None
        except (DuplicateEventError, InvalidReversalError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        except (LedgerError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from None

    @app.get(
        f"{API_PREFIX}/accounts/{{account_id}}/snapshot",
        response_model=AccountSnapshotResponse,
    )
    def get_snapshot(
        account_id: str,
        owner_id: Annotated[str, Depends(_development_user)],
    ) -> AccountSnapshotResponse:
        try:
            account = application_service.repository.get_account(
                owner_id=owner_id, account_id=account_id
            )
            return _snapshot_response(account)
        except AccountNotFoundError:
            raise HTTPException(status_code=404, detail="account not found") from None
        except LedgerError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None

    @app.post(
        f"{API_PREFIX}/accounts/{{account_id}}/evaluations",
        response_model=StoredEvaluationResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def evaluate_account(
        account_id: str,
        request: EvaluationCreateRequest,
        owner_id: Annotated[str, Depends(_development_user)],
    ) -> StoredEvaluationResponse:
        quote_values = [quote.to_domain() for quote in request.quotes]
        quotes = {quote.ticker: quote for quote in quote_values}
        if len(quotes) != len(quote_values):
            raise HTTPException(status_code=422, detail="quote tickers must be unique")
        try:
            record = application_service.evaluate_account(
                owner_id=owner_id,
                account_id=account_id,
                strategy_id=request.strategy_id,
                market=request.market.to_domain(),
                quotes=quotes,
                include_orders_when_not_required=(
                    request.include_orders_when_not_required
                ),
            )
            return _stored_evaluation_response(record)
        except AccountNotFoundError:
            raise HTTPException(status_code=404, detail="account not found") from None
        except StrategyNotFoundError:
            raise HTTPException(status_code=404, detail="strategy not found") from None
        except (LedgerError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from None

    @app.get(
        f"{API_PREFIX}/accounts/{{account_id}}/evaluations",
        response_model=list[StoredEvaluationResponse],
    )
    def list_evaluations(
        account_id: str,
        owner_id: Annotated[str, Depends(_development_user)],
    ) -> list[StoredEvaluationResponse]:
        try:
            records = application_service.repository.list_evaluations(
                owner_id=owner_id, account_id=account_id
            )
        except AccountNotFoundError:
            raise HTTPException(status_code=404, detail="account not found") from None
        return [_stored_evaluation_response(record) for record in records]

    return app


app = create_app()

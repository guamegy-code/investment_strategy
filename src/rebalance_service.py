"""Application service and repository boundary for rebalance APIs."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from threading import RLock
from typing import Protocol
from uuid import uuid4

from rebalancing import (
    AccountSnapshot,
    LedgerEvent,
    PriceQuote,
    RebalancePlan,
    RebalancePlanner,
    TransactionLedger,
)
from strategy import (
    ASYMMETRIC_TREND_BAND_ADD_DEFENSE2,
    STATIC_RETIREMENT_7030,
    RetirementAllocationStrategy,
    RetirementAllocationVXUSStrategy,
)
from experimental_strategies import (
    RetirementAllocationProfitBandStrategy,
    RetirementAllocationProfitBandVXUSStrategy,
)
from strategy_domain import (
    MarketSnapshot,
    StrategyEngine,
    StrategyEvaluation,
    StrategyIsolationError,
    StrategyRuntimeState,
)


class AccountNotFoundError(KeyError):
    """Raised when an account does not belong to the current user."""


class StrategyNotFoundError(KeyError):
    """Raised when an API strategy ID is not registered."""


@dataclass(frozen=True)
class StrategyDescriptor:
    strategy_id: str
    display_name: str
    factory: Callable[[], object] = field(repr=False, compare=False)


class StrategyCatalog:
    """Stable mobile-facing IDs for strategies, independent of class names."""

    def __init__(self, descriptors: tuple[StrategyDescriptor, ...]):
        by_id = {descriptor.strategy_id: descriptor for descriptor in descriptors}
        if len(by_id) != len(descriptors):
            raise ValueError("strategy IDs must be unique")
        self._by_id = by_id

    def list(self) -> tuple[StrategyDescriptor, ...]:
        return tuple(self._by_id.values())

    def create(self, strategy_id: str) -> object:
        try:
            return self._by_id[strategy_id].factory()
        except KeyError:
            raise StrategyNotFoundError(strategy_id) from None


DEFAULT_STRATEGY_CATALOG = StrategyCatalog((
    StrategyDescriptor(
        "retirement-allocation",
        "Retirement Allocation",
        RetirementAllocationStrategy,
    ),
    StrategyDescriptor(
        "retirement-allocation-vxus",
        "Retirement Allocation VXUS",
        RetirementAllocationVXUSStrategy,
    ),
    StrategyDescriptor(
        "retirement-allocation-profit-band",
        "Retirement Allocation Profit Band",
        RetirementAllocationProfitBandStrategy,
    ),
    StrategyDescriptor(
        "retirement-allocation-profit-band-vxus",
        "Retirement Allocation Profit Band VXUS",
        RetirementAllocationProfitBandVXUSStrategy,
    ),
    StrategyDescriptor(
        "static-retirement-7030",
        "Static Retirement 70/30",
        STATIC_RETIREMENT_7030,
    ),
    StrategyDescriptor(
        "asymmetric-trend-band-add-defense2",
        "Asymmetric Trend Band Add Defense 2",
        ASYMMETRIC_TREND_BAND_ADD_DEFENSE2,
    ),
))


@dataclass
class Account:
    account_id: str
    owner_id: str
    name: str
    base_currency: str
    ledger: TransactionLedger
    created_at: datetime


@dataclass(frozen=True)
class StoredEvaluation:
    evaluation_id: str
    account_id: str
    strategy_id: str
    evaluation: StrategyEvaluation
    plan: RebalancePlan
    created_at: datetime


class AccountRepository(Protocol):
    def create_account(
        self, *, owner_id: str, name: str, base_currency: str
    ) -> Account: ...

    def get_account(self, *, owner_id: str, account_id: str) -> Account: ...

    def append_event(
        self, *, owner_id: str, account_id: str, event: LedgerEvent
    ) -> Account: ...

    def runtime_state(
        self, *, owner_id: str, account_id: str, strategy_id: str
    ) -> StrategyRuntimeState | None: ...

    def save_evaluation(
        self,
        *,
        owner_id: str,
        account_id: str,
        strategy_id: str,
        runtime_state: StrategyRuntimeState,
        evaluation: StrategyEvaluation,
        plan: RebalancePlan,
    ) -> StoredEvaluation: ...

    def list_evaluations(
        self, *, owner_id: str, account_id: str
    ) -> tuple[StoredEvaluation, ...]: ...


class InMemoryAccountRepository:
    """Thread-safe development repository replaceable by a database adapter."""

    def __init__(self):
        self._accounts: dict[str, Account] = {}
        self._runtime_states: dict[tuple[str, str], StrategyRuntimeState] = {}
        self._evaluations: dict[str, list[StoredEvaluation]] = {}
        self._lock = RLock()

    def create_account(self, *, owner_id: str, name: str, base_currency: str) -> Account:
        account = Account(
            account_id=str(uuid4()),
            owner_id=owner_id,
            name=name,
            base_currency=base_currency,
            ledger=TransactionLedger(base_currency),
            created_at=datetime.now().astimezone(),
        )
        with self._lock:
            self._accounts[account.account_id] = account
            self._evaluations[account.account_id] = []
        return account

    def get_account(self, *, owner_id: str, account_id: str) -> Account:
        with self._lock:
            account = self._accounts.get(account_id)
            if account is None or account.owner_id != owner_id:
                raise AccountNotFoundError(account_id)
            return account

    def append_event(
        self, *, owner_id: str, account_id: str, event: LedgerEvent
    ) -> Account:
        with self._lock:
            account = self.get_account(owner_id=owner_id, account_id=account_id)
            # Constructing a new ledger and replaying it before replacement
            # preserves append-only history and prevents invalid partial state.
            updated_ledger = TransactionLedger(
                account.base_currency,
                (*account.ledger.events, event),
                allow_negative_cash=account.ledger.allow_negative_cash,
            )
            updated_ledger.snapshot()
            account.ledger = updated_ledger
            return account

    def runtime_state(
        self, *, owner_id: str, account_id: str, strategy_id: str
    ) -> StrategyRuntimeState | None:
        self.get_account(owner_id=owner_id, account_id=account_id)
        with self._lock:
            return self._runtime_states.get((account_id, strategy_id))

    def save_evaluation(
        self,
        *,
        owner_id: str,
        account_id: str,
        strategy_id: str,
        runtime_state: StrategyRuntimeState,
        evaluation: StrategyEvaluation,
        plan: RebalancePlan,
    ) -> StoredEvaluation:
        self.get_account(owner_id=owner_id, account_id=account_id)
        record = StoredEvaluation(
            evaluation_id=str(uuid4()),
            account_id=account_id,
            strategy_id=strategy_id,
            evaluation=evaluation,
            plan=plan,
            created_at=datetime.now().astimezone(),
        )
        with self._lock:
            self._runtime_states[(account_id, strategy_id)] = runtime_state
            self._evaluations[account_id].append(record)
        return record

    def list_evaluations(
        self, *, owner_id: str, account_id: str
    ) -> tuple[StoredEvaluation, ...]:
        self.get_account(owner_id=owner_id, account_id=account_id)
        with self._lock:
            return tuple(self._evaluations[account_id])


class AccountPortfolioView:
    """Expose actual account weights through the legacy strategy portfolio API."""

    def __init__(self, account: AccountSnapshot, quotes: Mapping[str, PriceQuote]):
        self._account = account
        self._quotes = dict(quotes)

    def weights(self, prices: Mapping[str, Decimal | float]) -> dict[str, float]:
        positions = self._account.positions
        missing = [
            ticker for ticker, quantity in positions.items()
            if quantity and ticker not in self._quotes
        ]
        missing.extend(ticker for ticker in prices if ticker not in self._quotes)
        if missing:
            raise ValueError(f"missing price quotes: {', '.join(sorted(missing))}")
        total = self._account.cash + sum(
            quantity * self._quotes[ticker].base_price
            for ticker, quantity in positions.items()
        )
        if total <= 0:
            return {ticker: 0.0 for ticker in prices}
        return {
            ticker: float(
                positions.get(ticker, Decimal("0"))
                * self._quotes[ticker].base_price
                / total
            )
            for ticker in prices
        }


class RebalanceApplicationService:
    """Coordinates strategy evaluation, ledger state, and order planning."""

    def __init__(
        self,
        repository: AccountRepository | None = None,
        catalog: StrategyCatalog | None = None,
        planner: RebalancePlanner | None = None,
    ):
        self.repository = repository or InMemoryAccountRepository()
        self.catalog = catalog or DEFAULT_STRATEGY_CATALOG
        self.planner = planner or RebalancePlanner()

    def evaluate_account(
        self,
        *,
        owner_id: str,
        account_id: str,
        strategy_id: str,
        market: MarketSnapshot,
        quotes: Mapping[str, PriceQuote],
        include_orders_when_not_required: bool = False,
    ) -> StoredEvaluation:
        account = self.repository.get_account(
            owner_id=owner_id, account_id=account_id
        )
        strategy = self.catalog.create(strategy_id)
        engine = StrategyEngine(strategy)
        runtime_state = self.repository.runtime_state(
            owner_id=owner_id, account_id=account_id, strategy_id=strategy_id
        )
        snapshot = account.ledger.snapshot(market.as_of)
        portfolio = AccountPortfolioView(snapshot, quotes)
        try:
            step = engine.evaluate(market, portfolio, runtime_state)
        except StrategyIsolationError:
            raise ValueError(
                "registered API strategies must support isolated evaluation"
            ) from None
        if step.next_runtime_state is None:
            raise ValueError(
                "registered API strategies must provide a runtime state"
            )
        plan = self.planner.create_plan(
            step.evaluation,
            snapshot,
            quotes,
            include_orders_when_not_required=include_orders_when_not_required,
        )
        return self.repository.save_evaluation(
            owner_id=owner_id,
            account_id=account_id,
            strategy_id=strategy_id,
            runtime_state=step.next_runtime_state,
            evaluation=step.evaluation,
            plan=plan,
        )

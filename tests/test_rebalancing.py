import sys
import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from rebalancing import (  # noqa: E402
    AccountSnapshot,
    DuplicateEventError,
    InsufficientCashError,
    InsufficientPositionError,
    InvalidReversalError,
    LedgerEvent,
    LedgerEventType,
    OrderSide,
    PriceQuote,
    RebalancePlanner,
    RebalancePolicy,
    TransactionLedger,
)
from strategy_domain import StrategyEvaluation  # noqa: E402


UTC = timezone.utc


def at(day):
    return datetime(2024, 1, day, tzinfo=UTC)


def evaluation(*, required=True, target=None):
    return StrategyEvaluation(
        strategy_id="strategy.RetirementAllocationStrategy",
        strategy_version="1",
        evaluated_at=at(10),
        previous_state="CAUTION",
        next_state="BULL",
        rebalance_required=required,
        target_weights=target or {"QQQ": 0.7, "BND": 0.3},
        execution_days=1,
        reason="TEST_REBALANCE" if required else None,
    )


class TransactionLedgerTests(unittest.TestCase):
    def test_replays_trades_income_fees_and_fx_in_base_currency(self):
        ledger = TransactionLedger("KRW", [
            LedgerEvent(
                "deposit", LedgerEventType.CASH_DEPOSIT, at(1),
                cash_amount="1000000", currency="KRW",
            ),
            LedgerEvent(
                "buy", LedgerEventType.BUY, at(2), ticker="qqq",
                quantity="1", unit_price="100", fee="1",
                currency="USD", fx_rate_to_base="1300",
            ),
            LedgerEvent(
                "dividend", LedgerEventType.DIVIDEND, at(3), ticker="QQQ",
                cash_amount="2", currency="USD", fx_rate_to_base="1300",
            ),
            LedgerEvent(
                "sell", LedgerEventType.SELL, at(4), ticker="QQQ",
                quantity="0.4", unit_price="120", fee="1",
                currency="USD", fx_rate_to_base="1300",
            ),
            LedgerEvent(
                "account-fee", LedgerEventType.FEE, at(5),
                cash_amount="400", currency="KRW",
            ),
        ])

        snapshot = ledger.snapshot()

        self.assertEqual(snapshot.cash, Decimal("932000.0"))
        self.assertEqual(snapshot.positions, {"QQQ": Decimal("0.6")})
        self.assertEqual(
            snapshot.applied_event_ids,
            ("deposit", "buy", "dividend", "sell", "account-fee"),
        )

    def test_reversal_and_replacement_preserve_correction_history(self):
        ledger = TransactionLedger("KRW", [
            LedgerEvent(
                "deposit", LedgerEventType.CASH_DEPOSIT, at(1),
                cash_amount="1000", currency="KRW",
            ),
            LedgerEvent(
                "wrong-buy", LedgerEventType.BUY, at(2), ticker="QQQ",
                quantity="2", unit_price="100", currency="KRW",
            ),
        ])
        ledger.extend([
            LedgerEvent(
                "reverse-wrong-buy", LedgerEventType.REVERSAL, at(3),
                reverses_event_id="wrong-buy",
            ),
            LedgerEvent(
                "correct-buy", LedgerEventType.BUY, at(3), ticker="QQQ",
                quantity="1", unit_price="100", currency="KRW",
            ),
        ])

        before_correction = ledger.snapshot(at(2))
        after_correction = ledger.snapshot(at(3))

        self.assertEqual(before_correction.positions["QQQ"], Decimal("2"))
        self.assertEqual(before_correction.cash, Decimal("800"))
        self.assertEqual(after_correction.positions["QQQ"], Decimal("1"))
        self.assertEqual(after_correction.cash, Decimal("900"))
        self.assertEqual(len(ledger.events), 4)
        self.assertEqual(
            after_correction.applied_event_ids,
            ("deposit", "reverse-wrong-buy", "correct-buy"),
        )

    def test_events_are_immutable(self):
        event = LedgerEvent(
            "deposit", LedgerEventType.CASH_DEPOSIT, at(1),
            cash_amount="1000", currency="KRW",
        )

        with self.assertRaises(FrozenInstanceError):
            event.cash_amount = Decimal("2000")

    def test_duplicate_and_invalid_reversals_are_rejected(self):
        deposit = LedgerEvent(
            "deposit", LedgerEventType.CASH_DEPOSIT, at(1),
            cash_amount="1000", currency="KRW",
        )
        ledger = TransactionLedger("KRW", [deposit])

        with self.assertRaises(DuplicateEventError):
            ledger.append(deposit)
        with self.assertRaises(InvalidReversalError):
            ledger.append(LedgerEvent(
                "bad-reversal", LedgerEventType.REVERSAL, at(2),
                reverses_event_id="missing",
            ))

    def test_replay_rejects_negative_cash_and_short_positions(self):
        buy_without_cash = TransactionLedger("KRW", [LedgerEvent(
            "buy", LedgerEventType.BUY, at(1), ticker="QQQ",
            quantity="1", unit_price="100", currency="KRW",
        )])
        sell_without_position = TransactionLedger("KRW", [LedgerEvent(
            "deposit", LedgerEventType.CASH_DEPOSIT, at(1),
            cash_amount="100", currency="KRW",
        ), LedgerEvent(
            "sell", LedgerEventType.SELL, at(2), ticker="QQQ",
            quantity="1", unit_price="100", currency="KRW",
        )])

        with self.assertRaises(InsufficientCashError):
            buy_without_cash.snapshot()
        with self.assertRaises(InsufficientPositionError):
            sell_without_position.snapshot()

    def test_asset_transfer_supports_an_opening_position_without_fake_cash_trade(self):
        ledger = TransactionLedger("KRW", [
            LedgerEvent(
                "opening-cash", LedgerEventType.CASH_DEPOSIT, at(1),
                cash_amount="100", currency="KRW",
            ),
            LedgerEvent(
                "opening-position", LedgerEventType.ASSET_TRANSFER_IN, at(1),
                ticker="QQQ", quantity="3",
            ),
        ])

        snapshot = ledger.snapshot()

        self.assertEqual(snapshot.cash, Decimal("100"))
        self.assertEqual(snapshot.positions, {"QQQ": Decimal("3")})


class RebalancePlannerTests(unittest.TestCase):
    def test_creates_fractional_buy_orders_from_cash_without_executing_them(self):
        account = AccountSnapshot(
            as_of=at(10), base_currency="KRW", cash=Decimal("1000"),
            positions={},
        )
        planner = RebalancePlanner()

        first = planner.create_plan(
            evaluation(), account, {"QQQ": 100, "BND": 100}
        )
        second = planner.create_plan(
            evaluation(), account, {"QQQ": 100, "BND": 100}
        )

        orders = {order.ticker: order for order in first.suggested_orders}
        self.assertEqual(orders["QQQ"].side, OrderSide.BUY)
        self.assertEqual(orders["QQQ"].quantity, Decimal("7.0"))
        self.assertEqual(orders["BND"].quantity, Decimal("3.0"))
        self.assertTrue(first.notification_required)
        self.assertEqual(first.plan_id, second.plan_id)
        self.assertEqual(account.cash, Decimal("1000"))
        self.assertEqual(account.positions, {})

    def test_sells_overweight_asset_before_buying_underweight_asset(self):
        account = AccountSnapshot(
            as_of=at(10), base_currency="KRW", cash=Decimal("0"),
            positions={"QQQ": Decimal("8"), "BND": Decimal("2")},
        )

        plan = RebalancePlanner().create_plan(
            evaluation(), account, {"QQQ": 100, "BND": 100}
        )

        self.assertEqual(
            [(order.side, order.ticker, order.quantity) for order in plan.suggested_orders],
            [
                (OrderSide.SELL, "QQQ", Decimal("1.0")),
                (OrderSide.BUY, "BND", Decimal("1.0")),
            ],
        )

    def test_non_target_position_is_liquidated_during_required_rebalance(self):
        account = AccountSnapshot(
            as_of=at(10), base_currency="KRW", cash=Decimal("0"),
            positions={"QQQ": Decimal("6"), "BND": Decimal("3"), "GLD": Decimal("1")},
        )

        plan = RebalancePlanner().create_plan(
            evaluation(), account, {"QQQ": 100, "BND": 100, "GLD": 100}
        )

        orders = {(order.side, order.ticker): order for order in plan.suggested_orders}
        self.assertEqual(orders[(OrderSide.SELL, "GLD")].quantity, Decimal("1"))
        self.assertEqual(orders[(OrderSide.BUY, "QQQ")].quantity, Decimal("1.0"))

    def test_no_default_orders_or_notification_when_strategy_does_not_rebalance(self):
        account = AccountSnapshot(
            as_of=at(10), base_currency="KRW", cash=Decimal("1000"), positions={},
        )
        planner = RebalancePlanner()

        plan = planner.create_plan(
            evaluation(required=False), account, {"QQQ": 100, "BND": 100}
        )
        preview = planner.create_plan(
            evaluation(required=False), account, {"QQQ": 100, "BND": 100},
            include_orders_when_not_required=True,
        )

        self.assertEqual(plan.suggested_orders, ())
        self.assertFalse(plan.notification_required)
        self.assertNotEqual(preview.suggested_orders, ())
        self.assertFalse(preview.notification_required)

    def test_whole_share_policy_and_minimum_trade_amount_are_applied(self):
        account = AccountSnapshot(
            as_of=at(10), base_currency="KRW", cash=Decimal("1000"), positions={},
        )
        planner = RebalancePlanner(RebalancePolicy(
            allow_fractional=False,
            min_trade_amount=Decimal("250"),
        ))

        plan = planner.create_plan(
            evaluation(), account, {"QQQ": 333, "BND": 100}
        )

        orders = {order.ticker: order for order in plan.suggested_orders}
        self.assertEqual(orders["QQQ"].quantity, Decimal("2"))
        self.assertEqual(orders["BND"].quantity, Decimal("3"))

    def test_foreign_quote_uses_base_currency_value(self):
        account = AccountSnapshot(
            as_of=at(10), base_currency="KRW", cash=Decimal("130000"), positions={},
        )
        target = evaluation(target={"QQQ": 1.0})

        plan = RebalancePlanner().create_plan(
            target,
            account,
            {"QQQ": PriceQuote("QQQ", "100", "USD", "1300", at(10))},
        )

        self.assertEqual(plan.suggested_orders[0].quantity, Decimal("1"))
        self.assertEqual(
            plan.suggested_orders[0].estimated_notional_base,
            Decimal("130000"),
        )

    def test_missing_price_and_invalid_target_are_rejected(self):
        account = AccountSnapshot(
            as_of=at(10), base_currency="KRW", cash=Decimal("1000"), positions={},
        )
        planner = RebalancePlanner()

        with self.assertRaisesRegex(ValueError, "missing price"):
            planner.create_plan(evaluation(), account, {"QQQ": 100})
        with self.assertRaisesRegex(ValueError, "sum to one"):
            planner.create_plan(
                evaluation(target={"QQQ": 0.6, "BND": 0.3}),
                account,
                {"QQQ": 100, "BND": 100},
            )


if __name__ == "__main__":
    unittest.main()

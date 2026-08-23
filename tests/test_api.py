import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from api import create_app  # noqa: E402
from rebalance_service import (  # noqa: E402
    InMemoryAccountRepository,
    RebalanceApplicationService,
)


def market_payload():
    return {
        "as_of": "2024-01-02T00:00:00Z",
        "assets": {
            "QQQ": {
                "Close": 110.0,
                "EMA20": 105.0,
                "EMA55": 100.0,
                "EMA200": 95.0,
                "ROC5": 2.0,
                "ROC20": 5.0,
                "ROC60": 5.0,
                "EMA20_SLOPE5": 1.0,
                "EMA200_SLOPE20": 0.5,
                "DRAWDOWN120": 0.0,
            },
            "BND": {"Close": 100.0, "ROC40": 1.0},
            "BIL": {"Close": 100.0, "ROC40": 0.0},
        },
    }


def quotes_payload():
    return [
        {"ticker": "QQQ", "price": "100", "currency": "KRW"},
        {"ticker": "BND", "price": "100", "currency": "KRW"},
        {"ticker": "BIL", "price": "100", "currency": "KRW"},
    ]


class RebalanceApiTests(unittest.TestCase):
    def setUp(self):
        service = RebalanceApplicationService(
            repository=InMemoryAccountRepository()
        )
        self.client = TestClient(create_app(service))
        self.headers = {"X-User-Id": "user-1"}

    def create_account(self):
        response = self.client.post(
            "/api/v1/accounts",
            headers=self.headers,
            json={"name": "Retirement Account", "base_currency": "KRW"},
        )
        self.assertEqual(response.status_code, 201)
        return response.json()["account_id"]

    def deposit(self, account_id, event_id="deposit"):
        response = self.client.post(
            f"/api/v1/accounts/{account_id}/events",
            headers=self.headers,
            json={
                "event_id": event_id,
                "event_type": "CASH_DEPOSIT",
                "occurred_at": "2024-01-01T00:00:00Z",
                "cash_amount": "1000",
                "currency": "KRW",
            },
        )
        self.assertEqual(response.status_code, 201)

    def test_health_openapi_and_stable_strategy_ids_are_available(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})

        response = self.client.get("/api/v1/strategies")
        self.assertEqual(response.status_code, 200)
        ids = {item["strategy_id"] for item in response.json()}
        self.assertIn("allocation", ids)
        self.assertIn("profit-band-vxus", ids)

        openapi = self.client.get("/openapi.json").json()
        self.assertIn("/api/v1/accounts/{account_id}/evaluations", openapi["paths"])

    def test_account_endpoints_require_development_identity_and_enforce_ownership(self):
        missing_identity = self.client.post(
            "/api/v1/accounts",
            json={"name": "Retirement Account", "base_currency": "KRW"},
        )
        self.assertEqual(missing_identity.status_code, 401)

        account_id = self.create_account()
        other_user = self.client.get(
            f"/api/v1/accounts/{account_id}",
            headers={"X-User-Id": "user-2"},
        )
        self.assertEqual(other_user.status_code, 404)

    def test_append_only_ledger_event_and_snapshot_contract(self):
        account_id = self.create_account()
        self.deposit(account_id)

        events = self.client.get(
            f"/api/v1/accounts/{account_id}/events", headers=self.headers
        )
        snapshot = self.client.get(
            f"/api/v1/accounts/{account_id}/snapshot", headers=self.headers
        )
        duplicate = self.client.post(
            f"/api/v1/accounts/{account_id}/events",
            headers=self.headers,
            json={
                "event_id": "deposit",
                "event_type": "CASH_DEPOSIT",
                "occurred_at": "2024-01-01T00:00:00Z",
                "cash_amount": "1000",
                "currency": "KRW",
            },
        )

        self.assertEqual(events.status_code, 200)
        self.assertEqual(events.json()[0]["event_id"], "deposit")
        self.assertEqual(snapshot.status_code, 200)
        self.assertEqual(snapshot.json()["cash"], "1000")
        self.assertEqual(snapshot.json()["positions"], {})
        self.assertEqual(duplicate.status_code, 409)

    def test_evaluation_persists_plan_without_assuming_suggested_orders_executed(self):
        account_id = self.create_account()
        self.deposit(account_id)

        response = self.client.post(
            f"/api/v1/accounts/{account_id}/evaluations",
            headers=self.headers,
            json={
                "strategy_id": "allocation",
                "market": market_payload(),
                "quotes": quotes_payload(),
            },
        )
        body = response.json()
        stored = self.client.get(
            f"/api/v1/accounts/{account_id}/evaluations", headers=self.headers
        )
        snapshot = self.client.get(
            f"/api/v1/accounts/{account_id}/snapshot", headers=self.headers
        )

        self.assertEqual(response.status_code, 201, body)
        self.assertEqual(body["strategy_id"], "allocation")
        self.assertEqual(
            body["evaluation"]["strategy_id"], "allocation"
        )
        self.assertEqual(body["plan"]["strategy_id"], "allocation")
        self.assertTrue(body["evaluation"]["rebalance_required"])
        self.assertTrue(body["plan"]["notification_required"])
        self.assertEqual(len(body["plan"]["suggested_orders"]), 2)
        self.assertEqual(stored.status_code, 200)
        self.assertEqual(len(stored.json()), 1)
        self.assertEqual(snapshot.json()["cash"], "1000")
        self.assertEqual(snapshot.json()["positions"], {})

    def test_evaluation_rejects_unknown_strategy_and_missing_quote(self):
        account_id = self.create_account()
        self.deposit(account_id)
        unknown = self.client.post(
            f"/api/v1/accounts/{account_id}/evaluations",
            headers=self.headers,
            json={
                "strategy_id": "not-registered",
                "market": market_payload(),
                "quotes": quotes_payload(),
            },
        )
        missing_quote = self.client.post(
            f"/api/v1/accounts/{account_id}/evaluations",
            headers=self.headers,
            json={
                "strategy_id": "allocation",
                "market": market_payload(),
                "quotes": [quotes_payload()[0], quotes_payload()[2]],
            },
        )

        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(missing_quote.status_code, 422)


if __name__ == "__main__":
    unittest.main()

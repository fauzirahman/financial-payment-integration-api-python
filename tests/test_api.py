import hashlib
import hmac
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main
from app.main import app
from app.repositories.payment_repository import PaymentRepository

CUSTOMER_ID = "00000000-0000-0000-0000-000000000101"


class PaymentAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = PaymentRepository()
        main.payment_repository = self.repository
        self.client = TestClient(app)

    def test_create_payment_normalizes_currency_and_returns_contract(self) -> None:
        response = self.client.post(
            "/api/v1/payments",
            json={
                "reference": " PAY-001 ",
                "amount": 150000,
                "currency": "idr",
                "customer_id": CUSTOMER_ID,
                "method": "bank_transfer",
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            {key: response.json()[key] for key in ("id", "reference", "amount", "currency", "status")},
            {
                "id": 1,
                "reference": "PAY-001",
                "amount": 150000,
                "currency": "IDR",
                "status": "PENDING",
            },
        )
        self.assertIn("created_at", response.json())
        self.assertIn("updated_at", response.json())

    def test_create_payment_rejects_bad_input_and_duplicate_reference(self) -> None:
        invalid = self.client.post(
            "/api/v1/payments",
            json={
                "reference": "PAY-001",
                "amount": 0,
                "currency": "IDR",
                "customer_id": CUSTOMER_ID,
                "method": "BANK_TRANSFER",
                "extra": 1,
            },
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.json()["error"], "invalid request")

        payload = {
            "reference": "PAY-001",
            "amount": 100,
            "currency": "IDR",
            "customer_id": CUSTOMER_ID,
            "method": "BANK_TRANSFER",
        }
        self.assertEqual(self.client.post("/api/v1/payments", json=payload).status_code, 201)
        self.assertEqual(self.client.post("/api/v1/payments", json=payload).status_code, 409)

        with_key = {
            "reference": "PAY-KEY-1",
            "amount": 100,
            "currency": "IDR",
            "customer_id": CUSTOMER_ID,
            "method": "BANK_TRANSFER",
        }
        self.assertEqual(
            self.client.post(
                "/api/v1/payments",
                json=with_key,
                headers={"Idempotency-Key": "request-001"},
            ).status_code,
            201,
        )
        with_key["reference"] = "PAY-KEY-2"
        self.assertEqual(
            self.client.post(
                "/api/v1/payments",
                json=with_key,
                headers={"Idempotency-Key": "request-001"},
            ).status_code,
            409,
        )

    def test_list_and_get_payment(self) -> None:
        self.assertEqual(self.client.get("/api/v1/payments").json(), [])
        created = self.client.post(
            "/api/v1/payments",
            json={
                "reference": "PAY-002",
                "amount": 250,
                "currency": "IDR",
                "customer_id": CUSTOMER_ID,
                "method": "CARD",
            },
        ).json()

        self.assertEqual(self.client.get("/api/v1/payments").json(), [created])
        self.assertEqual(self.client.get("/api/v1/payments/1").json(), created)
        self.assertEqual(self.client.get("/api/v1/payments/99").status_code, 404)

    def test_webhook_requires_valid_signature_and_updates_payment(self) -> None:
        self.client.post(
            "/api/v1/payments",
            json={
                "reference": "PAY-003",
                "amount": 500,
                "currency": "IDR",
                "customer_id": CUSTOMER_ID,
                "method": "CARD",
            },
        )
        event = {
            "event_id": "evt-003",
            "type": "payment.success",
            "reference": "PAY-003",
        }
        body = json.dumps(event, separators=(",", ":")).encode()
        signature = hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()

        with patch.dict("os.environ", {"WEBHOOK_SECRET": "test-secret"}):
            rejected = self.client.post(
                "/api/v1/webhooks/payment",
                content=body,
                headers={"Content-Type": "application/json"},
            )
            self.assertEqual(rejected.status_code, 401)
            accepted = self.client.post(
                "/api/v1/webhooks/payment",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Signature": f"sha256={signature}",
                },
            )

        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()["status"], "SUCCESS")
        self.assertEqual(self.client.get("/api/v1/payments/1").json()["status"], "SUCCESS")
        with patch.dict("os.environ", {"WEBHOOK_SECRET": "test-secret"}):
            duplicate = self.client.post(
                "/api/v1/webhooks/payment",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Signature": f"sha256={signature}",
                },
            )
        self.assertEqual(duplicate.status_code, 409)

    def test_reports_aggregate_payment_data(self) -> None:
        self.client.post(
            "/api/v1/payments",
            json={
                "reference": "PAY-004",
                "amount": 100,
                "currency": "IDR",
                "customer_id": CUSTOMER_ID,
                "method": "BANK_TRANSFER",
            },
        )
        self.client.post(
            "/api/v1/payments",
            json={
                "reference": "PAY-005",
                "amount": 200,
                "currency": "IDR",
                "customer_id": CUSTOMER_ID,
                "method": "BANK_TRANSFER",
            },
        )

        summary = self.client.get("/api/v1/reports/payment-summary").json()
        daily = self.client.get("/api/v1/reports/daily-payments").json()
        self.assertEqual(summary["items"], [
            {"status": "PENDING", "currency": "IDR", "count": 2, "amount": 300}
        ])
        self.assertEqual(daily["items"][0]["count"], 2)
        self.assertEqual(daily["items"][0]["amount"], 300)


if __name__ == "__main__":
    unittest.main()

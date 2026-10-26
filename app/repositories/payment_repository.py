import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from threading import RLock
from uuid import UUID

import psycopg
from dotenv import load_dotenv
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

from app.schemas.payment import PaymentResponse

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


class PaymentRepository:
    """Process-local repository used by tests."""

    def __init__(self) -> None:
        self._payments: dict[int, PaymentResponse] = {}
        self._references: dict[str, int] = {}
        self._idempotency_keys: set[str] = set()
        self._webhook_events: set[str] = set()
        self._next_id = 1
        self._lock = RLock()

    def create(
        self,
        reference: str,
        amount: int,
        currency: str,
        customer_id: UUID,
        method: str,
        idempotency_key: str | None,
    ) -> PaymentResponse:
        with self._lock:
            if reference in self._references:
                raise ValueError("payment reference already exists")
            if idempotency_key and idempotency_key in self._idempotency_keys:
                raise ValueError("idempotency key already exists")

            now = datetime.now(timezone.utc)
            payment = PaymentResponse(
                id=self._next_id,
                reference=reference,
                amount=amount,
                currency=currency,
                status="PENDING",
                created_at=now,
                updated_at=now,
                customer_id=str(customer_id) if customer_id else None,
                method=method,
            )
            self._payments[self._next_id] = payment
            self._references[reference] = self._next_id
            if idempotency_key:
                self._idempotency_keys.add(idempotency_key)
            self._next_id += 1
            return payment

    def list(self, customer_id: UUID | None = None) -> list[PaymentResponse]:
        with self._lock:
            payments = self._payments.values()
            if customer_id is not None:
                return [
                    payment
                    for payment in payments
                    if payment.customer_id == str(customer_id)
                ]
            return list(payments)

    def get(
        self, payment_id: str | int, customer_id: UUID | None = None
    ) -> PaymentResponse | None:
        with self._lock:
            try:
                payment = self._payments.get(int(payment_id))
            except ValueError:
                return None
            if payment and customer_id is not None:
                if payment.customer_id != str(customer_id):
                    return None
            return payment

    def apply_webhook(self, event_id: str, reference: str, status: str) -> PaymentResponse:
        with self._lock:
            if event_id in self._webhook_events:
                raise ValueError("webhook event already processed")
            payment_id = self._references.get(reference)
            if payment_id is None:
                raise LookupError("payment not found")

            payment = self._payments[payment_id]
            if payment.status not in {"PENDING", "PROCESSING"}:
                raise ValueError("invalid payment status transition")

            updated = payment.model_copy(
                update={"status": status, "updated_at": datetime.now(timezone.utc)}
            )
            self._payments[payment_id] = updated
            self._webhook_events.add(event_id)
            return updated


class PostgresPaymentRepository:
    """Repository for the application's Neon payments table."""

    _schema_lock = RLock()

    def __init__(self) -> None:
        self._schema_initialized = False

    def _connect(self) -> psycopg.Connection:
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise RuntimeError("DATABASE_URL must be configured")
        return psycopg.connect(database_url, row_factory=dict_row)

    def _ensure_schema(self) -> None:
        if self._schema_initialized:
            return
        with self._schema_lock:
            if self._schema_initialized:
                return
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS financial_payment_api_idempotency_keys_v2 (
                        idempotency_key TEXT PRIMARY KEY,
                        payment_id UUID NOT NULL REFERENCES payments(id)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS financial_payment_api_webhook_events (
                        event_id VARCHAR(200) PRIMARY KEY
                    )
                    """
                )
            self._schema_initialized = True

    def create(
        self,
        reference: str,
        amount: int,
        currency: str,
        customer_id: UUID,
        method: str,
        idempotency_key: str | None,
    ) -> PaymentResponse:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                customer = connection.execute(
                    "SELECT id FROM customers WHERE id = %s",
                    (customer_id,),
                ).fetchone()
                if customer is None:
                    raise LookupError("customer not found")
                now = datetime.now(timezone.utc)
                row = connection.execute(
                    """
                    INSERT INTO payments
                        (id, payment_number, customer_id, amount, currency, method,
                         status, created_at, updated_at)
                    VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, 'PENDING', %s, %s)
                    RETURNING id, payment_number, customer_id, amount, currency, method,
                              status, gateway, gateway_transaction_id, paid_at,
                              description, created_at, updated_at
                    """,
                    (reference, customer_id, amount, currency, method, now, now),
                ).fetchone()
                if idempotency_key:
                    connection.execute(
                        """
                        INSERT INTO financial_payment_api_idempotency_keys_v2
                            (idempotency_key, payment_id)
                        VALUES (%s, %s)
                        """,
                        (idempotency_key, row["id"]),
                    )
            return self._to_payment_response(row)
        except UniqueViolation as exc:
            if exc.diag.constraint_name == "payments_payment_number_unique":
                raise ValueError("payment reference already exists") from exc
            if exc.diag.constraint_name == "financial_payment_api_idempotency_keys_v2_pkey":
                raise ValueError("idempotency key already exists") from exc
            raise

    def list(self, customer_id: UUID | None = None) -> list[PaymentResponse]:
        customer_filter = "WHERE customer_id = %s" if customer_id else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, payment_number, customer_id, amount, currency, method,
                       status, gateway, gateway_transaction_id, paid_at,
                       description, created_at, updated_at
                FROM payments
                {customer_filter}
                ORDER BY created_at DESC NULLS LAST, payment_number
                """,
                (customer_id,) if customer_id else (),
            ).fetchall()
        return [self._to_payment_response(row) for row in rows]

    def get(
        self, payment_id: str | int, customer_id: UUID | None = None
    ) -> PaymentResponse | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, payment_number, customer_id, amount, currency, method,
                       status, gateway, gateway_transaction_id, paid_at,
                       description, created_at, updated_at
                FROM payments
                WHERE (id::text = %s OR payment_number = %s)
                  AND (%s::uuid IS NULL OR customer_id = %s)
                """,
                (str(payment_id), str(payment_id), customer_id, customer_id),
            ).fetchone()
        return self._to_payment_response(row) if row else None

    def apply_webhook(
        self, event_id: str, reference: str, status: str
    ) -> PaymentResponse:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO financial_payment_api_webhook_events (event_id) VALUES (%s)",
                    (event_id,),
                )
                row = connection.execute(
                    """
                    SELECT id, payment_number, customer_id, amount, currency, method,
                           status, gateway, gateway_transaction_id, paid_at,
                           description, created_at, updated_at
                    FROM payments
                    WHERE payment_number = %s OR id::text = %s
                    FOR UPDATE
                    """,
                    (reference, reference),
                ).fetchone()
                if row is None:
                    raise LookupError("payment not found")
                if row["status"] not in {"PENDING", "PROCESSING"}:
                    raise ValueError("invalid payment status transition")
                updated = connection.execute(
                    """
                    UPDATE payments
                    SET status = %s,
                        paid_at = CASE
                            WHEN %s = 'SUCCESS' THEN COALESCE(paid_at, %s)
                            ELSE paid_at
                        END,
                        updated_at = %s
                    WHERE id = %s
                    RETURNING id, payment_number, customer_id, amount, currency, method,
                              status, gateway, gateway_transaction_id, paid_at,
                              description, created_at, updated_at
                    """,
                    (
                        status,
                        status,
                        datetime.now(timezone.utc),
                        datetime.now(timezone.utc),
                        row["id"],
                    ),
                ).fetchone()
            return self._to_payment_response(updated)
        except UniqueViolation as exc:
            if exc.diag.constraint_name == "financial_payment_api_webhook_events_pkey":
                raise ValueError("webhook event already processed") from exc
            raise

    @staticmethod
    def _to_payment_response(row: dict) -> PaymentResponse:
        amount = row["amount"]
        if isinstance(amount, Decimal) and amount == amount.to_integral_value():
            amount = int(amount)
        return PaymentResponse(
            id=str(row["id"]),
            reference=row["payment_number"],
            amount=amount,
            currency=row["currency"].strip(),
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            customer_id=str(row["customer_id"]),
            method=row["method"],
            gateway=row["gateway"],
            gateway_transaction_id=row["gateway_transaction_id"],
            paid_at=row["paid_at"],
            description=row["description"],
        )

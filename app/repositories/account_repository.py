import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from uuid import UUID, uuid4

import psycopg
from dotenv import load_dotenv
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

from app.core.security import (
    generate_access_token,
    hash_access_token,
    hash_password,
    verify_password,
)

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

_DUMMY_PASSWORD_HASH = hash_password("invalid-login-password")


class AccountRepository:
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
                    CREATE TABLE IF NOT EXISTS financial_payment_api_users (
                        id UUID PRIMARY KEY,
                        email VARCHAR(254) NOT NULL UNIQUE,
                        password_hash TEXT NOT NULL,
                        role VARCHAR(20) NOT NULL CHECK (role IN ('admin', 'customer')),
                        customer_id UUID REFERENCES customers(id),
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS financial_payment_api_sessions (
                        token_hash CHAR(64) PRIMARY KEY,
                        user_id UUID NOT NULL
                            REFERENCES financial_payment_api_users(id) ON DELETE CASCADE,
                        expires_at TIMESTAMPTZ NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS financial_payment_api_sessions_expiry_idx
                    ON financial_payment_api_sessions (expires_at)
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS financial_payment_api_payment_accounts (
                        id UUID PRIMARY KEY,
                        customer_id UUID NOT NULL REFERENCES customers(id),
                        label VARCHAR(100) NOT NULL,
                        provider VARCHAR(100) NOT NULL,
                        account_type VARCHAR(30) NOT NULL CHECK (
                            account_type IN ('bank_account', 'credit_card',
                                             'debit_card', 'e_wallet')
                        ),
                        last4 CHAR(4),
                        status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                            CHECK (status IN ('ACTIVE', 'INACTIVE')),
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
                admin_email = os.getenv("INITIAL_ADMIN_EMAIL", "").strip().lower()
                admin_password = os.getenv("INITIAL_ADMIN_PASSWORD")
                if admin_email and admin_password:
                    existing_admin = connection.execute(
                        """
                        SELECT id FROM financial_payment_api_users
                        WHERE email = %s
                        """,
                        (admin_email,),
                    ).fetchone()
                    if existing_admin is None:
                        connection.execute(
                            """
                            INSERT INTO financial_payment_api_users
                                (id, email, password_hash, role)
                            VALUES (%s, %s, %s, 'admin')
                                ON CONFLICT (email) DO NOTHING
                            """,
                            (uuid4(), admin_email, hash_password(admin_password)),
                        )
            self._schema_initialized = True

    @staticmethod
    def _user_response(row: dict) -> dict:
        return {
            "id": row["user_id"],
            "email": row["email"],
            "role": row["role"],
            "customer_id": row["customer_id"],
            "name": row.get("name"),
            "phone": row.get("phone"),
            "created_at": row["created_at"],
        }

    @staticmethod
    def _customer_response(row: dict) -> dict:
        return {
            "id": row["id"],
            "customer_number": row["customer_number"],
            "name": row["name"],
            "email": row["email"],
            "phone": row["phone"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _payment_account_response(row: dict) -> dict:
        return {
            "id": row["id"],
            "customer_id": row["customer_id"],
            "label": row["label"],
            "provider": row["provider"],
            "account_type": row["account_type"],
            "last4": row["last4"].strip() if row["last4"] else None,
            "status": row["status"],
            "created_at": row["created_at"],
        }

    def register(
        self, name: str, email: str, password: str, phone: str | None
    ) -> tuple[dict, str, datetime]:
        self._ensure_schema()
        password_hash = hash_password(password)
        token = generate_access_token()
        expires_at = datetime.now(timezone.utc) + timedelta(hours=12)
        try:
            with self._connect() as connection:
                existing_user = connection.execute(
                    """
                    SELECT id FROM financial_payment_api_users
                    WHERE email = %s
                    """,
                    (email,),
                ).fetchone()
                if existing_user:
                    raise ValueError("email is already registered")

                customer = connection.execute(
                    """
                    SELECT id FROM customers WHERE lower(email) = %s
                    """,
                    (email,),
                ).fetchone()
                if customer:
                    raise ValueError("email is unavailable for registration")

                customer_id = uuid4()
                now = datetime.now(timezone.utc)
                connection.execute(
                    """
                    INSERT INTO customers
                        (id, customer_number, name, email, phone, status,
                         created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, 'ACTIVE', %s, %s)
                    """,
                    (
                        customer_id,
                        f"CUS-{customer_id.hex[:16].upper()}",
                        name,
                        email,
                        phone,
                        now,
                        now,
                    ),
                )

                user_id = uuid4()
                connection.execute(
                    """
                    INSERT INTO financial_payment_api_users
                        (id, email, password_hash, role, customer_id)
                    VALUES (%s, %s, %s, 'customer', %s)
                    """,
                    (user_id, email, password_hash, customer_id),
                )
                connection.execute(
                    """
                    INSERT INTO financial_payment_api_sessions
                        (token_hash, user_id, expires_at)
                    VALUES (%s, %s, %s)
                    """,
                    (hash_access_token(token), user_id, expires_at),
                )
                row = connection.execute(
                    """
                    SELECT u.id AS user_id, u.email, u.role, u.customer_id,
                           u.created_at, c.name, c.phone
                    FROM financial_payment_api_users u
                    LEFT JOIN customers c ON c.id = u.customer_id
                    WHERE u.id = %s
                    """,
                    (user_id,),
                ).fetchone()
            return self._user_response(row), token, expires_at
        except UniqueViolation as exc:
            raise ValueError("email is unavailable for registration") from exc

    def provision_customer_user(self, customer_id: UUID, password: str) -> dict:
        self._ensure_schema()
        password_hash = hash_password(password)
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT id, email, status FROM customers WHERE id = %s
                    FOR UPDATE
                    """,
                    (customer_id,),
                ).fetchone()
                if row is None:
                    raise LookupError("customer not found")
                if row["status"] != "ACTIVE":
                    raise ValueError("customer is inactive")
                if connection.execute(
                    """
                    SELECT id FROM financial_payment_api_users
                    WHERE customer_id = %s OR email = %s
                    """,
                    (customer_id, row["email"].lower()),
                ).fetchone():
                    raise ValueError("customer already has a user account")
                user_id = uuid4()
                connection.execute(
                    """
                    INSERT INTO financial_payment_api_users
                        (id, email, password_hash, role, customer_id)
                    VALUES (%s, %s, %s, 'customer', %s)
                    """,
                    (user_id, row["email"].lower(), password_hash, customer_id),
                )
                user = connection.execute(
                    """
                    SELECT u.id AS user_id, u.email, u.role, u.customer_id,
                           u.created_at, c.name, c.phone
                    FROM financial_payment_api_users u
                    JOIN customers c ON c.id = u.customer_id
                    WHERE u.id = %s
                    """,
                    (user_id,),
                ).fetchone()
            return self._user_response(user)
        except UniqueViolation as exc:
            raise ValueError("customer already has a user account") from exc

    def login(self, email: str, password: str) -> tuple[dict, str, datetime] | None:
        self._ensure_schema()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT u.id AS user_id, u.email, u.password_hash, u.role,
                       u.customer_id, u.created_at, c.name, c.phone, c.status
                FROM financial_payment_api_users u
                LEFT JOIN customers c ON c.id = u.customer_id
                WHERE u.email = %s
                """,
                (email,),
            ).fetchone()
            if row is None:
                verify_password(password, _DUMMY_PASSWORD_HASH)
                return None
            if not verify_password(password, row["password_hash"]):
                return None
            if row["customer_id"] is not None and row["status"] != "ACTIVE":
                return None
            token = generate_access_token()
            expires_at = datetime.now(timezone.utc) + timedelta(hours=12)
            connection.execute(
                """
                INSERT INTO financial_payment_api_sessions
                    (token_hash, user_id, expires_at)
                VALUES (%s, %s, %s)
                """,
                (hash_access_token(token), row["user_id"], expires_at),
            )
        return self._user_response(row), token, expires_at

    def authenticate(self, token: str) -> dict | None:
        self._ensure_schema()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT u.id AS user_id, u.email, u.role, u.customer_id,
                       u.created_at, c.name, c.phone
                FROM financial_payment_api_sessions s
                JOIN financial_payment_api_users u ON u.id = s.user_id
                LEFT JOIN customers c ON c.id = u.customer_id
                WHERE s.token_hash = %s AND s.expires_at > now()
                  AND (u.customer_id IS NULL OR c.status = 'ACTIVE')
                """,
                (hash_access_token(token),),
            ).fetchone()
        return self._user_response(row) if row else None

    def logout(self, token: str) -> None:
        self._ensure_schema()
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM financial_payment_api_sessions WHERE token_hash = %s",
                (hash_access_token(token),),
            )

    def list_customers(self) -> list[dict]:
        self._ensure_schema()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, customer_number, name, email, phone, status,
                       created_at, updated_at
                FROM customers ORDER BY created_at DESC NULLS LAST, customer_number
                """
            ).fetchall()
        return [self._customer_response(row) for row in rows]

    def get_customer(self, customer_id: UUID) -> dict | None:
        self._ensure_schema()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, customer_number, name, email, phone, status,
                       created_at, updated_at
                FROM customers WHERE id = %s
                """,
                (customer_id,),
            ).fetchone()
        return self._customer_response(row) if row else None

    def create_customer(self, name: str, email: str, phone: str | None) -> dict:
        self._ensure_schema()
        customer_id = uuid4()
        now = datetime.now(timezone.utc)
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    INSERT INTO customers
                        (id, customer_number, name, email, phone, status,
                         created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, 'ACTIVE', %s, %s)
                    RETURNING id, customer_number, name, email, phone, status,
                              created_at, updated_at
                    """,
                    (
                        customer_id,
                        f"CUS-{customer_id.hex[:16].upper()}",
                        name,
                        email,
                        phone,
                        now,
                        now,
                    ),
                ).fetchone()
            return self._customer_response(row)
        except UniqueViolation as exc:
            raise ValueError("customer email or number already exists") from exc

    def update_customer(self, customer_id: UUID, updates: dict) -> dict | None:
        self._ensure_schema()
        allowed = {"name", "email", "phone"}
        fields = [(key, value) for key, value in updates.items() if key in allowed]
        if not fields:
            return self.get_customer(customer_id)
        assignments = ", ".join(f"{key} = %s" for key, _ in fields)
        values = [value for _, value in fields]
        try:
            with self._connect() as connection:
                row = connection.execute(
                    f"""
                    UPDATE customers SET {assignments}, updated_at = %s
                    WHERE id = %s
                    RETURNING id, customer_number, name, email, phone, status,
                              created_at, updated_at
                    """,
                    (*values, datetime.now(timezone.utc), customer_id),
                ).fetchone()
                if row and "email" in updates:
                    connection.execute(
                        """
                        UPDATE financial_payment_api_users SET email = %s
                        WHERE customer_id = %s
                        """,
                        (updates["email"], customer_id),
                    )
            return self._customer_response(row) if row else None
        except UniqueViolation as exc:
            raise ValueError("customer email already exists") from exc

    def deactivate_customer(self, customer_id: UUID) -> bool:
        self._ensure_schema()
        with self._connect() as connection:
            row = connection.execute(
                """
                UPDATE customers SET status = 'INACTIVE', updated_at = %s
                WHERE id = %s AND status != 'INACTIVE'
                RETURNING id
                """,
                (datetime.now(timezone.utc), customer_id),
            ).fetchone()
            if row:
                connection.execute(
                    """
                    UPDATE financial_payment_api_payment_accounts
                    SET status = 'INACTIVE'
                    WHERE customer_id = %s AND status = 'ACTIVE'
                    """,
                    (customer_id,),
                )
        return row is not None

    def update_profile(self, customer_id: UUID, updates: dict) -> dict | None:
        return self.update_customer(customer_id, updates)

    def list_payment_accounts(self, customer_id: UUID) -> list[dict]:
        self._ensure_schema()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, customer_id, label, provider, account_type, last4,
                       status, created_at
                FROM financial_payment_api_payment_accounts
                WHERE customer_id = %s AND status = 'ACTIVE'
                ORDER BY created_at DESC
                """,
                (customer_id,),
            ).fetchall()
        return [self._payment_account_response(row) for row in rows]

    def create_payment_account(
        self,
        customer_id: UUID,
        label: str,
        provider: str,
        account_type: str,
        last4: str | None,
    ) -> dict:
        self._ensure_schema()
        with self._connect() as connection:
            row = connection.execute(
                """
                INSERT INTO financial_payment_api_payment_accounts
                    (id, customer_id, label, provider, account_type, last4)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, customer_id, label, provider, account_type,
                          last4, status, created_at
                """,
                (uuid4(), customer_id, label, provider, account_type, last4),
            ).fetchone()
        return self._payment_account_response(row)

    def deactivate_payment_account(self, customer_id: UUID, account_id: UUID) -> bool:
        self._ensure_schema()
        with self._connect() as connection:
            row = connection.execute(
                """
                UPDATE financial_payment_api_payment_accounts
                SET status = 'INACTIVE'
                WHERE id = %s AND customer_id = %s AND status = 'ACTIVE'
                RETURNING id
                """,
                (account_id, customer_id),
            ).fetchone()
        return row is not None

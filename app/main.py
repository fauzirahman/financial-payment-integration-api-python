import hashlib
import hmac
import os
from collections import defaultdict
from datetime import date
from decimal import Decimal

from fastapi import FastAPI, Header, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.repositories.payment_repository import PostgresPaymentRepository
from app.schemas.payment import (
    PaymentCreate,
    PaymentResponse,
    WebhookEvent,
    WebhookResponse,
)

payment_repository = PostgresPaymentRepository()

app = FastAPI(
    title="Financial Payment Integration API",
    description="Demo REST API untuk payment, webhook, dan laporan payment.",
    version="1.0.0",
)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    del request
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"error": "invalid request", "details": jsonable_encoder(exc.errors())},
    )


@app.get("/", tags=["System"])
def root() -> dict[str, str]:
    return {
        "service": "financial-payment-integration-api-python",
        "message": "Welcome to Financial Payment Integration API",
        "status": "ok",
    }


@app.get("/health", tags=["System"])
def health_check() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "financial-payment-integration-api-python",
    }


@app.post(
    "/api/v1/payments",
    response_model=PaymentResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Payments"],
    summary="Create a payment",
)
def create_payment(
    payload: PaymentCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> PaymentResponse:
    key = idempotency_key.strip() if idempotency_key else None
    if key == "":
        raise HTTPException(status_code=400, detail="Idempotency-Key must not be blank")
    try:
        return payment_repository.create(
            payload.reference,
            payload.amount,
            payload.currency,
            payload.customer_id,
            payload.method,
            key,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get(
    "/api/v1/payments",
    response_model=list[PaymentResponse],
    tags=["Payments"],
    summary="List payments",
)
def list_payments() -> list[PaymentResponse]:
    return payment_repository.list()


@app.get(
    "/api/v1/payments/{payment_id}",
    response_model=PaymentResponse,
    tags=["Payments"],
    summary="Get payment by ID",
)
def get_payment(payment_id: str) -> PaymentResponse:
    if payment_id.isdigit() and int(payment_id) < 1:
        raise HTTPException(status_code=400, detail="invalid payment id")
    payment = payment_repository.get(payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="payment not found")
    return payment


@app.post(
    "/api/v1/webhooks/payment",
    response_model=WebhookResponse,
    tags=["Webhooks"],
    summary="Process a payment provider webhook",
)
async def process_payment_webhook(
    payload: WebhookEvent,
    request: Request,
    x_webhook_signature: str | None = Header(default=None, alias="X-Webhook-Signature"),
) -> WebhookResponse:
    secret = os.getenv("WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(status_code=503, detail="webhook is not configured")
    if not x_webhook_signature or not x_webhook_signature.startswith("sha256="):
        raise HTTPException(status_code=401, detail="invalid webhook signature")

    expected = hmac.new(
        secret.encode(), await request.body(), hashlib.sha256
    ).hexdigest()
    provided = x_webhook_signature.removeprefix("sha256=")
    if not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=401, detail="invalid webhook signature")

    new_status = "SUCCESS" if payload.type == "payment.success" else "FAILED"
    try:
        payment = payment_repository.apply_webhook(
            payload.event_id, payload.reference, new_status
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return WebhookResponse(
        event_id=payload.event_id,
        reference=payment.reference,
        status=new_status,
    )


def _filter_payments(
    from_date: date | None, to_date: date | None
) -> list[PaymentResponse]:
    if from_date and to_date and from_date > to_date:
        raise HTTPException(
            status_code=400, detail="'from' must be before or equal to 'to'"
        )
    return [
        payment
        for payment in payment_repository.list()
        if (from_date is None and to_date is None)
        or (
            payment.created_at is not None
            and (from_date is None or payment.created_at.date() >= from_date)
            and (to_date is None or payment.created_at.date() <= to_date)
        )
    ]


@app.get(
    "/api/v1/reports/payment-summary",
    tags=["Reports"],
    summary="Summarize payments by status and currency",
)
def payment_summary(
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
) -> dict[str, list[dict[str, int | Decimal | str]]]:
    totals: dict[tuple[str, str], dict[str, int | Decimal]] = defaultdict(
        lambda: {"count": 0, "amount": 0}
    )
    for payment in _filter_payments(from_date, to_date):
        bucket = totals[(payment.status, payment.currency)]
        bucket["count"] += 1
        bucket["amount"] += payment.amount
    return {
        "items": [
            {"status": key[0], "currency": key[1], **value}
            for key, value in sorted(totals.items())
        ]
    }


@app.get(
    "/api/v1/reports/daily-payments",
    tags=["Reports"],
    summary="Summarize payments by creation date and currency",
)
def daily_payments(
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
) -> dict[str, list[dict[str, int | Decimal | str]]]:
    totals: dict[tuple[str, str], dict[str, int | Decimal]] = defaultdict(
        lambda: {"count": 0, "amount": 0}
    )
    for payment in _filter_payments(from_date, to_date):
        if payment.created_at is None:
            continue
        bucket = totals[(payment.created_at.date().isoformat(), payment.currency)]
        bucket["count"] += 1
        bucket["amount"] += payment.amount
    return {
        "items": [
            {"date": key[0], "currency": key[1], **value}
            for key, value in sorted(totals.items())
        ]
    }

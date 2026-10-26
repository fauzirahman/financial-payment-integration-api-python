import hashlib
import hmac
import os
from collections import defaultdict
from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.repositories.account_repository import AccountRepository
from app.repositories.payment_repository import PostgresPaymentRepository
from app.schemas.accounts import (
    AuthResponse,
    CustomerCreate,
    CustomerResponse,
    CustomerUpdate,
    LoginRequest,
    MessageResponse,
    PaymentAccountCreate,
    PaymentAccountResponse,
    ProfileUpdate,
    ProvisionCustomerAccountRequest,
    RegisterRequest,
    UserResponse,
)
from app.schemas.payment import (
    PaymentCreate,
    PaymentResponse,
    WebhookEvent,
    WebhookResponse,
)

payment_repository = PostgresPaymentRepository()
account_repository = AccountRepository()
bearer_scheme = HTTPBearer(auto_error=False)

app = FastAPI(
    title="Financial Payment Integration API",
    description=(
        "REST API untuk autentikasi, customer, payment account, payment, "
        "webhook, dan laporan."
    ),
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


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> UserResponse:
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = account_repository.authenticate(credentials.credentials)
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return UserResponse.model_validate(user)


def require_admin(
    current_user: UserResponse = Depends(get_current_user),
) -> UserResponse:
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    return current_user


def require_customer_access(customer_id: UUID, current_user: UserResponse) -> None:
    if current_user.role != "admin" and current_user.customer_id != customer_id:
        raise HTTPException(status_code=403, detail="customer access denied")


@app.post(
    "/api/v1/auth/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Authentication"],
)
def register(payload: RegisterRequest) -> AuthResponse:
    try:
        user, token, expires_at = account_repository.register(
            payload.name, payload.email, payload.password, payload.phone
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return AuthResponse(
        access_token=token,
        expires_at=expires_at,
        user=UserResponse.model_validate(user),
    )


@app.post(
    "/api/v1/auth/login",
    response_model=AuthResponse,
    tags=["Authentication"],
)
def login(payload: LoginRequest) -> AuthResponse:
    result = account_repository.login(payload.email, payload.password)
    if result is None:
        raise HTTPException(status_code=401, detail="invalid email or password")
    user, token, expires_at = result
    return AuthResponse(
        access_token=token,
        expires_at=expires_at,
        user=UserResponse.model_validate(user),
    )


@app.post(
    "/api/v1/auth/logout",
    response_model=MessageResponse,
    tags=["Authentication"],
)
def logout(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    _: UserResponse = Depends(get_current_user),
) -> MessageResponse:
    if credentials is None:
        raise HTTPException(status_code=401, detail="authentication required")
    account_repository.logout(credentials.credentials)
    return MessageResponse(message="logged out")


@app.get(
    "/api/v1/auth/me",
    response_model=UserResponse,
    tags=["Authentication"],
)
def get_profile(
    current_user: UserResponse = Depends(get_current_user),
) -> UserResponse:
    return current_user


@app.patch(
    "/api/v1/auth/me",
    response_model=CustomerResponse,
    tags=["Authentication"],
)
def update_profile(
    payload: ProfileUpdate,
    current_user: UserResponse = Depends(get_current_user),
) -> CustomerResponse:
    if current_user.customer_id is None:
        raise HTTPException(status_code=400, detail="admin user has no customer profile")
    customer = account_repository.update_profile(
        current_user.customer_id, payload.model_dump(exclude_unset=True)
    )
    if customer is None:
        raise HTTPException(status_code=404, detail="customer not found")
    return CustomerResponse.model_validate(customer)


@app.get(
    "/api/v1/customers",
    response_model=list[CustomerResponse],
    tags=["Customers"],
)
def list_customers(
    _: UserResponse = Depends(require_admin),
) -> list[CustomerResponse]:
    return [
        CustomerResponse.model_validate(customer)
        for customer in account_repository.list_customers()
    ]


@app.post(
    "/api/v1/customers",
    response_model=CustomerResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Customers"],
)
def create_customer(
    payload: CustomerCreate,
    _: UserResponse = Depends(require_admin),
) -> CustomerResponse:
    try:
        customer = account_repository.create_customer(
            payload.name, payload.email, payload.phone
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return CustomerResponse.model_validate(customer)


@app.post(
    "/api/v1/customers/{customer_id}/user",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Customers"],
)
def provision_customer_user(
    customer_id: UUID,
    payload: ProvisionCustomerAccountRequest,
    _: UserResponse = Depends(require_admin),
) -> UserResponse:
    try:
        user = account_repository.provision_customer_user(
            customer_id, payload.password
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return UserResponse.model_validate(user)


@app.get(
    "/api/v1/customers/{customer_id}",
    response_model=CustomerResponse,
    tags=["Customers"],
)
def get_customer(
    customer_id: UUID,
    current_user: UserResponse = Depends(get_current_user),
) -> CustomerResponse:
    require_customer_access(customer_id, current_user)
    customer = account_repository.get_customer(customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="customer not found")
    return CustomerResponse.model_validate(customer)


@app.patch(
    "/api/v1/customers/{customer_id}",
    response_model=CustomerResponse,
    tags=["Customers"],
)
def update_customer(
    customer_id: UUID,
    payload: CustomerUpdate,
    _: UserResponse = Depends(require_admin),
) -> CustomerResponse:
    try:
        customer = account_repository.update_customer(
            customer_id, payload.model_dump(exclude_unset=True)
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if customer is None:
        raise HTTPException(status_code=404, detail="customer not found")
    return CustomerResponse.model_validate(customer)


@app.delete(
    "/api/v1/customers/{customer_id}",
    response_model=MessageResponse,
    tags=["Customers"],
)
def deactivate_customer(
    customer_id: UUID,
    _: UserResponse = Depends(require_admin),
) -> MessageResponse:
    if not account_repository.deactivate_customer(customer_id):
        raise HTTPException(status_code=404, detail="active customer not found")
    return MessageResponse(message="customer deactivated")


@app.get(
    "/api/v1/customers/{customer_id}/accounts",
    response_model=list[PaymentAccountResponse],
    tags=["Payment Accounts"],
)
def list_payment_accounts(
    customer_id: UUID,
    current_user: UserResponse = Depends(get_current_user),
) -> list[PaymentAccountResponse]:
    require_customer_access(customer_id, current_user)
    if account_repository.get_customer(customer_id) is None:
        raise HTTPException(status_code=404, detail="customer not found")
    return [
        PaymentAccountResponse.model_validate(account)
        for account in account_repository.list_payment_accounts(customer_id)
    ]


@app.post(
    "/api/v1/customers/{customer_id}/accounts",
    response_model=PaymentAccountResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Payment Accounts"],
)
def create_payment_account(
    customer_id: UUID,
    payload: PaymentAccountCreate,
    current_user: UserResponse = Depends(get_current_user),
) -> PaymentAccountResponse:
    require_customer_access(customer_id, current_user)
    if account_repository.get_customer(customer_id) is None:
        raise HTTPException(status_code=404, detail="customer not found")
    account = account_repository.create_payment_account(
        customer_id,
        payload.label,
        payload.provider,
        payload.account_type,
        payload.last4,
    )
    return PaymentAccountResponse.model_validate(account)


@app.delete(
    "/api/v1/customers/{customer_id}/accounts/{account_id}",
    response_model=MessageResponse,
    tags=["Payment Accounts"],
)
def deactivate_payment_account(
    customer_id: UUID,
    account_id: UUID,
    current_user: UserResponse = Depends(get_current_user),
) -> MessageResponse:
    require_customer_access(customer_id, current_user)
    if not account_repository.deactivate_payment_account(customer_id, account_id):
        raise HTTPException(status_code=404, detail="payment account not found")
    return MessageResponse(message="payment account deactivated")


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
    current_user: UserResponse = Depends(get_current_user),
) -> PaymentResponse:
    if current_user.role != "admin" and current_user.customer_id != payload.customer_id:
        raise HTTPException(status_code=403, detail="payment customer access denied")
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
def list_payments(
    current_user: UserResponse = Depends(get_current_user),
) -> list[PaymentResponse]:
    customer_id = current_user.customer_id if current_user.role == "customer" else None
    return payment_repository.list(customer_id)


@app.get(
    "/api/v1/payments/{payment_id}",
    response_model=PaymentResponse,
    tags=["Payments"],
    summary="Get payment by ID",
)
def get_payment(
    payment_id: str,
    current_user: UserResponse = Depends(get_current_user),
) -> PaymentResponse:
    if payment_id.isdigit() and int(payment_id) < 1:
        raise HTTPException(status_code=400, detail="invalid payment id")
    customer_id = current_user.customer_id if current_user.role == "customer" else None
    payment = payment_repository.get(payment_id, customer_id)
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
    _: UserResponse = Depends(require_admin),
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
    _: UserResponse = Depends(require_admin),
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

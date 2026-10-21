from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PaymentCreate(StrictRequest):
    reference: str = Field(min_length=1, max_length=40)
    amount: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    customer_id: UUID
    method: str = Field(min_length=1, max_length=30)

    @field_validator("reference")
    @classmethod
    def normalize_reference(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("reference must not be blank")
        return value

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        value = value.strip().upper()
        if len(value) != 3 or not value.isalpha() or not value.isascii():
            raise ValueError("currency must be a three-letter code")
        return value

    @field_validator("method")
    @classmethod
    def normalize_method(cls, value: str) -> str:
        value = value.strip().upper()
        if not value:
            raise ValueError("method must not be blank")
        return value


class PaymentResponse(BaseModel):
    id: int | str
    reference: str
    amount: int | Decimal
    currency: str
    status: str
    created_at: datetime | None
    updated_at: datetime | None
    customer_id: str | None = None
    method: str | None = None
    gateway: str | None = None
    gateway_transaction_id: str | None = None
    paid_at: datetime | None = None
    description: str | None = None


class WebhookEvent(StrictRequest):
    event_id: str = Field(min_length=1, max_length=200)
    type: Literal["payment.success", "payment.failed"]
    reference: str = Field(min_length=1, max_length=40)

    @field_validator("event_id", "reference")
    @classmethod
    def strip_non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value


class WebhookResponse(BaseModel):
    event_id: str
    reference: str
    status: Literal["SUCCESS", "FAILED"]

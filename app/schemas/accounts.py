from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.payment import StrictRequest


class RegisterRequest(StrictRequest):
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)
    phone: str | None = Field(default=None, max_length=40)

    @field_validator("name", "email", "phone")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value and value is not None:
            raise ValueError("value must not be blank")
        return value

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.lower()
        if value.count("@") != 1 or "." not in value.rsplit("@", 1)[1]:
            raise ValueError("email must be a valid email address")
        return value

    @field_validator("password")
    @classmethod
    def reject_blank_password(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("password must not be blank")
        return value


class LoginRequest(StrictRequest):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "email": "admin@example.com",
                    "password": "<INITIAL_ADMIN_PASSWORD>",
                }
            ]
        }
    )

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class CustomerCreate(StrictRequest):
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=254)
    phone: str | None = Field(default=None, max_length=40)

    @field_validator("name", "email", "phone")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.lower()
        if value.count("@") != 1 or "." not in value.rsplit("@", 1)[1]:
            raise ValueError("email must be a valid email address")
        return value


class CustomerUpdate(StrictRequest):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    email: str | None = Field(default=None, min_length=3, max_length=254)
    phone: str | None = Field(default=None, max_length=40)

    @field_validator("name", "email", "phone")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.lower()
        if value.count("@") != 1 or "." not in value.rsplit("@", 1)[1]:
            raise ValueError("email must be a valid email address")
        return value

    @field_validator("name", "email")
    @classmethod
    def reject_null_required_fields(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("field must not be null")
        return value


class ProvisionCustomerAccountRequest(StrictRequest):
    password: str = Field(min_length=12, max_length=128)

    @field_validator("password")
    @classmethod
    def reject_blank_password(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("password must not be blank")
        return value


class ProfileUpdate(StrictRequest):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=40)

    @field_validator("name", "phone")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value

    @field_validator("name")
    @classmethod
    def reject_null_name(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("name must not be null")
        return value


class PaymentAccountCreate(StrictRequest):
    label: str = Field(min_length=1, max_length=100)
    provider: str = Field(min_length=1, max_length=100)
    account_type: Literal["bank_account", "credit_card", "debit_card", "e_wallet"]
    last4: str | None = Field(default=None, min_length=4, max_length=4)

    @field_validator("label", "provider")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value

    @field_validator("last4")
    @classmethod
    def validate_last4(cls, value: str | None) -> str | None:
        if value is not None and not value.isdigit():
            raise ValueError("last4 must contain four digits")
        return value


class UserResponse(BaseModel):
    id: UUID
    email: str
    role: Literal["admin", "customer"]
    customer_id: UUID | None
    name: str | None = None
    phone: str | None = None
    created_at: datetime


class AuthResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    user: UserResponse


class CustomerResponse(BaseModel):
    id: UUID
    customer_number: str
    name: str
    email: str
    phone: str | None
    status: str
    created_at: datetime | None
    updated_at: datetime | None


class PaymentAccountResponse(BaseModel):
    id: UUID
    customer_id: UUID
    label: str
    provider: str
    account_type: str
    last4: str | None
    status: str
    created_at: datetime


class MessageResponse(BaseModel):
    message: str

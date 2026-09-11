"""Request and response shapes. Input validation happens here.

Pydantic checks every field before any code of ours runs. A bad body never reaches the
service. FastAPI answers 422 and app/api/errors.py formats it with the field names.
"""

from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, Field, StringConstraints

from app.domain.models import Invoice, InvoiceStatus, PaymentResult

Email = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        min_length=3,
        max_length=254,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    ),
]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
Cents = Annotated[int, Field(gt=0, le=1_000_000_000_000)]


class LoginIn(BaseModel):
    email: Email
    password: Annotated[str, Field(min_length=1, max_length=128)]


class TokenOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105  (a scheme name, not a secret)
    expires_in: int


class InvoiceIn(BaseModel):
    customer_id: int
    amount_cents: Cents
    currency: Currency
    due_at: AwareDatetime


class InvoiceOut(BaseModel):
    id: int
    user_id: int
    amount_cents: int
    balance_cents: int
    currency: str
    status: InvoiceStatus
    due_at: datetime

    @classmethod
    def from_domain(cls, invoice: Invoice) -> "InvoiceOut":
        return cls(**asdict(invoice))


class PaymentIn(BaseModel):
    amount_cents: Cents
    currency: Currency


class PaymentOut(BaseModel):
    invoice_id: int
    payment_id: int
    paid_cents: int
    balance_cents: int
    status: InvoiceStatus

    @classmethod
    def from_domain(cls, result: PaymentResult) -> "PaymentOut":
        return cls(**asdict(result))


class ErrorOut(BaseModel):
    """Every error response has this shape. 422 adds an `errors` list with field names."""

    code: str
    message: str
    request_id: str

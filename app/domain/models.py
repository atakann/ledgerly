"""Plain domain entities. No SQLAlchemy and no FastAPI imports anywhere under app/domain.

Money is an integer amount in minor units (cents) plus an ISO 4217 currency code. Integers
add up exactly; floats do not. Payment providers use the same convention, so nothing needs
converting at the edge.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class InvoiceStatus(StrEnum):
    OPEN = "open"
    PARTIAL = "partial"
    PAID = "paid"


class IdempotencyStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    DONE = "done"


@dataclass(frozen=True)
class User:
    id: int
    email: str
    password_hash: str
    roles: frozenset[str]


@dataclass(frozen=True)
class Principal:
    """Who is calling, as proven by the bearer token. Built once per request, not from the DB."""

    id: int
    roles: frozenset[str]

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles


@dataclass(frozen=True)
class Invoice:
    id: int
    user_id: int
    amount_cents: int
    balance_cents: int
    currency: str
    status: InvoiceStatus
    due_at: datetime


@dataclass(frozen=True)
class Payment:
    id: int
    invoice_id: int
    amount_cents: int
    currency: str


@dataclass(frozen=True)
class IdempotencyRecord:
    request_hash: str
    status: IdempotencyStatus
    response: dict | None


@dataclass(frozen=True)
class PaymentResult:
    """What the caller gets back. Also what the idempotency table stores as JSON."""

    invoice_id: int
    payment_id: int
    paid_cents: int
    balance_cents: int
    status: InvoiceStatus

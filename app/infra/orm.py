"""SQLAlchemy 2.0 table definitions. This is the persistence shape. The domain shape lives in
app/domain/models.py, and the repository converts between the two.

users             id, email UNIQUE, password_hash, roles, created_at
invoices          id, user_id FK, amount_cents, balance_cents, currency, status, due_at, created_at
payments          id, invoice_id FK, amount_cents, currency, created_at         (append-only)
idempotency_keys  (user_id, key) PK, request_hash, status, response_json, created_at
outbox            id, event_type, aggregate_id, payload_json, created_at, published_at

Money is an integer number of minor units (cents) plus a 3-letter currency code. Never a float.
The CHECK constraints are the last line of defence. The same rules live in
app/domain/payment_rules.py.
DateTime(timezone=True) becomes TIMESTAMPTZ on Postgres, so every timestamp is stored in UTC.
"""

from datetime import UTC, datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    roles: Mapped[str] = mapped_column(String(100), default="customer")  # comma separated
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InvoiceRow(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        CheckConstraint("amount_cents > 0", name="ck_invoices_amount_positive"),
        CheckConstraint("balance_cents >= 0", name="ck_invoices_balance_not_negative"),
        CheckConstraint("balance_cents <= amount_cents", name="ck_invoices_balance_le_amount"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    balance_cents: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(10))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PaymentRow(Base):
    __tablename__ = "payments"
    __table_args__ = (CheckConstraint("amount_cents > 0", name="ck_payments_amount_positive"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IdempotencyKeyRow(Base):
    __tablename__ = "idempotency_keys"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(11))  # in_progress | done
    response_json: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OutboxRow(Base):
    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_type: Mapped[str] = mapped_column(String(50))
    aggregate_id: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, index=True
    )

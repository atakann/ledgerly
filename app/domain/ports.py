"""The interfaces the service needs from storage. This is where the layers meet.

These are Protocols: structural interfaces. app/infra/repository.py implements them without
inheriting from them. The service imports only this file, so app/domain never imports
SQLAlchemy. A test could pass an in-memory implementation instead.
"""

from contextlib import AbstractContextManager
from datetime import datetime
from typing import Protocol

from app.domain.models import IdempotencyRecord, Invoice, InvoiceStatus, Payment, User


class UserStore(Protocol):
    def add(self, email: str, password_hash: str, roles: set[str]) -> User: ...
    def get_by_email(self, email: str) -> User | None: ...


class InvoiceStore(Protocol):
    def add(self, user_id: int, amount_cents: int, currency: str, due_at: datetime) -> Invoice: ...
    def get(self, invoice_id: int) -> Invoice | None: ...
    def get_for_update(self, invoice_id: int) -> Invoice | None: ...
    def save_balance(self, invoice: Invoice) -> None: ...
    def list_for_user(self, user_id: int, status: InvoiceStatus | None = None) -> list[Invoice]: ...


class PaymentStore(Protocol):
    def add(self, invoice_id: int, amount_cents: int, currency: str) -> Payment: ...
    def count_for_invoice(self, invoice_id: int) -> int: ...


class IdempotencyStore(Protocol):
    def reserve(self, user_id: int, key: str, request_hash: str) -> IdempotencyRecord | None: ...
    def complete(self, user_id: int, key: str, response: dict) -> None: ...
    def release(self, user_id: int, key: str) -> None: ...


class OutboxStore(Protocol):
    def add(self, event_type: str, aggregate_id: str, payload: dict) -> None: ...
    def count(self, event_type: str) -> int: ...


class UnitOfWork(Protocol):
    users: UserStore
    invoices: InvoiceStore
    payments: PaymentStore
    idempotency: IdempotencyStore
    outbox: OutboxStore

    def transaction(self) -> AbstractContextManager[object]: ...

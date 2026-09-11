"""Data access. Every SQL statement of the project is in this file.

One SqlUnitOfWork per request wraps one Session. It has one repository per table and one method,
transaction(), that opens an explicit transaction. The service composes its writes inside
`with uow.transaction():`. The block commits at the end, or rolls back on any exception.

Every query is built with SQLAlchemy and bound parameters. There is one hand-written SQL
statement on purpose, InvoiceRepository.list_for_user, to show text() with named parameters.
No query is ever built from user data with string formatting.

Repositories return domain objects (app/domain/models.py), never ORM rows, so the service
cannot depend on SQLAlchemy by accident.
"""

import json
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, SessionTransaction

from app.domain.errors import Conflict
from app.domain.models import (
    IdempotencyRecord,
    IdempotencyStatus,
    Invoice,
    InvoiceStatus,
    Payment,
    User,
)
from app.infra.orm import IdempotencyKeyRow, InvoiceRow, OutboxRow, PaymentRow, UserRow


class SqlUnitOfWork:
    """One session, explicit transactions, one repository per table.
    Implements app/domain/ports.py:UnitOfWork.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.invoices = InvoiceRepository(session)
        self.payments = PaymentRepository(session)
        self.idempotency = IdempotencyRepository(session)
        self.outbox = OutboxRepository(session)

    def transaction(self) -> SessionTransaction:
        """Use as `with uow.transaction():`. Commit on exit, rollback on exception."""
        return self.session.begin()


class UserRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, email: str, password_hash: str, roles: set[str]) -> User:
        row = UserRow(email=email, password_hash=password_hash, roles=",".join(sorted(roles)))
        self._session.add(row)
        self._session.flush()  # assigns row.id
        return _user(row)

    def get_by_email(self, email: str) -> User | None:
        row = self._session.scalar(select(UserRow).where(UserRow.email == email))
        return _user(row) if row is not None else None


class InvoiceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, user_id: int, amount_cents: int, currency: str, due_at: datetime) -> Invoice:
        row = InvoiceRow(
            user_id=user_id,
            amount_cents=amount_cents,
            balance_cents=amount_cents,
            currency=currency,
            status=InvoiceStatus.OPEN.value,
            due_at=due_at,
        )
        self._session.add(row)
        self._session.flush()
        return _invoice(row)

    def get(self, invoice_id: int) -> Invoice | None:
        return self._load(invoice_id, for_update=False)

    def get_for_update(self, invoice_id: int) -> Invoice | None:
        """Load the invoice and lock its row until the transaction ends: SELECT ... FOR UPDATE.

        A second payment on the same invoice waits here, then sees the balance the first
        payment left behind. This is the line that prevents overpaying under concurrency.
        """
        return self._load(invoice_id, for_update=True)

    def _load(self, invoice_id: int, *, for_update: bool) -> Invoice | None:
        stmt = select(InvoiceRow).where(InvoiceRow.id == invoice_id)
        if for_update:
            stmt = stmt.with_for_update()
        # populate_existing: always take the values from this query, never from a row that an
        # earlier transaction in the same session left in memory.
        row = self._session.scalar(stmt.execution_options(populate_existing=True))
        return _invoice(row) if row is not None else None

    def save_balance(self, invoice: Invoice) -> None:
        """Write back the two fields that a payment changes."""
        self._session.execute(
            update(InvoiceRow)
            .where(InvoiceRow.id == invoice.id)
            .values(balance_cents=invoice.balance_cents, status=invoice.status.value)
        )

    _LIST_SQL = text(
        "SELECT id, user_id, amount_cents, balance_cents, currency, status, due_at "
        "FROM invoices WHERE user_id = :user_id ORDER BY due_at, id"
    )
    _LIST_BY_STATUS_SQL = text(
        "SELECT id, user_id, amount_cents, balance_cents, currency, status, due_at "
        "FROM invoices WHERE user_id = :user_id AND status = :status ORDER BY due_at, id"
    )

    def list_for_user(self, user_id: int, status: InvoiceStatus | None = None) -> list[Invoice]:
        """The one hand-written query. Two fixed statements, values always as named parameters."""
        if status is None:
            result = self._session.execute(self._LIST_SQL, {"user_id": user_id})
        else:
            params = {"user_id": user_id, "status": status.value}
            result = self._session.execute(self._LIST_BY_STATUS_SQL, params)
        return [
            Invoice(
                id=r.id,
                user_id=r.user_id,
                amount_cents=r.amount_cents,
                balance_cents=r.balance_cents,
                currency=r.currency,
                status=InvoiceStatus(r.status),
                due_at=r.due_at,
            )
            for r in result
        ]


class PaymentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, invoice_id: int, amount_cents: int, currency: str) -> Payment:
        row = PaymentRow(invoice_id=invoice_id, amount_cents=amount_cents, currency=currency)
        self._session.add(row)
        self._session.flush()
        return Payment(
            id=row.id,
            invoice_id=row.invoice_id,
            amount_cents=row.amount_cents,
            currency=row.currency,
        )

    def count_for_invoice(self, invoice_id: int) -> int:
        stmt = (
            select(func.count()).select_from(PaymentRow).where(PaymentRow.invoice_id == invoice_id)
        )
        return self._session.scalar(stmt) or 0


class IdempotencyRepository:
    """The idempotency table. reserve() and release() open their own short transactions, so a
    concurrent duplicate request sees the reservation at once, before the payment work starts.
    complete() runs inside the payment transaction, so the stored response commits with the
    payment or not at all.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def reserve(self, user_id: int, key: str, request_hash: str) -> IdempotencyRecord | None:
        """Insert (user_id, key) as in_progress. Return None when this call won the key.
        When the key already exists, insert nothing and return the existing record.

        INSERT ... ON CONFLICT DO NOTHING is one round trip and needs no exception handling.
        If another transaction inserted the same key and has not committed yet, Postgres makes
        this statement wait for it, so a duplicate in flight is always seen.
        """
        # RETURNING gives one row when the insert happened and no row when it was skipped.
        table = IdempotencyKeyRow.__table__
        insert_stmt = (
            pg_insert(table)
            .values(
                user_id=user_id,
                key=key,
                request_hash=request_hash,
                status=IdempotencyStatus.IN_PROGRESS.value,
            )
            .on_conflict_do_nothing(index_elements=["user_id", "key"])
            .returning(table.c.key)
        )
        for _attempt in range(3):
            with self._session.begin():
                inserted = self._session.execute(insert_stmt).first() is not None
                if inserted:
                    return None
                row = self._session.get(IdempotencyKeyRow, (user_id, key), populate_existing=True)
            if row is not None:
                return _record(row)
            # The other request released the key between our INSERT and our SELECT. Retry.
        raise Conflict("Could not reserve the idempotency key.", code="request_in_flight")

    def complete(self, user_id: int, key: str, response: dict) -> None:
        self._session.execute(
            update(IdempotencyKeyRow)
            .where(IdempotencyKeyRow.user_id == user_id, IdempotencyKeyRow.key == key)
            .values(status=IdempotencyStatus.DONE.value, response_json=json.dumps(response))
        )

    def release(self, user_id: int, key: str) -> None:
        """Remove the reservation after a failure, so the client can retry with the same key."""
        with self._session.begin():
            self._session.execute(
                delete(IdempotencyKeyRow).where(
                    IdempotencyKeyRow.user_id == user_id, IdempotencyKeyRow.key == key
                )
            )


class OutboxRepository:
    """Events for other systems. Written in the same transaction as the business change, so an
    event exists if and only if the change committed. A relay process (not built here) reads
    unpublished rows, publishes them, and sets published_at.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, event_type: str, aggregate_id: str, payload: dict) -> None:
        self._session.add(
            OutboxRow(
                event_type=event_type, aggregate_id=aggregate_id, payload_json=json.dumps(payload)
            )
        )

    def count(self, event_type: str) -> int:
        stmt = select(func.count()).select_from(OutboxRow).where(OutboxRow.event_type == event_type)
        return self._session.scalar(stmt) or 0


# Row to domain converters. The only place that knows both shapes.


def _user(row: UserRow) -> User:
    roles = frozenset(r for r in row.roles.split(",") if r)
    return User(id=row.id, email=row.email, password_hash=row.password_hash, roles=roles)


def _invoice(row: InvoiceRow) -> Invoice:
    return Invoice(
        id=row.id,
        user_id=row.user_id,
        amount_cents=row.amount_cents,
        balance_cents=row.balance_cents,
        currency=row.currency,
        status=InvoiceStatus(row.status),
        due_at=row.due_at if row.due_at.tzinfo else row.due_at.replace(tzinfo=UTC),
    )


def _record(row: IdempotencyKeyRow) -> IdempotencyRecord:
    response = json.loads(row.response_json) if row.response_json else None
    return IdempotencyRecord(
        request_hash=row.request_hash, status=IdempotencyStatus(row.status), response=response
    )

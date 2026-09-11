"""Business rules. Start reading here.

InvoiceService.pay(...) is the main request, POST /invoices/{id}/payments:

  1. Fingerprint the request: sha256 of invoice id, amount and currency.
  2. Reserve the Idempotency-Key in its own short transaction (IdempotencyStore.reserve).
       key exists, other fingerprint   -> InvalidRequest (422): key reused for a different request
       key exists, still in_progress   -> Conflict (409): the same request is still running
       key exists, done                -> return the stored result and touch nothing
  3. In ONE transaction:
       load the invoice with a row lock         -> NotFound (404)
       assert_can_pay(principal, invoice)       -> Forbidden (403): not the owner, not admin
       apply_payment(invoice, amount, currency) -> Conflict (409) with a rule code
       insert the payment row
       write the new balance and status
       insert the outbox event
       mark the idempotency key done, with the response
     All of it commits, or none of it.
  4. If anything fails after the reservation, release the key so the client can retry.

The service depends on the UnitOfWork interface in app/domain/ports.py. It never imports
FastAPI or SQLAlchemy.
"""

import hashlib
import json
from dataclasses import asdict
from datetime import datetime

from app.domain.errors import Conflict, Forbidden, InvalidRequest, NotFound
from app.domain.models import (
    IdempotencyRecord,
    IdempotencyStatus,
    Invoice,
    InvoiceStatus,
    PaymentResult,
    Principal,
)
from app.domain.payment_rules import apply_payment
from app.domain.ports import UnitOfWork

PAYMENT_APPLIED = "payment.applied"


def assert_can_pay(principal: Principal, invoice: Invoice) -> None:
    """Authorization. The owner may pay their own invoice. An admin may pay any invoice."""
    if invoice.user_id != principal.id and not principal.is_admin:
        raise Forbidden("You may not pay this invoice.", code="not_invoice_owner")


def fingerprint(invoice_id: int, amount_cents: int, currency: str) -> str:
    """A stable hash of what the request asks for. The same key with a different fingerprint
    is a client bug, and the service refuses it instead of guessing."""
    canonical = json.dumps(
        {"invoice_id": invoice_id, "amount_cents": amount_cents, "currency": currency},
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class InvoiceService:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def create_invoice(
        self, principal: Principal, amount_cents: int, currency: str, due_at: datetime
    ) -> Invoice:
        with self._uow.transaction():
            return self._uow.invoices.add(principal.id, amount_cents, currency, due_at)

    def list_invoices(self, principal: Principal, status: InvoiceStatus | None) -> list[Invoice]:
        with self._uow.transaction():
            return self._uow.invoices.list_for_user(principal.id, status)

    def pay(
        self,
        principal: Principal,
        invoice_id: int,
        idempotency_key: str,
        amount_cents: int,
        currency: str,
    ) -> PaymentResult:
        request_hash = fingerprint(invoice_id, amount_cents, currency)

        # Step 2: reserve the key. Its own short transaction, so a duplicate sees it at once.
        existing = self._uow.idempotency.reserve(principal.id, idempotency_key, request_hash)
        if existing is not None:
            return self._replay(existing, request_hash)

        # Step 3: the payment, in one transaction. Step 4: on any failure, free the key.
        try:
            with self._uow.transaction():
                result = self._apply(principal, invoice_id, amount_cents, currency)
                self._uow.idempotency.complete(principal.id, idempotency_key, asdict(result))
            return result
        except BaseException:
            self._uow.idempotency.release(principal.id, idempotency_key)
            raise

    def _replay(self, existing: IdempotencyRecord, request_hash: str) -> PaymentResult:
        """The key was seen before. Decide between 422, 409 and the stored response."""
        if existing.request_hash != request_hash:
            raise InvalidRequest(
                "Idempotency-Key was already used for a different request.",
                code="idempotency_key_reused",
            )
        if existing.status is IdempotencyStatus.IN_PROGRESS or existing.response is None:
            raise Conflict("The same request is still being processed.", code="request_in_flight")
        stored = dict(existing.response)
        stored["status"] = InvoiceStatus(stored["status"])
        return PaymentResult(**stored)

    def _apply(
        self, principal: Principal, invoice_id: int, amount_cents: int, currency: str
    ) -> PaymentResult:
        """Runs inside the transaction: lock, authorize, apply the rules, write four rows."""
        invoice = self._uow.invoices.get_for_update(invoice_id)
        if invoice is None:
            raise NotFound("Invoice not found.", code="invoice_not_found")
        # A 403 here tells a stranger that the invoice exists. Returning 404 for both cases
        # would hide that. 403 is kept because it is clearer to read.
        assert_can_pay(principal, invoice)

        updated = apply_payment(invoice, amount_cents, currency)

        payment = self._uow.payments.add(invoice.id, amount_cents, currency)
        self._uow.invoices.save_balance(updated)
        self._uow.outbox.add(
            PAYMENT_APPLIED,
            aggregate_id=str(invoice.id),
            payload={
                "invoice_id": invoice.id,
                "payment_id": payment.id,
                "amount_cents": amount_cents,
                "currency": currency,
                "balance_cents": updated.balance_cents,
                "status": updated.status.value,
            },
        )
        return PaymentResult(
            invoice_id=invoice.id,
            payment_id=payment.id,
            paid_cents=amount_cents,
            balance_cents=updated.balance_cents,
            status=updated.status,
        )

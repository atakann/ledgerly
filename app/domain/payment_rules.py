"""The payment rules, as one pure function. No I/O, no clock, no database. Easy to test and
easy to read aloud. The service calls it inside the transaction, after loading the invoice.

Each rejection carries its own `code`, so a client can tell the cases apart without parsing
an English sentence.
"""

from dataclasses import replace

from app.domain.errors import Conflict
from app.domain.models import Invoice, InvoiceStatus


def apply_payment(invoice: Invoice, amount_cents: int, currency: str) -> Invoice:
    """Return the invoice as it looks after paying `amount_cents`, or raise Conflict."""
    if invoice.status is InvoiceStatus.PAID:
        raise Conflict("Invoice is already paid.", code="invoice_already_paid")
    if currency != invoice.currency:
        raise Conflict(
            f"Invoice is in {invoice.currency}, payment is in {currency}.",
            code="currency_mismatch",
        )
    if amount_cents <= 0:
        raise Conflict("Amount must be positive.", code="amount_not_positive")
    if amount_cents > invoice.balance_cents:
        raise Conflict("Amount exceeds the open balance.", code="amount_exceeds_balance")

    balance_cents = invoice.balance_cents - amount_cents
    status = InvoiceStatus.PAID if balance_cents == 0 else InvoiceStatus.PARTIAL
    return replace(invoice, balance_cents=balance_cents, status=status)

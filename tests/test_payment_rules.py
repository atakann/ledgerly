"""apply_payment is pure: one test per rule, no database, no app."""

from datetime import UTC, datetime

import pytest

from app.domain.errors import Conflict
from app.domain.models import Invoice, InvoiceStatus
from app.domain.payment_rules import apply_payment


def make_invoice(
    balance_cents: int = 10_000,
    status: InvoiceStatus = InvoiceStatus.OPEN,
    currency: str = "EUR",
) -> Invoice:
    return Invoice(
        id=1,
        user_id=1,
        amount_cents=10_000,
        balance_cents=balance_cents,
        currency=currency,
        status=status,
        due_at=datetime(2026, 10, 1, tzinfo=UTC),
    )


def test_partial_payment_leaves_invoice_partial():
    after = apply_payment(make_invoice(), 4_000, "EUR")
    assert after.balance_cents == 6_000
    assert after.status is InvoiceStatus.PARTIAL


def test_exact_payment_marks_invoice_paid():
    after = apply_payment(make_invoice(), 10_000, "EUR")
    assert after.balance_cents == 0
    assert after.status is InvoiceStatus.PAID


def test_paying_the_rest_of_a_partial_invoice_marks_it_paid():
    invoice = make_invoice(balance_cents=6_000, status=InvoiceStatus.PARTIAL)
    after = apply_payment(invoice, 6_000, "EUR")
    assert after.balance_cents == 0
    assert after.status is InvoiceStatus.PAID


def test_apply_payment_does_not_mutate_its_input():
    invoice = make_invoice()
    apply_payment(invoice, 4_000, "EUR")
    assert invoice.balance_cents == 10_000
    assert invoice.status is InvoiceStatus.OPEN


PAID_INVOICE = make_invoice(balance_cents=0, status=InvoiceStatus.PAID)


@pytest.mark.parametrize(
    ("invoice", "amount_cents", "currency", "code"),
    [
        (PAID_INVOICE, 1, "EUR", "invoice_already_paid"),
        (make_invoice(), 1_000, "USD", "currency_mismatch"),
        (make_invoice(), 0, "EUR", "amount_not_positive"),
        (make_invoice(), -5, "EUR", "amount_not_positive"),
        (make_invoice(), 10_001, "EUR", "amount_exceeds_balance"),
    ],
)
def test_rejections_carry_a_typed_code(invoice, amount_cents, currency, code):
    with pytest.raises(Conflict) as excinfo:
        apply_payment(invoice, amount_cents, currency)
    assert excinfo.value.code == code

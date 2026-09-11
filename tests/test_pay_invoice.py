"""The main request, POST /invoices/{id}/payments, one test per outcome.

Every test goes through HTTP, so the whole chain runs: route, token check, service, rules,
repository, Postgres. `uow` looks at the database afterwards.
"""

from uuid import uuid4

from app.domain.service import fingerprint


def pay(client, headers, invoice_id, amount_cents=4_000, currency="EUR", key=None):
    return client.post(
        f"/invoices/{invoice_id}/payments",
        json={"amount_cents": amount_cents, "currency": currency},
        headers={**headers, "Idempotency-Key": key or uuid4().hex},
    )


# Happy paths


def test_partial_payment_returns_201_and_the_new_balance(client, alice_headers, invoice):
    response = pay(client, alice_headers, invoice.id, 4_000)
    assert response.status_code == 201
    body = response.json()
    assert body["invoice_id"] == invoice.id
    assert body["paid_cents"] == 4_000
    assert body["balance_cents"] == 6_000
    assert body["status"] == "partial"
    assert isinstance(body["payment_id"], int)


def test_exact_payment_marks_the_invoice_paid(client, alice_headers, invoice):
    response = pay(client, alice_headers, invoice.id, 10_000)
    assert response.status_code == 201
    assert response.json()["balance_cents"] == 0
    assert response.json()["status"] == "paid"


def test_two_partial_payments_add_up(client, alice_headers, invoice):
    assert pay(client, alice_headers, invoice.id, 4_000).status_code == 201
    response = pay(client, alice_headers, invoice.id, 6_000)
    assert response.status_code == 201
    assert response.json()["status"] == "paid"


def test_admin_can_pay_another_users_invoice(client, admin_headers, invoice):
    assert pay(client, admin_headers, invoice.id, 1_000).status_code == 201


def test_payment_writes_an_outbox_event_in_the_same_transaction(
    client, alice_headers, invoice, uow
):
    pay(client, alice_headers, invoice.id, 4_000)
    with uow.transaction():
        assert uow.outbox.count("payment.applied") == 1
        assert uow.payments.count_for_invoice(invoice.id) == 1


# Authentication and authorization


def test_missing_token_is_401(client, invoice):
    response = pay(client, {}, invoice.id)
    assert response.status_code == 401
    assert response.json()["code"] == "missing_token"


def test_someone_elses_invoice_is_403(client, bob_headers, invoice):
    response = pay(client, bob_headers, invoice.id)
    assert response.status_code == 403
    assert response.json()["code"] == "not_invoice_owner"


def test_unknown_invoice_is_404(client, alice_headers):
    response = pay(client, alice_headers, 999_999)
    assert response.status_code == 404
    assert response.json()["code"] == "invoice_not_found"


# Business rules, each with its own code


def test_already_paid_invoice_is_409(client, alice_headers, invoice):
    pay(client, alice_headers, invoice.id, 10_000)
    response = pay(client, alice_headers, invoice.id, 1)
    assert response.status_code == 409
    assert response.json()["code"] == "invoice_already_paid"


def test_currency_mismatch_is_409(client, alice_headers, invoice):
    response = pay(client, alice_headers, invoice.id, 1_000, currency="USD")
    assert response.status_code == 409
    assert response.json()["code"] == "currency_mismatch"


def test_amount_over_balance_is_409(client, alice_headers, invoice):
    response = pay(client, alice_headers, invoice.id, 10_001)
    assert response.status_code == 409
    assert response.json()["code"] == "amount_exceeds_balance"


# Idempotency


def test_replay_returns_the_same_body_and_no_second_payment(client, alice_headers, invoice, uow):
    key = uuid4().hex
    first = pay(client, alice_headers, invoice.id, 4_000, key=key)
    second = pay(client, alice_headers, invoice.id, 4_000, key=key)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    with uow.transaction():
        assert uow.payments.count_for_invoice(invoice.id) == 1


def test_same_key_with_a_different_body_is_422(client, alice_headers, invoice):
    key = uuid4().hex
    pay(client, alice_headers, invoice.id, 4_000, key=key)
    response = pay(client, alice_headers, invoice.id, 5_000, key=key)
    assert response.status_code == 422
    assert response.json()["code"] == "idempotency_key_reused"


def test_duplicate_still_in_flight_is_409(client, alice_headers, invoice, alice, uow):
    key = uuid4().hex
    # Arrange the state a concurrent request would leave: the key is reserved, not done.
    uow.idempotency.reserve(alice.id, key, fingerprint(invoice.id, 4_000, "EUR"))
    response = pay(client, alice_headers, invoice.id, 4_000, key=key)
    assert response.status_code == 409
    assert response.json()["code"] == "request_in_flight"


def test_rejected_payment_frees_the_key_for_a_retry(client, alice_headers, invoice):
    key = uuid4().hex
    first = pay(client, alice_headers, invoice.id, 10_001, key=key)
    second = pay(client, alice_headers, invoice.id, 10_001, key=key)
    assert first.json()["code"] == "amount_exceeds_balance"
    assert second.json()["code"] == "amount_exceeds_balance"  # not request_in_flight


# Input validation


def test_invalid_body_is_422_and_names_the_field_only(client, alice_headers, invoice):
    response = pay(client, alice_headers, invoice.id, amount_cents=0)
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert body["errors"][0]["field"] == "body.amount_cents"
    assert "input" not in body["errors"][0]


def test_missing_idempotency_key_header_is_422(client, alice_headers, invoice):
    response = client.post(
        f"/invoices/{invoice.id}/payments",
        json={"amount_cents": 1_000, "currency": "EUR"},
        headers=alice_headers,
    )
    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "header.Idempotency-Key"

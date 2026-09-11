"""Issuing and viewing invoices: who may do what."""

DUE = "2026-10-15T00:00:00Z"


def create(client, headers, customer_id, amount_cents=10_000):
    body = {
        "customer_id": customer_id,
        "amount_cents": amount_cents,
        "currency": "EUR",
        "due_at": DUE,
    }
    return client.post("/invoices", json=body, headers=headers)


def test_admin_issues_an_invoice_to_a_customer(client, admin_headers, alice):
    response = create(client, admin_headers, alice.id)
    assert response.status_code == 201
    body = response.json()
    assert body["user_id"] == alice.id
    assert body["balance_cents"] == 10_000
    assert body["status"] == "open"


def test_customer_cannot_issue_an_invoice(client, alice_headers, alice):
    response = create(client, alice_headers, alice.id)
    assert response.status_code == 403
    assert response.json()["code"] == "admin_required"


def test_unknown_customer_is_404(client, admin_headers):
    response = create(client, admin_headers, 999_999)
    assert response.status_code == 404
    assert response.json()["code"] == "customer_not_found"


def test_customer_sees_only_their_own_invoices(client, alice_headers, bob_headers, invoice):
    mine = client.get("/invoices", headers=alice_headers).json()
    theirs = client.get("/invoices", headers=bob_headers).json()
    assert [i["id"] for i in mine] == [invoice.id]
    assert theirs == []


def test_status_filter_uses_the_hand_written_query(client, alice_headers, invoice):
    assert len(client.get("/invoices?status=open", headers=alice_headers).json()) == 1
    assert client.get("/invoices?status=paid", headers=alice_headers).json() == []


def test_admin_can_list_a_customers_invoices(client, admin_headers, alice, invoice):
    response = client.get(f"/invoices?user_id={alice.id}", headers=admin_headers)
    assert response.status_code == 200
    assert [i["id"] for i in response.json()] == [invoice.id]


def test_customer_cannot_list_another_customers_invoices(client, bob_headers, alice):
    response = client.get(f"/invoices?user_id={alice.id}", headers=bob_headers)
    assert response.status_code == 403
    assert response.json()["code"] == "not_invoice_owner"

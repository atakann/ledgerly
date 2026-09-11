"""Login and the bearer check."""

from app.infra.security import LocalHS256Verifier
from tests.conftest import PASSWORD


def login(client, email, password):
    return client.post("/auth/login", json={"email": email, "password": password})


def test_login_returns_a_bearer_token(client, alice):
    response = login(client, alice.email, PASSWORD)
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 3600
    assert body["access_token"].count(".") == 2


def test_wrong_password_is_401(client, alice):
    response = login(client, alice.email, "not the password")
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


def test_unknown_email_gets_the_same_401(client, alice):
    response = login(client, "nobody@example.com", PASSWORD)
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


def test_token_from_login_opens_a_protected_route(client, alice):
    token = login(client, alice.email, PASSWORD).json()["access_token"]
    response = client.get("/invoices", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json() == []


def test_missing_token_is_401(client):
    response = client.get("/invoices")
    assert response.status_code == 401
    assert response.json()["code"] == "missing_token"
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_garbage_token_is_401(client):
    response = client.get("/invoices", headers={"Authorization": "Bearer not.a.token"})
    assert response.status_code == 401
    assert response.json()["code"] == "token_invalid"


def test_expired_token_is_401(client, settings, alice):
    expired = LocalHS256Verifier(settings.jwt_secret.get_secret_value(), ttl_seconds=-1)
    token = expired.issue(alice)
    response = client.get("/invoices", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["code"] == "token_expired"


def test_token_signed_with_another_secret_is_401(client, alice):
    forged = LocalHS256Verifier("another-secret-" + "y" * 32, ttl_seconds=60).issue(alice)
    response = client.get("/invoices", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401
    assert response.json()["code"] == "token_invalid"

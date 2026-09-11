"""Fixtures. Tests run against the real Postgres from compose.yaml, in the ledgerly_test
database, with empty tables before every test. Start the database first:

    docker compose up -d db
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import make_url

from app.domain.models import Invoice, User
from app.infra.orm import Base
from app.infra.repository import SqlUnitOfWork
from app.infra.security import hash_password
from app.infra.settings import Settings
from app.main import create_app

PASSWORD = "correct horse battery staple"
PASSWORD_HASH = hash_password(PASSWORD)  # hashed once; argon2 is slow on purpose


@pytest.fixture(scope="session")
def settings() -> Settings:
    """Same Postgres credentials as .env, but the ledgerly_test database and a test secret."""
    dev = Settings()
    test_url = make_url(dev.database_url.get_secret_value()).set(database="ledgerly_test")
    return Settings(
        database_url=test_url.render_as_string(hide_password=False),
        jwt_secret="test-only-secret-" + "x" * 32,
        log_level="WARNING",
    )


@pytest.fixture
def app(settings):
    app = create_app(settings)
    Base.metadata.drop_all(app.state.engine)
    Base.metadata.create_all(app.state.engine)
    yield app
    app.state.engine.dispose()


@pytest.fixture
def client(app):
    # raise_server_exceptions=False: a 500 comes back as a response, like in production.
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def uow(app):
    """Direct database access for arranging data and for checking what a request wrote."""
    session = app.state.session_factory()
    yield SqlUnitOfWork(session)
    session.close()


def make_user(uow: SqlUnitOfWork, email: str, roles: set[str]) -> User:
    with uow.transaction():
        return uow.users.add(email, PASSWORD_HASH, roles)


def bearer(app, user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {app.state.auth.issue(user)}"}


@pytest.fixture
def alice(uow) -> User:
    return make_user(uow, "alice@example.com", {"customer"})


@pytest.fixture
def bob(uow) -> User:
    return make_user(uow, "bob@example.com", {"customer"})


@pytest.fixture
def admin(uow) -> User:
    return make_user(uow, "admin@example.com", {"admin"})


@pytest.fixture
def alice_headers(app, alice):
    return bearer(app, alice)


@pytest.fixture
def bob_headers(app, bob):
    return bearer(app, bob)


@pytest.fixture
def admin_headers(app, admin):
    return bearer(app, admin)


@pytest.fixture
def invoice(uow, alice) -> Invoice:
    """An open invoice of 100.00 EUR owned by alice."""
    with uow.transaction():
        return uow.invoices.add(alice.id, 10_000, "EUR", datetime.now(UTC) + timedelta(days=30))

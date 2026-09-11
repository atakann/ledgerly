"""Create a demo user and one open invoice so /docs has something to work with.

uv run python scripts/seed.py            -> alice@example.com / demo-password, admin@example.com
PYTHONPATH=. is not needed; the script adds the project root itself.
"""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.infra.db import make_engine, make_session_factory  # noqa: E402
from app.infra.orm import Base  # noqa: E402
from app.infra.repository import SqlUnitOfWork  # noqa: E402
from app.infra.security import hash_password  # noqa: E402
from app.infra.settings import Settings  # noqa: E402

DEMO_PASSWORD = "demo-password"


def main() -> None:
    engine = make_engine(Settings().database_url.get_secret_value())
    Base.metadata.create_all(engine)
    uow = SqlUnitOfWork(make_session_factory(engine)())
    with uow.transaction():
        alice = uow.users.get_by_email("alice@example.com")
        if alice is not None:
            print(f"already seeded. alice@example.com / {DEMO_PASSWORD}. invoices:")
            for inv in uow.invoices.list_for_user(alice.id):
                print(f"  invoice {inv.id}: balance {inv.balance_cents}, {inv.status}")
            return
        alice = uow.users.add("alice@example.com", hash_password(DEMO_PASSWORD), {"customer"})
        uow.users.add("admin@example.com", hash_password(DEMO_PASSWORD), {"admin"})
        due = datetime.now(UTC) + timedelta(days=30)
        invoice = uow.invoices.add(alice.id, 10_000, "EUR", due)
    print(f"users: alice@example.com, admin@example.com (password {DEMO_PASSWORD})")
    print(f"invoice {invoice.id}: 100.00 EUR, open, owned by alice")


if __name__ == "__main__":
    main()

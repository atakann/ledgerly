"""Engine and session factory. The transaction rules of the project live here.

Postgres through psycopg 3. The engine owns a connection pool. Each request gets one Session
from the factory, and every unit of work is an explicit `with uow.transaction():` block in
app/infra/repository.py.

  - autobegin=False: a query outside an explicit transaction raises. This keeps the
    transaction boundaries visible in the service code.
  - The row lock for a payment is SELECT ... FOR UPDATE on the invoice row, in
    InvoiceRepository.get_for_update. Two payments on the same invoice queue up there, and
    the second one sees the balance the first one left behind.
  - pool_pre_ping: a stale pooled connection is replaced instead of failing the request.
"""

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def make_engine(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    # expire_on_commit=False: objects stay readable after the transaction ends, so the
    # repository can convert them to domain models outside the lock.
    return sessionmaker(bind=engine, autobegin=False, expire_on_commit=False)

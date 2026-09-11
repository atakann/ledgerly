"""HTTP layer. The request comes IN here and the response goes OUT here.

  POST /auth/login                public   -> 200 TokenOut
  POST /invoices                  bearer   -> 201 InvoiceOut
  GET  /invoices?status=open      bearer   -> 200 list of InvoiceOut
  POST /invoices/{id}/payments    bearer   -> 201 PaymentOut        (the main request)

The router knows HTTP: paths, status codes, headers, schemas. It holds no business rule and
no SQL. Rules live in app/domain/service.py, SQL in app/infra/repository.py.
"""

from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy.orm import Session

from app.api.schemas import (
    ErrorOut,
    InvoiceIn,
    InvoiceOut,
    LoginIn,
    PaymentIn,
    PaymentOut,
    TokenOut,
)
from app.domain.errors import Unauthorized
from app.domain.models import InvoiceStatus, Principal
from app.domain.service import InvoiceService
from app.infra.repository import SqlUnitOfWork
from app.infra.security import LocalHS256Verifier, current_user, verify_password

# Dependencies: how a route gets a database session, the service, and the caller.


def get_session(request: Request) -> Iterator[Session]:
    """One session per request, closed when the response is sent."""
    with request.app.state.session_factory() as session:
        yield session


def get_service(session: Annotated[Session, Depends(get_session)]) -> InvoiceService:
    return InvoiceService(SqlUnitOfWork(session))


DbSession = Annotated[Session, Depends(get_session)]
Service = Annotated[InvoiceService, Depends(get_service)]
Caller = Annotated[Principal, Depends(current_user)]  # 401 if the token is missing or bad

# Error bodies per status, so /docs shows them. The shape is ErrorOut for all of them.
UNAUTHORIZED = {401: {"model": ErrorOut}}
PROTECTED = {401: {"model": ErrorOut}, 422: {"model": ErrorOut}}
PAYMENT_ERRORS = {
    **PROTECTED,
    403: {"model": ErrorOut},
    404: {"model": ErrorOut},
    409: {"model": ErrorOut},
}

router = APIRouter()


@router.post("/auth/login", response_model=TokenOut, responses=UNAUTHORIZED)
def login(body: LoginIn, request: Request, session: DbSession) -> TokenOut:
    """Email and password in, token out. Unknown email and wrong password get the same 401."""
    auth: LocalHS256Verifier = request.app.state.auth
    uow = SqlUnitOfWork(session)
    with uow.transaction():
        user = uow.users.get_by_email(body.email)
    if user is None or not verify_password(body.password, user.password_hash):
        raise Unauthorized("Invalid email or password.", code="invalid_credentials")
    return TokenOut(access_token=auth.issue(user), expires_in=auth.ttl_seconds)


@router.post("/invoices", status_code=201, response_model=InvoiceOut, responses=PROTECTED)
def create_invoice(body: InvoiceIn, caller: Caller, service: Service) -> InvoiceOut:
    invoice = service.create_invoice(caller, body.amount_cents, body.currency, body.due_at)
    return InvoiceOut.from_domain(invoice)


@router.get("/invoices", response_model=list[InvoiceOut], responses=PROTECTED)
def list_invoices(
    caller: Caller,
    service: Service,
    status: Annotated[InvoiceStatus | None, Query()] = None,
) -> list[InvoiceOut]:
    return [InvoiceOut.from_domain(i) for i in service.list_invoices(caller, status)]


@router.post(
    "/invoices/{invoice_id}/payments",
    status_code=201,
    response_model=PaymentOut,
    responses=PAYMENT_ERRORS,
)
def pay_invoice(
    invoice_id: int,
    body: PaymentIn,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
    caller: Caller,
    service: Service,
) -> PaymentOut:
    """The main request. It comes in here, and the response goes out at the return."""
    result = service.pay(
        principal=caller,
        invoice_id=invoice_id,
        idempotency_key=idempotency_key,
        amount_cents=body.amount_cents,
        currency=body.currency,
    )
    return PaymentOut.from_domain(result)

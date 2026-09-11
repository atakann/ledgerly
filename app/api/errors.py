"""Turns errors into HTTP responses. Error handling and error logging live here.

  DomainError (app/domain/errors.py)  401 / 403 / 404 / 409 / 422, body {code, message, request_id}
  RequestValidationError (Pydantic)   422, body lists field names and reasons, never the values
  anything else                       500, body {code: "internal_error", request_id}, nothing else

The stack trace of a 500 goes to the log under the same request_id. The client never sees it.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.domain.errors import (
    Conflict,
    DomainError,
    Forbidden,
    InvalidRequest,
    NotFound,
    Unauthorized,
)

logger = logging.getLogger("ledgerly")

STATUS_BY_ERROR: dict[type[DomainError], int] = {
    Unauthorized: 401,
    Forbidden: 403,
    NotFound: 404,
    Conflict: 409,
    InvalidRequest: 422,
}


def request_id_of(request: Request) -> str:
    return getattr(request.state, "request_id", "-")


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError) -> JSONResponse:
        status = STATUS_BY_ERROR.get(type(exc), 400)
        headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
        body = {"code": exc.code, "message": exc.message, "request_id": request_id_of(request)}
        return JSONResponse(status_code=status, content=body, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Pydantic's error list includes the submitted value under "input". We keep only the
        # field path and the reason, so a password or an email is never echoed back or logged.
        fields = [
            {"field": ".".join(str(part) for part in err["loc"]), "reason": err["msg"]}
            for err in exc.errors()
        ]
        body = {
            "code": "validation_error",
            "message": "Request is not valid.",
            "errors": fields,
            "request_id": request_id_of(request),
        }
        return JSONResponse(status_code=422, content=body)

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        # The one logger.exception in the project. The redaction filter in
        # app/infra/logging.py scrubs the record before it is written.
        logger.exception(
            "unhandled error",
            extra={"request_id": request_id_of(request), "path": request.url.path},
        )
        body = {"code": "internal_error", "request_id": request_id_of(request)}
        return JSONResponse(status_code=500, content=body)

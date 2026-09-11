"""Typed errors the domain raises. The API layer maps them to HTTP status codes in
app/api/errors.py. Nothing under app/domain knows what an HTTP status is.

`code` is a stable machine-readable string that goes to the client. `message` is one short
sentence for a human. Neither carries user data, so both are safe to log and to return.
"""


class DomainError(Exception):
    code: str = "domain_error"

    def __init__(self, message: str | None = None, *, code: str | None = None) -> None:
        if code is not None:
            self.code = code
        self.message = message or self.code.replace("_", " ").capitalize() + "."
        super().__init__(self.message)


class Unauthorized(DomainError):
    """The caller is not identified: no token, bad token, wrong password."""

    code = "unauthorized"


class Forbidden(DomainError):
    """The caller is identified but may not touch this resource."""

    code = "forbidden"


class NotFound(DomainError):
    code = "not_found"


class Conflict(DomainError):
    """The request is well formed but the current state does not allow it."""

    code = "conflict"


class InvalidRequest(DomainError):
    """The request contradicts itself, for example a reused idempotency key with a new body."""

    code = "invalid_request"

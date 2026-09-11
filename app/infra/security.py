"""Password hashing and bearer tokens.

  hash_password(plain)                   the hashing call, argon2id
  verify_password(plain, password_hash)  the compare, constant time inside argon2
  LocalHS256Verifier                     issues and verifies HS256 JWTs signed with JWT_SECRET
  current_user                           FastAPI dependency: Bearer in, Principal out, else 401

The token carries `sub` (user id), `roles`, `iat` and `exp`. It carries no email and no name,
so a leaked token or a logged token exposes no personal data.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.domain.errors import Unauthorized
from app.domain.models import Principal, User

# argon2id with the library defaults: 64 MiB memory, 3 passes, 16-byte random salt per hash.
_hasher = PasswordHasher()


def hash_password(plain: str) -> str:
    """The hashing call. The result string carries algorithm, parameters, salt and hash."""
    return _hasher.hash(plain)


def verify_password(plain: str, password_hash: str) -> bool:
    """The compare. argon2 re-derives the hash with the stored salt, then compares."""
    try:
        return _hasher.verify(password_hash, plain)
    except (VerificationError, InvalidHashError):
        return False


class LocalHS256Verifier:
    """Issues and verifies tokens signed with our own secret. Fine for one service."""

    def __init__(self, secret: str, ttl_seconds: int) -> None:
        self._secret = secret
        self.ttl_seconds = ttl_seconds

    def issue(self, user: User) -> str:
        now = datetime.now(UTC)
        claims = {
            "sub": str(user.id),
            "roles": sorted(user.roles),
            "iat": now,
            "exp": now + timedelta(seconds=self.ttl_seconds),
        }
        return jwt.encode(claims, self._secret, algorithm="HS256")

    def verify(self, token: str) -> Principal:
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                options={"require": ["sub", "exp", "iat"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise Unauthorized("Token has expired.", code="token_expired") from exc
        except jwt.InvalidTokenError as exc:
            raise Unauthorized("Token is invalid.", code="token_invalid") from exc
        return Principal(id=int(claims["sub"]), roles=frozenset(claims.get("roles", [])))


# auto_error=False: a missing header gives None, and we raise our own 401 with a code.
_bearer = HTTPBearer(auto_error=False)


def current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    """FastAPI dependency. Every protected route lists it. Missing or bad token gives 401."""
    if credentials is None:
        raise Unauthorized("Missing bearer token.", code="missing_token")
    verifier: LocalHS256Verifier = request.app.state.auth
    return verifier.verify(credentials.credentials)

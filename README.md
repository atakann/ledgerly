# ledgerly

A small invoice and payment service, built to walk **one request from start to finish**.
A request comes in, the code checks the caller, checks the input, applies the payment rules,
writes Postgres in one transaction, and sends a response.

- **Request comes in:** `app/api/routes.py`, function `pay_invoice` (line 96).
- **Response goes out:** the same function, the `return` on line 111. Errors go out through `app/api/errors.py`.

Stack: Python 3.14, FastAPI, Pydantic v2, SQLAlchemy 2.0, Postgres 17 in Docker, argon2, PyJWT, pytest, ruff.

## Run

Needs Docker and [uv](https://docs.astral.sh/uv/). Nothing else.

```bash
cp .env.example .env            # then set JWT_SECRET to a long random string
docker compose up -d db         # Postgres 17 on localhost:5433, plus a ledgerly_test database
uv sync
uv run pytest -q                # 36 tests, run against ledgerly_test
uv run python scripts/seed.py   # alice@example.com / demo-password, and one open invoice
uv run uvicorn app.main:create_app --factory --reload
```

Open http://localhost:8000/docs. Log in as alice, click Authorize, paste the token.
Call `POST /invoices/{id}/payments` with an `Idempotency-Key` header and the body
`{"amount_cents": 4000, "currency": "EUR"}`. Call it twice. The second call returns the
first answer and writes nothing.

To run everything in containers instead: `docker compose up --build`. The API is on port 8000.

## The request to follow: `POST /invoices/{id}/payments`

1. The bearer token becomes a `Principal` (user id and roles), or 401. `app/infra/security.py:77`
2. The `Idempotency-Key` is reserved in its own short transaction. Seen before with a different
   body: 422. Seen before and still running: 409. Seen before and done: the stored response goes
   back and nothing else runs. `app/domain/service.py:86`, `app/infra/repository.py:174`
3. One transaction opens. `app/domain/service.py:92`
4. The invoice is loaded with `SELECT ... FOR UPDATE`, or 404. The caller must be the owner or an
   admin, or 403. `app/infra/repository.py:87`, `app/domain/service.py:45`
5. The rules run as a pure function: not already paid, same currency, positive amount, within the
   balance. Each rejection is a 409 with its own `code`. `app/domain/payment_rules.py:14`
6. Four writes: the payment row, the new balance and status, the outbox event, the stored response
   for the key. Commit. 201 with `invoice_id`, `payment_id`, `paid_cents`, `balance_cents`, `status`.
7. Any failure after step 2 releases the key, so the client can retry. `app/domain/service.py:96`
8. Anything unexpected: 500 with `{"code": "internal_error", "request_id": "..."}`. The traceback
   goes to the log under the same id, with secrets scrubbed. `app/api/errors.py:65`

## Where each thing lives

| Topic | Where |
|---|---|
| Error handling | Typed errors in `app/domain/errors.py`. Mapping to 401/403/404/409/422 at `app/api/errors.py:27`. The 500 handler at `app/api/errors.py:65`. The rule rejections at `app/domain/payment_rules.py:14`. |
| Error logging | The one `logger.exception` in the project, `app/api/errors.py:68`. Redaction patterns `app/infra/logging.py:26`, the filter `:46`, the JSON line format `:61`. Test: `tests/test_logging.py`. |
| Clean code | Three layers: `app/api` (HTTP only), `app/domain` (rules, no HTTP, no SQL), `app/infra` (Postgres, passwords, tokens). `app/domain/service.py` reads top to bottom as the request. The list below says what I would clean up. |
| Database credentials | `app/infra/settings.py:21`. `DATABASE_URL` and `JWT_SECRET` come from the environment or `.env`. No default, typed `SecretStr`, `.env` is gitignored, `.env.example` has placeholders only. Compose reads the Postgres password from `.env` too. |
| Login and permissions | The hashing call `app/infra/security.py:28`, the compare `:36`, called from `app/api/routes.py:70`. Who the user is on each request: token claims become a `Principal` at `app/infra/security.py:60`, no database read. What they may do: `assert_can_pay` at `app/domain/service.py:45`, owner or admin. |
| Object oriented design | Domain models `app/domain/models.py`. Storage interface `app/domain/ports.py:44`, implemented by `app/infra/repository.py:34`. Tokens are one class, `LocalHS256Verifier`, with `issue` and `verify`. Row to domain conversion happens in one place, the bottom of `repository.py`. |
| Authentication and authorization | Route table at the top of `app/api/routes.py`. `/auth/login` is public. Every other route lists `Caller` (`app/api/routes.py:48`), the `current_user` dependency. Authorization lives in the service, not in the router. |
| Database access | ORM with bound parameters everywhere. One hand-written `text()` query with named parameters at `app/infra/repository.py:112`. Row lock at `:98`. Idempotency insert as `INSERT ... ON CONFLICT DO NOTHING RETURNING` at `:192`. Explicit transactions only (`autobegin=False`, `app/infra/db.py`). CHECK constraints at `app/infra/orm.py:44`. |
| General good practice | 36 tests, one per outcome, in `tests/`. Pydantic validation `app/api/schemas.py:26`. `uv.lock` plus Dependabot for uv, Docker and Actions. Ruff. CI in `.github/workflows/ci.yml` with a Postgres service. Dockerfile and Compose. Deploy notes below. |

## What I would change with more time

In the order I would do them.

- Migrations. Tables come from `create_all` at startup (`app/main.py:26`). Production needs Alembic.
- Refresh tokens. One access token for one hour is the whole session.
- A rate limit on `/auth/login`. Also, an unknown email answers faster than a wrong password, because argon2 does not run. Comparing against a dummy hash would close that.
- A TTL on idempotency keys. A nightly delete of rows older than 24 hours, or a TTL attribute on DynamoDB.
- Stored error responses. A rejected payment frees the key and the retry recomputes the same answer. That is safe because a rejection changes nothing, but a client that expects the first answer byte for byte gets a new `request_id`.
- An outbox relay. Rows land in `outbox`; nothing publishes them yet. A worker would read unpublished rows, publish to SQS or EventBridge, and set `published_at`. Consumers must be idempotent on the outbox id.
- 404 instead of 403 on someone else's invoice. Today a stranger learns that the invoice exists.
- A roles table. `users.roles` is a comma separated string.
- Refunds, multi-currency invoices, partial refunds. None exist.
- An `AuthService`. Login logic sits in the route (`app/api/routes.py:64`); it should move into the service layer like the invoice logic.
- An identity provider (Auth0, Cognito, Keycloak) instead of our own user table and tokens. The change is one class: a verifier that checks the provider's RS256 tokens against its public keys, in place of `LocalHS256Verifier.verify`. `/auth/login` then disappears.

## Deploy

The image comes from `Dockerfile`: `python:3.14-slim`, uv, no dev dependencies. Locally,
`docker compose up --build` runs Postgres and the API together. In a real pipeline, GitLab CI
runs ruff and pytest against a Postgres service, builds the image, pushes it to a registry, and
Terraform rolls it out (ECS, or Lambda behind API Gateway) with `DATABASE_URL` and `JWT_SECRET`
injected from a secrets store. The idempotency table maps well to DynamoDB with a TTL attribute;
the rest stays in RDS Postgres. Logs are JSON lines on stderr, shipped to CloudWatch and searchable
by `request_id`. Dependabot opens weekly update PRs, and CI must pass before merge.


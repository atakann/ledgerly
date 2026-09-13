# ledgerly

A small invoice and payment service that handles **one request from start to finish**: a request
comes in, the caller and the input are checked, the payment rules run, Postgres is written in one
transaction, and a response goes back.

- **Request comes in:** `app/api/routes.py`, function `pay_invoice` (line 103).
- **Response goes out:** the same function, the `return` on line 118. Error responses come from `app/api/errors.py`.

Python 3.14, FastAPI, Pydantic v2, SQLAlchemy 2.0, Postgres 17 in Docker, argon2, PyJWT, pytest, ruff.

## Run

Needs Docker and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env            # then set JWT_SECRET to a long random string
docker compose up -d db         # Postgres 17 on localhost:5433, plus a ledgerly_test database
uv sync
uv run pytest -q                # 43 tests, against ledgerly_test
uv run python scripts/seed.py   # alice@example.com and admin@example.com, password demo-password
uv run uvicorn app.main:create_app --factory --reload
```

Then open http://localhost:8000/docs, log in as alice, click Authorize, and call
`POST /invoices/{id}/payments` with an `Idempotency-Key` header and `{"amount_cents": 4000, "currency": "EUR"}`.
Call it twice: the second call returns the first answer and writes nothing.
`docker compose up --build` runs the API in a container instead, on port 8000.

Roles: an admin issues invoices and may view or pay any of them. A customer views and pays their own.

## The request, step by step

1. Bearer token to `Principal` (user id, roles), or 401. `app/infra/security.py:77`
2. The `Idempotency-Key` is reserved in its own short transaction. Same key, different body: 422.
   Same key, still running: 409. Same key, done: the stored response goes back, nothing else runs.
   `app/domain/service.py:115`
3. One transaction opens. `app/domain/service.py:121`
4. The invoice row is loaded and locked with `SELECT ... FOR UPDATE`, or 404. Owner or admin, or 403.
   `app/infra/repository.py:91`, `app/domain/service.py:61`
5. The rules run as a pure function: not already paid, same currency, positive, within the balance.
   Each rejection is a 409 with its own `code`. `app/domain/payment_rules.py:14`
6. Four writes, one commit: the payment, the new balance and status, the outbox event, the stored
   response. 201 with `invoice_id`, `payment_id`, `paid_cents`, `balance_cents`, `status`.
7. Any failure after step 2 frees the key so the client can retry. `app/domain/service.py:125`
8. Anything unexpected: 500 with `code` and `request_id` only. The traceback goes to the log under
   the same id, secrets scrubbed. `app/api/errors.py:65`

## Where each thing lives

| Topic | Where |
|---|---|
| Error handling | Typed errors in `app/domain/errors.py`, mapped to status codes in `app/api/errors.py:27`. |
| Logging | One `logger.exception`, `app/api/errors.py:68`. Redaction and JSON lines in `app/infra/logging.py`. Test: `tests/test_logging.py`. |
| Code structure | `app/api` knows HTTP. `app/domain` knows rules, no HTTP, no SQL. `app/infra` knows Postgres, passwords, tokens. Read `app/domain/service.py` first. |
| Credentials | `app/infra/settings.py`. `DATABASE_URL` and `JWT_SECRET` come from the environment or `.env`, no default, typed `SecretStr`. `.env` is gitignored. |
| Passwords and tokens | Hashing call `app/infra/security.py:28`, compare `:36`. Each request: token claims become a `Principal`, no database read. |
| Layers | Storage interface `app/domain/ports.py`, implemented by `app/infra/repository.py`. Rows become domain objects at the bottom of that file. |
| Routes and access | Route table at the top of `app/api/routes.py`. `/auth/login` is public; every other route lists `Caller`. Permissions: `assert_admin`, `assert_can_view`, `assert_can_pay` at `app/domain/service.py:51`. |
| Database access | ORM with bound parameters. One hand-written `text()` query with named parameters, `app/infra/repository.py:116`. Idempotency insert with `ON CONFLICT DO NOTHING RETURNING`, `:196`. Explicit transactions only, `app/infra/db.py`. CHECK constraints in `app/infra/orm.py`. |
| Tests and tooling | 43 tests, one per outcome. Pydantic validation in `app/api/schemas.py`. `uv.lock`, Dependabot, ruff, CI with a Postgres service, Dockerfile, Compose. |

## Known gaps

- No migrations: tables come from `create_all` at startup. Production needs Alembic.
- No refresh tokens and no rate limit on `/auth/login`.
- Idempotency keys never expire. A nightly delete, or a TTL attribute on DynamoDB.
- Nothing publishes the `outbox` rows yet. A relay would send them to SQS or EventBridge and set `published_at`.
- A rejected payment frees the key instead of storing the error, so a retry recomputes the answer.
- 403 on someone else's invoice reveals that it exists. 404 would hide that.
- Login logic sits in the route. It belongs in a service, like the invoice logic.

## Deploy

Image from `Dockerfile` (`python:3.14-slim`, uv, no dev packages). CI runs ruff and pytest against a
Postgres service on every push. From there: build the image, push it to a registry, deploy with
Terraform (ECS or Lambda behind API Gateway) with `DATABASE_URL` and `JWT_SECRET` injected from a
secrets store. The idempotency table maps well to DynamoDB with a TTL; the rest stays in Postgres.
Logs are JSON lines on stderr, shipped to CloudWatch and searchable by `request_id`.

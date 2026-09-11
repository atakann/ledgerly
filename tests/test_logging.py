"""What the log records, and what it keeps out."""

import io
import json

from app.infra.logging import configure_logging, redact


def test_redact_removes_every_kind_of_secret_and_keeps_the_rest():
    text = (
        "login failed for alice@example.com password=hunter2, "
        "Authorization: Bearer eyJhbGciOi.eyJzdWIiOi.HN7KwCn7Hw, "
        'stored $argon2id$v=19$m=65536,t=3,p=4$n8NUEsyj$OxMLc6rR, token="t0k3n-value"'
    )
    out = redact(text)
    for secret in ("alice@example.com", "hunter2", "eyJhbGciOi", "$argon2id$", "t0k3n-value"):
        assert secret not in out
    assert "login failed" in out


def test_unhandled_error_gives_500_and_a_scrubbed_traceback_with_the_same_request_id(
    app, client, alice_headers
):
    log = io.StringIO()
    configure_logging("INFO", stream=log)
    token = alice_headers["Authorization"].split()[1]

    @app.get("/boom")
    def boom():
        raise RuntimeError(f"database down for alice@example.com, token was {token}")

    response = client.get("/boom", headers=alice_headers)

    assert response.status_code == 500
    body = response.json()
    assert set(body) == {"code", "request_id"}  # no message, no trace, nothing else
    assert body["code"] == "internal_error"

    lines = [json.loads(line) for line in log.getvalue().splitlines()]
    error = next(line for line in lines if line["level"] == "ERROR")
    assert error["request_id"] == body["request_id"]
    assert error["path"] == "/boom"
    assert "RuntimeError" in error["exc"]
    assert "alice@example.com" not in error["exc"]
    assert token not in error["exc"]

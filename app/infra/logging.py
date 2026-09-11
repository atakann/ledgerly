"""Logging with redaction: where errors are written, what is recorded, and what is kept out.

configure_logging() installs one handler on the root logger. Every record passes through
RedactFilter before it is written. The filter replaces:

  - bearer tokens and anything shaped like a JWT
  - email addresses
  - argon2 password hashes
  - the value after password=, secret=, token= or authorization= (also with ':' and quotes)

Each log line is one JSON object: ts, level, logger, request_id, message, and exc for a
traceback. request_id comes from a context variable that the middleware in app/main.py sets,
so every line of one request carries the same id as the error body the client got.
"""

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import IO

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]+"), "Bearer [redacted]"),
    (re.compile(r"eyJ[\w-]{6,}\.[\w-]{6,}\.[\w-]{6,}"), "[jwt redacted]"),
    (re.compile(r"\$argon2[a-z]*\$[^\s'\"]+"), "[hash redacted]"),
    (re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+"), "[email redacted]"),
    (
        re.compile(
            r"(?i)\b(password|passwd|secret|token|authorization)\b(['\"]?\s*[:=]\s*['\"]?)([^\s,'\"}]+)"
        ),
        r"\1\2[redacted]",
    ),
]


def redact(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactFilter(logging.Filter):
    """Scrubs the message and the traceback text. Runs before the formatter."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        if record.exc_info:
            # Another handler may have formatted and cached the traceback already. Scrub it.
            text = record.exc_text or logging.Formatter().formatException(record.exc_info)
            record.exc_text = redact(text)
        if not getattr(record, "request_id", None):
            record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        if getattr(record, "path", None):
            line["path"] = record.path
        if record.exc_text:
            line["exc"] = record.exc_text
        return json.dumps(line)


HANDLER_NAME = "ledgerly"


def configure_logging(level: str = "INFO", stream: IO[str] | None = None) -> logging.Handler:
    """Install the JSON handler with the redaction filter on the root logger. Safe to call
    more than once: an earlier ledgerly handler is replaced, other handlers stay."""
    root = logging.getLogger()
    for existing in list(root.handlers):
        if existing.name == HANDLER_NAME:
            root.removeHandler(existing)
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.name = HANDLER_NAME
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactFilter())
    root.addHandler(handler)
    root.setLevel(level.upper())
    return handler

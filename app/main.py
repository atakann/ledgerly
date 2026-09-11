"""Application factory. Wires settings, database, auth, error handlers and routes.
No business logic here.

Run:   uv run uvicorn app.main:create_app --factory --reload
Docs:  http://localhost:8000/docs
"""

import uuid

from fastapi import FastAPI, Request

from app.api.errors import register_error_handlers
from app.api.routes import router
from app.infra.db import make_engine, make_session_factory
from app.infra.logging import configure_logging, request_id_var
from app.infra.orm import Base
from app.infra.security import LocalHS256Verifier
from app.infra.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()  # refuses to start without DATABASE_URL and JWT_SECRET
    configure_logging(settings.log_level)

    engine = make_engine(settings.database_url.get_secret_value())
    Base.metadata.create_all(engine)  # no migrations in this version, see the README

    app = FastAPI(title="ledgerly", docs_url="/docs")
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.auth = LocalHS256Verifier(
        settings.jwt_secret.get_secret_value(), settings.jwt_ttl_seconds
    )

    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        """One id per request. It goes into every error body and every log line."""
        request.state.request_id = uuid.uuid4().hex
        request_id_var.set(request.state.request_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    register_error_handlers(app)
    app.include_router(router)
    return app

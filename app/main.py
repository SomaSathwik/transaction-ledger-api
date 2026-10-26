"""Application entry point.

Run locally with::

    uvicorn app.main:app --reload

Interactive API documentation is served at ``/docs`` (Swagger UI) and
``/redoc`` once the server is running.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from app import schemas
from app.config import Settings, get_settings
from app.database import create_db_engine, create_session_factory, init_db
from app.errors import register_exception_handlers
from app.routers import accounts, transactions, transfers

OPENAPI_TAGS = [
    {"name": "Accounts", "description": "Create accounts, read balances and browse transaction history."},
    {"name": "Transactions", "description": "Post credits/debits and read individual transactions."},
    {"name": "Transfers", "description": "Atomic account-to-account money transfers."},
    {"name": "System", "description": "Health and diagnostics."},
]

DESCRIPTION = """
A small, strongly consistent transaction ledger.

* Accounts start at a **zero** balance; the client cannot set it.
* Credits increase and debits decrease the balance; overdrafts are rejected.
* `reference_id` is an idempotency key that is unique per account.
* Transactions are **immutable** and are always listed latest-first with filtering and pagination.
* Creating a transaction and updating the balance is a single atomic database transaction.
* Transfers debit one account and credit another atomically - no partial transfers.

All errors share one envelope: `{"error": {"code": "...", "message": "...", "details": {...}}}`.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    """Application factory (also used by the test-suite with a throw-away database)."""
    settings = settings or get_settings()
    engine = create_db_engine(settings.database_url, echo=settings.sql_echo)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(engine)
        yield
        engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=DESCRIPTION,
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)

    register_exception_handlers(app)

    app.include_router(accounts.router)
    app.include_router(transactions.router)
    app.include_router(transfers.router)

    @app.get("/health", tags=["System"], response_model=schemas.HealthResponse, summary="Health check")
    def health() -> schemas.HealthResponse:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return schemas.HealthResponse(status="ok", database=engine.dialect.name, version=settings.app_version)

    return app


app = create_app()

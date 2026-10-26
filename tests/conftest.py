"""Shared pytest fixtures.

Each test gets a brand-new SQLite database file so tests are fully isolated
and can safely exercise multi-threaded scenarios (file databases can be shared
between connections, unlike ``:memory:``).

Set ``TEST_DATABASE_URL`` to run the same suite against another database, e.g.::

    TEST_DATABASE_URL=postgresql+psycopg://ledger:ledger@localhost:5432/ledger_test pytest
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import Settings
from app.main import create_app
from app.models import Base


@pytest.fixture()
def database_url(tmp_path) -> str:
    return os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'test.db'}"


@pytest.fixture()
def app(database_url: str):
    application = create_app(Settings(database_url=database_url))
    yield application
    engine = application.state.engine
    if not database_url.startswith("sqlite"):
        # Leave shared databases clean for the next test.
        Base.metadata.drop_all(engine)
        with engine.begin() as conn:
            conn.execute(text("DROP FUNCTION IF EXISTS prevent_transaction_mutation() CASCADE"))
    engine.dispose()


@pytest.fixture()
def client(app) -> Iterator[TestClient]:
    # Entering the context manager runs the lifespan (creates the schema).
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def session_factory(client, app):
    """Direct database access for white-box assertions (schema already created)."""
    return app.state.session_factory


# ---------------------------------------------------------------------------
# Small helpers used across test modules
# ---------------------------------------------------------------------------
def create_account(client: TestClient, name: str = "Alice") -> dict:
    response = client.post("/accounts", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def post_txn(client: TestClient, account_id: str, type_: str, amount, reference_id: str, **extra):
    payload = {"type": type_, "amount": amount, "reference_id": reference_id, **extra}
    return client.post(f"/accounts/{account_id}/transactions", json=payload)


def credit(client: TestClient, account_id: str, amount, reference_id: str, **extra) -> dict:
    response = post_txn(client, account_id, "credit", amount, reference_id, **extra)
    assert response.status_code == 201, response.text
    return response.json()


def debit(client: TestClient, account_id: str, amount, reference_id: str, **extra) -> dict:
    response = post_txn(client, account_id, "debit", amount, reference_id, **extra)
    assert response.status_code == 201, response.text
    return response.json()


def balance_of(client: TestClient, account_id: str) -> str:
    response = client.get(f"/accounts/{account_id}/balance")
    assert response.status_code == 200, response.text
    return response.json()["balance"]


def history(client: TestClient, account_id: str, **params) -> dict:
    response = client.get(f"/accounts/{account_id}/transactions", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def assert_error(response, status_code: int, code: str) -> dict:
    assert response.status_code == status_code, response.text
    body = response.json()
    assert set(body) == {"error"}, body
    assert body["error"]["code"] == code, body
    assert body["error"]["message"]
    return body["error"]

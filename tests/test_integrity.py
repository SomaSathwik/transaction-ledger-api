"""Section 5 - Data Integrity: immutability, balance consistency, atomicity, concurrency."""

from __future__ import annotations

import threading
from decimal import Decimal
from unittest import mock

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app import schemas
from app.errors import DuplicateTransactionError, InsufficientBalanceError
from app.models import Account, Transaction, TransactionType
from app.services import ledger
from tests.conftest import assert_error, balance_of, create_account, credit, debit, history, post_txn

# ---------------------------------------------------------------------------
# Transaction immutability
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_transactions_cannot_be_modified_through_the_api(client, method):
    account = create_account(client)
    txn = credit(client, account["id"], 10, "r1")

    response = getattr(client, method)(
        f"/transactions/{txn['id']}", **({"json": {"amount": 1}} if method != "delete" else {})
    )

    error = assert_error(response, 405, "METHOD_NOT_ALLOWED")
    assert "immutable" in error["message"]
    # Unchanged.
    fetched = client.get(f"/transactions/{txn['id']}").json()
    assert fetched == {k: v for k, v in txn.items() if k != "balance_after"}


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_account_transaction_collection_rejects_mutating_methods(client, method):
    account = create_account(client)
    credit(client, account["id"], 10, "r1")
    response = getattr(client, method)(f"/accounts/{account['id']}/transactions")
    assert_error(response, 405, "METHOD_NOT_ALLOWED")


def test_database_blocks_updates_and_deletes_of_transactions(client, session_factory):
    """Belt and braces: even raw SQL cannot rewrite history thanks to triggers."""
    account = create_account(client)
    txn = credit(client, account["id"], 10, "r1")

    for statement in (
        "UPDATE transactions SET amount_minor = 1 WHERE id = :id",
        "DELETE FROM transactions WHERE id = :id",
    ):
        with session_factory() as session:
            with pytest.raises(Exception) as exc_info:
                with session.begin():
                    session.execute(text(statement), {"id": txn["id"]})
            assert "immutable" in str(exc_info.value)

    assert client.get(f"/transactions/{txn['id']}").json()["amount"] == "10.00"


# ---------------------------------------------------------------------------
# Balance consistency
# ---------------------------------------------------------------------------


def test_balance_example_from_the_brief(client):
    account = create_account(client)
    credit(client, account["id"], 100, "c1")
    credit(client, account["id"], 50, "c2")
    debit(client, account["id"], 30, "d1")
    debit(client, account["id"], 20, "d2")

    assert balance_of(client, account["id"]) == "100.00"


def test_balance_equals_sum_of_recorded_transactions(client, session_factory):
    account = create_account(client)
    credit(client, account["id"], "10.25", "c1")
    credit(client, account["id"], "0.75", "c2")
    debit(client, account["id"], "3.50", "d1")
    assert_error(post_txn(client, account["id"], "debit", 1000, "d2"), 422, "INSUFFICIENT_BALANCE")  # rejected
    assert_error(post_txn(client, account["id"], "credit", 5, "c1"), 409, "DUPLICATE_TRANSACTION")  # rejected

    with session_factory() as session:
        recomputed = ledger.compute_balance_from_history(session, account["id"])
    assert recomputed == Decimal("7.50")
    assert balance_of(client, account["id"]) == "7.50"

    # Only the three valid transactions were recorded.
    rows = history(client, account["id"])["data"]
    assert [t["reference_id"] for t in rows] == ["d1", "c2", "c1"]


def test_failed_transactions_do_not_affect_balance_or_history(client):
    account = create_account(client)
    credit(client, account["id"], 100, "ok")

    assert_error(post_txn(client, account["id"], "debit", 500, "fail-1"), 422, "INSUFFICIENT_BALANCE")
    assert_error(post_txn(client, account["id"], "credit", -5, "fail-2"), 400, "VALIDATION_ERROR")
    assert_error(post_txn(client, account["id"], "credit", 5, "ok"), 409, "DUPLICATE_TRANSACTION")
    assert_error(post_txn(client, "ghost", "credit", 5, "fail-3"), 404, "ACCOUNT_NOT_FOUND")

    assert balance_of(client, account["id"]) == "100.00"
    assert history(client, account["id"])["pagination"]["total_records"] == 1


# ---------------------------------------------------------------------------
# Atomicity
# ---------------------------------------------------------------------------


def test_transaction_insert_failure_rolls_back_balance_update(client, session_factory):
    """If persisting the transaction row fails, the balance update must not survive."""
    account = create_account(client)
    credit(client, account["id"], 100, "seed")

    def exploding_flush(self, *args, **kwargs):
        raise IntegrityError("INSERT INTO transactions", {}, Exception("simulated disk failure"))

    with session_factory() as session:
        with mock.patch.object(type(session), "flush", exploding_flush):
            with pytest.raises(IntegrityError):
                ledger.post_transaction(
                    session,
                    account["id"],
                    schemas.TransactionCreate(type=TransactionType.CREDIT, amount=Decimal("40"), reference_id="boom"),
                )

    assert balance_of(client, account["id"]) == "100.00"
    assert history(client, account["id"])["pagination"]["total_records"] == 1


def test_database_check_constraint_is_the_last_line_of_defence(client, session_factory):
    """Even if application checks were bypassed, the database refuses a negative balance."""
    account = create_account(client)
    with session_factory() as session:
        with pytest.raises(IntegrityError):
            with session.begin():
                row = session.scalar(select(Account).where(Account.id == account["id"]))
                row.balance_minor = -1
    assert balance_of(client, account["id"]) == "0.00"


def test_database_unique_constraint_is_the_last_line_of_defence(client, session_factory):
    account = create_account(client)
    credit(client, account["id"], 1, "same")
    with session_factory() as session:
        with pytest.raises(IntegrityError):
            with session.begin():
                session.add(Transaction(account_id=account["id"], type="credit", amount_minor=100, reference_id="same"))
    assert history(client, account["id"])["pagination"]["total_records"] == 1


def test_database_rejects_invalid_type_and_amount(client, session_factory):
    account = create_account(client)
    for kwargs in ({"type": "refund", "amount_minor": 100}, {"type": "credit", "amount_minor": 0}):
        with session_factory() as session:
            with pytest.raises(IntegrityError):
                with session.begin():
                    session.add(Transaction(account_id=account["id"], reference_id="x", **kwargs))


# ---------------------------------------------------------------------------
# Concurrency (the atomicity contract under parallel load)
# ---------------------------------------------------------------------------


def _run_in_threads(count, target):
    barrier = threading.Barrier(count)
    errors: list[BaseException] = []

    def runner(i):
        barrier.wait()
        try:
            target(i)
        except BaseException as exc:  # noqa: BLE001 - collected and re-raised below
            errors.append(exc)

    threads = [threading.Thread(target=runner, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


def test_concurrent_debits_never_overdraw(client, session_factory):
    account = create_account(client)
    credit(client, account["id"], 100, "seed")
    outcome = {"ok": 0, "rejected": 0}
    lock = threading.Lock()

    def attempt_debit(i):
        with session_factory() as session:
            try:
                ledger.post_transaction(
                    session,
                    account["id"],
                    schemas.TransactionCreate(type=TransactionType.DEBIT, amount=Decimal("10"), reference_id=f"d{i}"),
                )
                with lock:
                    outcome["ok"] += 1
            except InsufficientBalanceError:
                with lock:
                    outcome["rejected"] += 1

    assert _run_in_threads(25, attempt_debit) == []
    assert outcome == {"ok": 10, "rejected": 15}
    assert balance_of(client, account["id"]) == "0.00"
    with session_factory() as session:
        assert ledger.compute_balance_from_history(session, account["id"]) == Decimal("0")


def test_concurrent_duplicate_submissions_create_exactly_one_transaction(client, session_factory):
    account = create_account(client)
    outcome = {"ok": 0, "duplicate": 0}
    lock = threading.Lock()

    def attempt(i):
        with session_factory() as session:
            try:
                ledger.post_transaction(
                    session,
                    account["id"],
                    schemas.TransactionCreate(type=TransactionType.CREDIT, amount=Decimal("1"), reference_id="same"),
                )
                with lock:
                    outcome["ok"] += 1
            except DuplicateTransactionError:
                with lock:
                    outcome["duplicate"] += 1

    assert _run_in_threads(10, attempt) == []
    assert outcome == {"ok": 1, "duplicate": 9}
    assert balance_of(client, account["id"]) == "1.00"

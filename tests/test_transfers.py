"""Section 9 (bonus) - Money Transfer."""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app import schemas
from app.services import ledger
from tests.conftest import assert_error, balance_of, create_account, credit, history


def _two_accounts(client, a_balance=100, b_balance=50):
    a = create_account(client, "A")
    b = create_account(client, "B")
    if a_balance:
        credit(client, a["id"], a_balance, "seed")
    if b_balance:
        credit(client, b["id"], b_balance, "seed")
    return a, b


def test_transfer_example_from_the_brief(client):
    a, b = _two_accounts(client, 100, 50)

    response = client.post("/transfers", json={"from_account_id": a["id"], "to_account_id": b["id"], "amount": 30})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["amount"] == "30.00"
    assert body["from_account_balance"] == "70.00"
    assert body["to_account_balance"] == "80.00"
    assert balance_of(client, a["id"]) == "70.00"
    assert balance_of(client, b["id"]) == "80.00"


def test_transfer_records_two_linked_immutable_transactions(client):
    a, b = _two_accounts(client)
    body = client.post(
        "/transfers",
        json={
            "from_account_id": a["id"],
            "to_account_id": b["id"],
            "amount": 30,
            "reference_id": "TRF-1",
            "description": "rent",
        },
    ).json()

    debit_leg, credit_leg = body["debit_transaction"], body["credit_transaction"]
    assert debit_leg["type"] == "debit" and debit_leg["account_id"] == a["id"]
    assert credit_leg["type"] == "credit" and credit_leg["account_id"] == b["id"]
    assert debit_leg["amount"] == credit_leg["amount"] == "30.00"
    assert debit_leg["reference_id"] == credit_leg["reference_id"] == "TRF-1"
    assert debit_leg["description"] == credit_leg["description"] == "rent"
    assert debit_leg["transfer_id"] == credit_leg["transfer_id"] == body["id"]

    # Both legs show up in the respective histories.
    assert history(client, a["id"])["data"][0]["id"] == debit_leg["id"]
    assert history(client, b["id"])["data"][0]["id"] == credit_leg["id"]

    # And are immutable like any other transaction.
    assert_error(client.delete(f"/transactions/{debit_leg['id']}"), 405, "METHOD_NOT_ALLOWED")


def test_transfer_can_be_fetched(client):
    a, b = _two_accounts(client)
    created = client.post("/transfers", json={"from_account_id": a["id"], "to_account_id": b["id"], "amount": 5}).json()

    response = client.get(f"/transfers/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


def test_get_unknown_transfer(client):
    assert_error(client.get("/transfers/nope"), 404, "TRANSFER_NOT_FOUND")


def test_transfer_generates_reference_and_descriptions_when_omitted(client):
    a, b = _two_accounts(client)
    body = client.post("/transfers", json={"from_account_id": a["id"], "to_account_id": b["id"], "amount": 1}).json()
    assert body["reference_id"].startswith("TRF-")
    assert body["debit_transaction"]["description"] == f"Transfer to account {b['id']}"
    assert body["credit_transaction"]["description"] == f"Transfer from account {a['id']}"


def test_transfer_with_insufficient_balance_is_rolled_back(client):
    a, b = _two_accounts(client, 100, 50)

    response = client.post("/transfers", json={"from_account_id": a["id"], "to_account_id": b["id"], "amount": 150})

    error = assert_error(response, 422, "INSUFFICIENT_BALANCE")
    assert error["details"]["account_id"] == a["id"]
    assert balance_of(client, a["id"]) == "100.00"
    assert balance_of(client, b["id"]) == "50.00"
    assert history(client, a["id"])["pagination"]["total_records"] == 1
    assert history(client, b["id"])["pagination"]["total_records"] == 1


def test_transfer_requires_both_accounts_to_exist(client):
    a, _ = _two_accounts(client)

    error = assert_error(
        client.post("/transfers", json={"from_account_id": a["id"], "to_account_id": "ghost", "amount": 10}),
        404,
        "ACCOUNT_NOT_FOUND",
    )
    assert "Destination" in error["message"]
    assert balance_of(client, a["id"]) == "100.00"

    error = assert_error(
        client.post("/transfers", json={"from_account_id": "ghost", "to_account_id": a["id"], "amount": 10}),
        404,
        "ACCOUNT_NOT_FOUND",
    )
    assert "Source" in error["message"]
    assert balance_of(client, a["id"]) == "100.00"


def test_transfer_to_same_account_is_rejected(client):
    a, _ = _two_accounts(client)
    assert_error(
        client.post("/transfers", json={"from_account_id": a["id"], "to_account_id": a["id"], "amount": 10}),
        422,
        "SAME_ACCOUNT_TRANSFER",
    )
    assert balance_of(client, a["id"]) == "100.00"


def test_transfer_is_idempotent_per_reference_id(client):
    a, b = _two_accounts(client)
    payload = {"from_account_id": a["id"], "to_account_id": b["id"], "amount": 30, "reference_id": "TRF-SAME"}

    assert client.post("/transfers", json=payload).status_code == 201
    error = assert_error(client.post("/transfers", json=payload), 409, "DUPLICATE_TRANSACTION")

    assert error["details"]["reference_id"] == "TRF-SAME"
    assert balance_of(client, a["id"]) == "70.00"
    assert balance_of(client, b["id"]) == "80.00"


def test_transfer_reference_conflicting_with_destination_transaction_is_rejected(client):
    a, b = _two_accounts(client)
    credit(client, b["id"], 1, "USED-ON-B")

    response = client.post(
        "/transfers",
        json={"from_account_id": a["id"], "to_account_id": b["id"], "amount": 30, "reference_id": "USED-ON-B"},
    )

    error = assert_error(response, 409, "DUPLICATE_TRANSACTION")
    assert error["details"]["account_id"] == b["id"]
    assert balance_of(client, a["id"]) == "100.00"  # nothing was debited
    assert balance_of(client, b["id"]) == "51.00"


@pytest.mark.parametrize(
    "payload",
    [
        {"to_account_id": "x", "amount": 1},  # missing from
        {"from_account_id": "x", "amount": 1},  # missing to
        {"from_account_id": "x", "to_account_id": "y"},  # missing amount
        {"from_account_id": "x", "to_account_id": "y", "amount": 0},
        {"from_account_id": "x", "to_account_id": "y", "amount": -3},
        {"from_account_id": "x", "to_account_id": "y", "amount": "1.234"},
        {"from_account_id": "x", "to_account_id": "y", "amount": 1, "reference_id": ""},
        {"from_account_id": "x", "to_account_id": "y", "amount": 1, "unexpected": True},
    ],
)
def test_transfer_validation(client, payload):
    assert_error(client.post("/transfers", json=payload), 400, "VALIDATION_ERROR")


def test_transfer_failure_mid_way_leaves_no_partial_state(client, session_factory):
    """Simulate a failure after the debit was applied but before the legs are persisted."""
    a, b = _two_accounts(client, 100, 50)

    original_flush = ledger.Session.flush
    calls = {"n": 0}

    def flaky_flush(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:  # the second flush writes the two transaction legs
            raise IntegrityError("INSERT INTO transactions", {}, Exception("simulated failure"))
        return original_flush(self, *args, **kwargs)

    with session_factory() as session:
        ledger.Session.flush = flaky_flush
        try:
            with pytest.raises(IntegrityError):
                ledger.transfer(
                    session,
                    schemas.TransferCreate(from_account_id=a["id"], to_account_id=b["id"], amount=Decimal("30")),
                )
        finally:
            ledger.Session.flush = original_flush

    assert balance_of(client, a["id"]) == "100.00"
    assert balance_of(client, b["id"]) == "50.00"
    assert history(client, a["id"])["pagination"]["total_records"] == 1
    assert history(client, b["id"])["pagination"]["total_records"] == 1


def test_concurrent_opposite_transfers_keep_total_money_constant(client, session_factory):
    a, b = _two_accounts(client, 1000, 1000)
    errors: list[BaseException] = []
    barrier = threading.Barrier(20)

    def worker(i):
        barrier.wait()
        src, dst = (a["id"], b["id"]) if i % 2 == 0 else (b["id"], a["id"])
        with session_factory() as session:
            try:
                ledger.transfer(
                    session, schemas.TransferCreate(from_account_id=src, to_account_id=dst, amount=Decimal("7"))
                )
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert balance_of(client, a["id"]) == "1000.00"
    assert balance_of(client, b["id"]) == "1000.00"
    assert history(client, a["id"])["pagination"]["total_records"] == 21

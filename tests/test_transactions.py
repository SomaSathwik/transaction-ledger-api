"""Section 2 - Add Transaction, Section 7 - Validations, Section 8 - Error Handling."""

from __future__ import annotations

import pytest

from tests.conftest import assert_error, balance_of, create_account, credit, debit, history, post_txn


def test_credit_increases_balance(client):
    account = create_account(client)
    credit(client, account["id"], 100, "r1")

    body = credit(client, account["id"], 50, "r2")

    assert body["balance_after"] == "150.00"
    assert balance_of(client, account["id"]) == "150.00"


def test_debit_decreases_balance(client):
    account = create_account(client)
    credit(client, account["id"], 100, "r1")

    body = debit(client, account["id"], 30, "r2")

    assert body["balance_after"] == "70.00"
    assert balance_of(client, account["id"]) == "70.00"


def test_transaction_response_contains_required_fields(client):
    account = create_account(client)
    body = credit(client, account["id"], "25.50", "INV-1", description="Invoice 1")

    assert body["account_id"] == account["id"]
    assert body["type"] == "credit"
    assert body["amount"] == "25.50"
    assert body["description"] == "Invoice 1"
    assert body["reference_id"] == "INV-1"
    assert body["id"] and body["created_at"]
    assert body["transfer_id"] is None


def test_transaction_ids_are_unique(client):
    account = create_account(client)
    ids = {credit(client, account["id"], 1, f"r{i}")["id"] for i in range(20)}
    assert len(ids) == 20


def test_debit_with_insufficient_balance_is_rejected(client):
    account = create_account(client)
    credit(client, account["id"], 100, "r1")

    error = assert_error(post_txn(client, account["id"], "debit", 150, "r2"), 422, "INSUFFICIENT_BALANCE")

    assert error["details"] == {"account_id": account["id"], "balance": "100.00", "requested_amount": "150.00"}
    assert balance_of(client, account["id"]) == "100.00"
    # The rejected transaction was not recorded.
    assert history(client, account["id"])["pagination"]["total_records"] == 1


def test_debit_from_empty_account_is_rejected(client):
    account = create_account(client)
    assert_error(post_txn(client, account["id"], "debit", "0.01", "r1"), 422, "INSUFFICIENT_BALANCE")
    assert balance_of(client, account["id"]) == "0.00"


def test_debit_of_exact_balance_is_allowed(client):
    account = create_account(client)
    credit(client, account["id"], 100, "r1")
    assert debit(client, account["id"], 100, "r2")["balance_after"] == "0.00"


def test_duplicate_reference_id_same_account(client):
    account = create_account(client)
    first = credit(client, account["id"], 100, "DUP-1")

    error = assert_error(post_txn(client, account["id"], "credit", 100, "DUP-1"), 409, "DUPLICATE_TRANSACTION")

    assert error["details"]["reference_id"] == "DUP-1"
    assert error["details"]["existing_transaction"]["id"] == first["id"]
    assert balance_of(client, account["id"]) == "100.00"
    assert history(client, account["id"])["pagination"]["total_records"] == 1


def test_duplicate_reference_id_with_different_payload_is_still_rejected(client):
    account = create_account(client)
    credit(client, account["id"], 100, "DUP-2")

    assert_error(post_txn(client, account["id"], "debit", 5, "DUP-2"), 409, "DUPLICATE_TRANSACTION")
    assert balance_of(client, account["id"]) == "100.00"


def test_same_reference_id_allowed_for_different_accounts(client):
    alice = create_account(client, "Alice")
    bob = create_account(client, "Bob")

    credit(client, alice["id"], 10, "SHARED")
    credit(client, bob["id"], 20, "SHARED")

    assert balance_of(client, alice["id"]) == "10.00"
    assert balance_of(client, bob["id"]) == "20.00"


def test_rejected_transaction_does_not_consume_reference_id(client):
    account = create_account(client)
    assert_error(post_txn(client, account["id"], "debit", 10, "RETRY"), 422, "INSUFFICIENT_BALANCE")
    credit(client, account["id"], 10, "RETRY")  # same reference now succeeds
    assert balance_of(client, account["id"]) == "10.00"


def test_unknown_account(client):
    error = assert_error(post_txn(client, "missing", "credit", 10, "r1"), 404, "ACCOUNT_NOT_FOUND")
    assert error["details"]["account_id"] == "missing"


@pytest.mark.parametrize("bad_type", ["CREDIT", "Debit", "transfer", "", None, 1])
def test_invalid_transaction_type(client, bad_type):
    account = create_account(client)
    error = assert_error(post_txn(client, account["id"], bad_type, 10, "r1"), 400, "VALIDATION_ERROR")
    assert error["details"]["fields"][0]["field"] == "body.type"


@pytest.mark.parametrize("bad_amount", [0, -1, -0.01, "0", "abc", None, True, "1.005", 1.005, "1e3x"])
def test_invalid_amount(client, bad_amount):
    account = create_account(client)
    error = assert_error(post_txn(client, account["id"], "credit", bad_amount, "r1"), 400, "VALIDATION_ERROR")
    assert error["details"]["fields"][0]["field"] == "body.amount"
    assert balance_of(client, account["id"]) == "0.00"


@pytest.mark.parametrize("amount, expected", [(100, "100.00"), ("12.5", "12.50"), (0.1, "0.10"), ("0.01", "0.01")])
def test_amount_formats_accepted(client, amount, expected):
    account = create_account(client)
    assert credit(client, account["id"], amount, "r1")["amount"] == expected


def test_decimal_arithmetic_is_exact(client):
    account = create_account(client)
    for i in range(10):
        credit(client, account["id"], 0.1, f"c{i}")
    debit(client, account["id"], "0.30", "d1")
    assert balance_of(client, account["id"]) == "0.70"


def test_reference_id_is_required(client):
    account = create_account(client)
    response = client.post(f"/accounts/{account['id']}/transactions", json={"type": "credit", "amount": 10})
    error = assert_error(response, 400, "VALIDATION_ERROR")
    assert error["details"]["fields"][0]["field"] == "body.reference_id"


@pytest.mark.parametrize("bad_ref", ["", "   ", None, 123])
def test_invalid_reference_id(client, bad_ref):
    account = create_account(client)
    assert_error(post_txn(client, account["id"], "credit", 10, bad_ref), 400, "VALIDATION_ERROR")


def test_unknown_fields_are_rejected(client):
    account = create_account(client)
    response = post_txn(client, account["id"], "credit", 10, "r1", balance=999)
    error = assert_error(response, 400, "VALIDATION_ERROR")
    assert error["details"]["fields"][0]["field"] == "body.balance"


def test_multiple_validation_errors_are_reported_together(client):
    account = create_account(client)
    response = client.post(f"/accounts/{account['id']}/transactions", json={"type": "x", "amount": -1})
    error = assert_error(response, 400, "VALIDATION_ERROR")
    fields = {f["field"] for f in error["details"]["fields"]}
    assert fields == {"body.type", "body.amount", "body.reference_id"}


def test_invalid_json_body(client):
    account = create_account(client)
    response = client.post(
        f"/accounts/{account['id']}/transactions", content=b"{", headers={"Content-Type": "application/json"}
    )
    assert_error(response, 400, "VALIDATION_ERROR")


def test_description_is_optional(client):
    account = create_account(client)
    assert credit(client, account["id"], 10, "r1")["description"] is None


def test_get_single_transaction(client):
    account = create_account(client)
    created = credit(client, account["id"], 10, "r1", description="hello")

    response = client.get(f"/transactions/{created['id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == created["id"]
    assert body["amount"] == "10.00"
    assert body["description"] == "hello"


def test_get_unknown_transaction(client):
    assert_error(client.get("/transactions/nope"), 404, "TRANSACTION_NOT_FOUND")


def test_flat_create_endpoint_with_account_id_in_body(client):
    account = create_account(client)
    response = client.post(
        "/transactions", json={"account_id": account["id"], "type": "credit", "amount": 42, "reference_id": "flat"}
    )
    assert response.status_code == 201, response.text
    assert response.json()["account_id"] == account["id"]
    assert response.json()["balance_after"] == "42.00"


def test_flat_create_endpoint_requires_account_id(client):
    response = client.post("/transactions", json={"type": "credit", "amount": 42, "reference_id": "flat"})
    error = assert_error(response, 400, "VALIDATION_ERROR")
    assert error["details"]["fields"][0]["field"] == "body.account_id"


def test_flat_create_endpoint_unknown_account(client):
    response = client.post(
        "/transactions", json={"account_id": "ghost", "type": "credit", "amount": 1, "reference_id": "x"}
    )
    assert_error(response, 404, "ACCOUNT_NOT_FOUND")

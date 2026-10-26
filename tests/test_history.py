"""Section 4 - Get Transaction History: filtering, pagination, sorting."""

from __future__ import annotations

import pytest

from tests.conftest import assert_error, create_account, credit, debit, history


def _seed(client, count_credits=3, count_debits=2):
    account = create_account(client)
    ids = []
    for i in range(count_credits):
        ids.append(credit(client, account["id"], 100, f"c{i}")["id"])
    for i in range(count_debits):
        ids.append(debit(client, account["id"], 10, f"d{i}")["id"])
    return account, ids  # ids in creation order


def test_history_returns_all_transactions_by_default(client):
    account, ids = _seed(client)
    body = history(client, account["id"])

    assert body["pagination"]["total_records"] == 5
    assert {t["type"] for t in body["data"]} == {"credit", "debit"}
    assert {t["id"] for t in body["data"]} == set(ids)


def test_history_is_latest_first(client):
    account, ids = _seed(client)
    body = history(client, account["id"])

    returned = [t["id"] for t in body["data"]]
    assert returned == list(reversed(ids))
    timestamps = [t["created_at"] for t in body["data"]]
    assert timestamps == sorted(timestamps, reverse=True)


def test_history_is_latest_first_even_with_identical_timestamps(client, session_factory):
    """Ties on created_at are broken by insertion order, so the order is still deterministic."""
    from datetime import datetime, timezone

    from app.models import Transaction

    account = create_account(client)
    same_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    with session_factory() as session, session.begin():
        for i in range(3):
            session.add(
                Transaction(
                    account_id=account["id"],
                    type="credit",
                    amount_minor=100,
                    reference_id=f"t{i}",
                    created_at=same_time,
                )
            )

    refs = [t["reference_id"] for t in history(client, account["id"])["data"]]
    assert refs == ["t2", "t1", "t0"]


@pytest.mark.parametrize("type_, expected_count", [("credit", 3), ("debit", 2)])
def test_history_filter_by_type(client, type_, expected_count):
    account, _ = _seed(client)
    body = history(client, account["id"], type=type_)

    assert body["pagination"]["total_records"] == expected_count
    assert all(t["type"] == type_ for t in body["data"])


def test_history_invalid_type_filter(client):
    account, _ = _seed(client)
    error = assert_error(
        client.get(f"/accounts/{account['id']}/transactions", params={"type": "refund"}), 400, "VALIDATION_ERROR"
    )
    assert error["details"]["fields"][0]["field"] == "query.type"


def test_pagination_metadata_and_pages(client):
    account, ids = _seed(client)  # 5 transactions
    newest_first = list(reversed(ids))

    page1 = history(client, account["id"], page=1, limit=2)
    page2 = history(client, account["id"], page=2, limit=2)
    page3 = history(client, account["id"], page=3, limit=2)

    assert page1["pagination"] == {
        "page": 1,
        "limit": 2,
        "total_records": 5,
        "total_pages": 3,
        "has_next": True,
        "has_previous": False,
    }
    assert [t["id"] for t in page1["data"]] == newest_first[0:2]
    assert [t["id"] for t in page2["data"]] == newest_first[2:4]
    assert [t["id"] for t in page3["data"]] == newest_first[4:5]
    assert page3["pagination"]["has_next"] is False
    assert page3["pagination"]["has_previous"] is True


def test_pagination_defaults(client):
    account, _ = _seed(client)
    body = history(client, account["id"])
    assert body["pagination"]["page"] == 1
    assert body["pagination"]["limit"] == 20
    assert body["pagination"]["total_pages"] == 1


def test_pagination_with_type_filter_counts_only_matching_rows(client):
    account, _ = _seed(client, count_credits=5, count_debits=1)
    body = history(client, account["id"], type="credit", page=2, limit=2)
    assert body["pagination"]["total_records"] == 5
    assert body["pagination"]["total_pages"] == 3
    assert len(body["data"]) == 2


def test_page_beyond_last_returns_empty_page(client):
    account, _ = _seed(client)
    body = history(client, account["id"], page=10, limit=2)
    assert body["data"] == []
    assert body["pagination"]["total_records"] == 5
    assert body["pagination"]["has_next"] is False


def test_empty_history(client):
    account = create_account(client)
    body = history(client, account["id"])
    assert body["data"] == []
    assert body["pagination"]["total_pages"] == 0
    assert body["pagination"]["total_records"] == 0


@pytest.mark.parametrize(
    "params", [{"page": 0}, {"page": -1}, {"page": "abc"}, {"limit": 0}, {"limit": -5}, {"limit": 101}, {"limit": "x"}]
)
def test_invalid_pagination_parameters(client, params):
    account, _ = _seed(client)
    error = assert_error(client.get(f"/accounts/{account['id']}/transactions", params=params), 400, "VALIDATION_ERROR")
    assert error["details"]["fields"][0]["field"].startswith("query.")


def test_history_unknown_account(client):
    assert_error(client.get("/accounts/ghost/transactions"), 404, "ACCOUNT_NOT_FOUND")


def test_history_only_contains_the_accounts_own_transactions(client):
    alice = create_account(client, "Alice")
    bob = create_account(client, "Bob")
    credit(client, alice["id"], 1, "a1")
    credit(client, bob["id"], 2, "b1")

    body = history(client, alice["id"])
    assert [t["reference_id"] for t in body["data"]] == ["a1"]

# Transaction Ledger API

[![CI](https://github.com/SomaSathwik/transaction-ledger-api/actions/workflows/ci.yml/badge.svg)](https://github.com/SomaSathwik/transaction-ledger-api/actions/workflows/ci.yml)

A small, strongly consistent backend for a transaction ledger: create accounts, post credit/debit
transactions, read balances, browse paginated history and transfer money between accounts atomically.

Built with **Python 3.10+ · FastAPI · SQLAlchemy 2 · Pydantic 2**. Runs out of the box on **SQLite**
(zero setup) and on **PostgreSQL** (one environment variable or `docker compose up`). The same
119-test suite passes on both databases.

> Submission for the *Full Stack Developer Assessment – Transaction Ledger API*.
> Deliverables: source code (this repository), this **README**, the
> [API documentation](docs/API.md) (also served live at `/docs`) and the [assumptions](ASSUMPTIONS.md).

---

## Contents

1. [Quick start](#quick-start)
2. [Running with PostgreSQL](#running-with-postgresql)
3. [Configuration](#configuration)
4. [Running the tests](#running-the-tests)
5. [API overview](#api-overview)
6. [Requirement coverage](#requirement-coverage)
7. [Design and data integrity](#design-and-data-integrity)
8. [Project structure](#project-structure)
9. [Assumptions and possible next steps](#assumptions-and-possible-next-steps)

---

## Quick start

Prerequisites: Python **3.10 or newer** and `pip`. Nothing else – the default database is a SQLite file.

```bash
git clone https://github.com/SomaSathwik/transaction-ledger-api.git
cd transaction-ledger-api

python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

uvicorn app.main:app --reload
```

The API is now available at `http://localhost:8000`. Open these URLs in your browser **while the server is
running** (they point at your own machine, so they will not open from GitHub):

| URL (local) | What you get |
| --- | --- |
| `http://localhost:8000/docs` | Swagger UI – try every endpoint from the browser |
| `http://localhost:8000/redoc` | ReDoc rendering of the same OpenAPI specification |
| `http://localhost:8000/health` | Health check (`{"status": "ok", "database": "sqlite", ...}` ) |

If you want to read the API documentation without running anything, see [docs/API.md](docs/API.md) – it
contains the same endpoints with full request and response examples.

Tables are created automatically on first start (`ledger.db` in the project directory).

### 60-second walkthrough

```bash
# 1. create an account – balance always starts at 0.00
curl -s -X POST localhost:8000/accounts -H 'Content-Type: application/json' -d '{"name": "Alice"}'
# {"id":"<ACCOUNT_ID>","name":"Alice","balance":"0.00","created_at":"2026-10-27T09:15:00.123456Z"}

# 2. credit 100
curl -s -X POST localhost:8000/accounts/<ACCOUNT_ID>/transactions -H 'Content-Type: application/json' \
     -d '{"type":"credit","amount":100,"description":"Salary","reference_id":"SAL-001"}'

# 3. debit 30
curl -s -X POST localhost:8000/accounts/<ACCOUNT_ID>/transactions -H 'Content-Type: application/json' \
     -d '{"type":"debit","amount":30,"description":"Groceries","reference_id":"GRO-001"}'

# 4. balance -> 70.00
curl -s localhost:8000/accounts/<ACCOUNT_ID>/balance

# 5. history, debits only, latest first, paginated
curl -s "localhost:8000/accounts/<ACCOUNT_ID>/transactions?type=debit&page=1&limit=20"
```

`scripts/demo.sh` runs the complete scenario from the brief (including the rejected debit, the duplicate
`reference_id`, immutability and a transfer) against a running server:

```bash
./scripts/demo.sh                      # BASE_URL=http://localhost:8000 by default
```

A Postman collection with the same requests lives in [`docs/postman_collection.json`](docs/postman_collection.json).

---

## Running with PostgreSQL

**Option A – Docker Compose** (API + PostgreSQL 16):

```bash
docker compose up --build
# API on http://localhost:8000, PostgreSQL on localhost:5432 (user/password/db: ledger/ledger/ledger)
```

**Option B – your own PostgreSQL**:

```bash
export DATABASE_URL="postgresql+psycopg://ledger:ledger@localhost:5432/ledger"
uvicorn app.main:app --reload
```

The schema (tables, constraints, triggers) is created automatically on start-up for both databases.

---

## Configuration

All settings are environment variables (a local `.env` file is also read – see [`.env.example`](.env.example)).

| Variable | Default | Description |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///./ledger.db` | Any SQLAlchemy URL. Tested with SQLite and PostgreSQL (`postgresql+psycopg://…`). |
| `SQL_ECHO` | `false` | Log every SQL statement. |
| `DEFAULT_PAGE_SIZE` | `20` | Page size when `limit` is omitted. |
| `MAX_PAGE_SIZE` | `100` | Largest accepted `limit`. |

---

## Running the tests

```bash
pip install -r requirements-dev.txt
pytest                                   # 119 tests, ~5 s, isolated SQLite database per test
```

Run the identical suite against PostgreSQL:

```bash
TEST_DATABASE_URL="postgresql+psycopg://ledger:ledger@localhost:5432/ledger_test" pytest
```

The suite covers every rule in the brief, including the examples given there, plus the things that are
easy to get wrong in a ledger: exact decimal arithmetic, latest-first ordering with identical timestamps,
rollback when the transaction insert fails after the balance was updated, database-level enforcement
(check / unique constraints and immutability triggers) and **concurrency** – 25 parallel debits against a
balance of 100 must result in exactly 10 successes and a final balance of 0.00; 10 parallel submissions of
the same `reference_id` must create exactly one transaction; 20 opposite-direction parallel transfers must
leave the total amount of money unchanged. CI runs everything on Python 3.10 and 3.12 and against PostgreSQL.

---

## API overview

Full reference with request/response examples: **[docs/API.md](docs/API.md)**. Machine-readable spec:
[`docs/openapi.json`](docs/openapi.json) (regenerate with `python scripts/export_openapi.py`).

| Method | Path | Purpose | Success |
| --- | --- | --- | --- |
| `POST` | `/accounts` | Create an account (balance starts at 0.00) | `201` |
| `GET` | `/accounts/{account_id}` | Read an account | `200` |
| `GET` | `/accounts/{account_id}/balance` | Current balance | `200` |
| `POST` | `/accounts/{account_id}/transactions` | Add a credit/debit transaction | `201` |
| `GET` | `/accounts/{account_id}/transactions` | History – `type`, `page`, `limit`; latest first | `200` |
| `POST` | `/transactions` | Same as above with `account_id` in the body | `201` |
| `GET` | `/transactions/{transaction_id}` | Read one transaction | `200` |
| `POST` | `/transfers` | Atomic account-to-account transfer (bonus) | `201` |
| `GET` | `/transfers/{transfer_id}` | Read a transfer | `200` |
| `GET` | `/health` | Health check | `200` |

There are deliberately **no** `PUT`/`PATCH`/`DELETE` routes for transactions – they answer `405` and the
database rejects such statements too.

### Error format

Every error uses the same envelope, so clients only need one handler:

```json
{
  "error": {
    "code": "INSUFFICIENT_BALANCE",
    "message": "Insufficient balance: account '7a3f…' has 100.00 but 150.00 was requested. The transaction was rejected and the balance is unchanged.",
    "details": { "account_id": "7a3f…", "balance": "100.00", "requested_amount": "150.00" }
  }
}
```

| HTTP | `code` | When |
| --- | --- | --- |
| `400` | `VALIDATION_ERROR` | Invalid body/query/path: missing name, unknown fields (e.g. `balance`), bad `type`, `amount <= 0`, more than 2 decimals, missing `reference_id`, invalid pagination, malformed JSON. `details.fields[]` lists each problem. |
| `404` | `ACCOUNT_NOT_FOUND` / `TRANSACTION_NOT_FOUND` / `TRANSFER_NOT_FOUND` / `NOT_FOUND` | Unknown resource or route |
| `405` | `METHOD_NOT_ALLOWED` | Attempt to modify or delete a transaction |
| `409` | `DUPLICATE_TRANSACTION` | `reference_id` already used for that account – `details.existing_transaction` holds the original |
| `422` | `INSUFFICIENT_BALANCE` | Debit (or transfer) larger than the available balance |
| `422` | `SAME_ACCOUNT_TRANSFER` | Transfer where source and destination are the same account |
| `500` | `INTERNAL_ERROR` | Unexpected failure – nothing was persisted |

---

## Requirement coverage

| Brief | Implementation |
| --- | --- |
| **1. Create account** – unique id, required name, balance 0, server timestamp, client cannot set balance | UUID `id`, `name` required & non-blank, `balance` fixed to `0.00`, `created_at` in UTC; unknown fields such as `balance` → `400`. |
| **2. Add transaction** – credit / debit, sufficient balance, duplicate `reference_id` per account | Credit adds, debit subtracts, overdraft → `422` and balance unchanged; `UNIQUE(account_id, reference_id)` + pre-check → `409` with the existing transaction; same `reference_id` is fine on another account. |
| **3. Get balance** – `account_id`, `balance`, 404 for unknown account | `GET /accounts/{id}/balance` → `{account_id, name, balance}`; `404 ACCOUNT_NOT_FOUND`. |
| **4. History** – filter by type, pagination with page/limit + metadata, latest-first | `type=credit|debit` (omit for both); `page`/`limit` with `page`, `limit`, `total_records`, `total_pages`, `has_next`, `has_previous`; `ORDER BY created_at DESC, seq DESC`. |
| **5. Data integrity** – immutability, balance consistency, failed transactions have no effect, atomicity | No mutating routes (`405`) + DB triggers; balance updated in the same DB transaction as the insert; rejected requests change nothing; worked example `0 +100 +50 −30 −20 = 100` is a test. |
| **7. Validations** | Pydantic schemas (`extra="forbid"`) + service checks + database constraints. |
| **8. Error handling** | Consistent envelope and status codes (table above). |
| **9. Bonus: transfer** | `POST /transfers`: both accounts locked, debit + credit + transfer record in one DB transaction; insufficient balance / unknown account / same account / duplicate reference → rolled back, nothing persisted. |
| **10. Technical** | FastAPI + SQLAlchemy; SQLite or PostgreSQL; constraints: `CHECK balance >= 0`, `CHECK amount > 0`, `CHECK type IN (...)`, `UNIQUE(account_id, reference_id)`, foreign keys, immutability triggers; runnable locally via this README. |

---

## Design and data integrity

**Money is stored as exact integers.** Amounts are accepted as JSON numbers or strings with at most two
decimal places and are stored in minor units (`150.00` → `15000`) in `BIGINT` columns. Integers are exact
in every database, so `0.1 + 0.2` style errors cannot creep into balances or into the
`balance_minor >= 0` check constraint. Responses render money as fixed two-decimal strings (`"150.00"`)
so clients never parse floats.

**One request = one database transaction.** `post_transaction()` and `transfer()` run entirely inside a
single `with session.begin():` block: lock the account row(s), check the duplicate / balance rules, insert
the transaction row(s), update the balance(s), commit. Any failure – business rule, constraint violation or
an unexpected exception – rolls back everything, so a balance can never change without its transaction
being recorded (or vice versa).

**Concurrency is handled at the database.**

* PostgreSQL: account rows are locked with `SELECT … FOR UPDATE`; transfers lock both accounts in sorted
  order so two opposite transfers cannot deadlock.
* SQLite: every transaction starts with `BEGIN IMMEDIATE`, which takes the write lock *before* the balance
  is read. Competing writers wait (30 s busy timeout) instead of racing, so the classic lost-update problem
  cannot occur. WAL mode keeps readers unblocked.
* Database constraints are the last line of defence: should a race ever slip past the application checks,
  the `UNIQUE` / `CHECK` constraints reject it and the violation is translated into the same `409` / `422`
  response.

**Idempotency.** `reference_id` is the client's idempotency key, unique per account. Re-submitting it
returns `409 DUPLICATE_TRANSACTION` together with the original transaction so the client can reconcile.
Only *successful* transactions consume a `reference_id`; a rejected debit can be retried with the same key.

**Immutability.** Transactions have no update/delete endpoints (`405`) and `BEFORE UPDATE/DELETE`
triggers make the tables append-only even for direct SQL. Transfers are recorded as two linked immutable
transactions plus a `transfers` row.

**Ordering.** History is sorted by `created_at DESC`, with a monotonically increasing internal sequence as
tie-breaker, so the order is deterministic even for transactions created in the same microsecond. A
composite index on `(account_id, created_at, seq)` serves the query.

**Validation layers.** Pydantic rejects malformed input (including unknown fields) with field-level
messages; the service layer enforces business rules; the database enforces invariants. Each layer only
trusts the ones above it as an optimisation, not as a guarantee.

---

## Project structure

```
app/
├── main.py              # FastAPI app factory, lifespan (schema creation), router registration
├── config.py            # Settings from environment / .env
├── database.py          # Engine + session factory; SQLite BEGIN IMMEDIATE / WAL / foreign keys
├── models.py            # SQLAlchemy models, constraints, indexes, immutability triggers
├── schemas.py           # Pydantic request / response models
├── money.py             # Decimal <-> minor-unit conversion
├── errors.py            # Domain errors + the single error envelope / exception handlers
├── dependencies.py      # FastAPI dependencies (DB session, settings)
├── routers/
│   ├── accounts.py      # /accounts, balance, nested transactions + history
│   ├── transactions.py  # /transactions (flat create, read one)
│   ├── transfers.py     # /transfers
│   └── common.py        # Shared OpenAPI error examples
└── services/
    └── ledger.py        # All business logic and transaction boundaries
tests/                   # pytest suite (accounts, transactions, history, integrity, transfers)
docs/                    # API.md, openapi.json, postman_collection.json
scripts/                 # demo.sh (curl walkthrough), export_openapi.py
Dockerfile, docker-compose.yml, .github/workflows/ci.yml
```

---

## Assumptions and possible next steps

All interpretation decisions are listed in **[ASSUMPTIONS.md](ASSUMPTIONS.md)** (money representation,
status-code choices, pagination semantics, transfer `reference_id` behaviour, single currency, …).

Natural next steps for a production deployment, intentionally out of scope for a 3–4 hour assignment:
Alembic migrations instead of `create_all`, authentication/authorisation and per-account access control,
multi-currency support, rate limiting, structured request logging and metrics, and a reconciliation job
that periodically asserts `balance == Σ credits − Σ debits` for every account (the helper already exists in
`services/ledger.py` and is exercised by the tests).

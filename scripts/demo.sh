#!/usr/bin/env bash
# End-to-end walkthrough of the API using curl.
#
#   1. start the server:  uvicorn app.main:app --reload
#   2. run this script:   ./scripts/demo.sh            (optionally: BASE_URL=http://localhost:8000)
#
# Requires `curl`; uses `python3` only to pretty-print JSON and extract ids.
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"

pretty() { python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin), indent=2))'; }
field()  { python3 -c "import json,sys; print(json.load(sys.stdin)$1)"; }
step()   { printf '\n\033[1;34m== %s\033[0m\n' "$1"; }

step "Create two accounts"
ALICE_JSON=$(curl -s -X POST "$BASE_URL/accounts" -H 'Content-Type: application/json' -d '{"name": "Alice"}')
BOB_JSON=$(curl -s -X POST "$BASE_URL/accounts" -H 'Content-Type: application/json' -d '{"name": "Bob"}')
echo "$ALICE_JSON" | pretty
ALICE=$(echo "$ALICE_JSON" | field '["id"]')
BOB=$(echo "$BOB_JSON" | field '["id"]')

step "Client tries to set the initial balance -> 400 VALIDATION_ERROR"
curl -s -X POST "$BASE_URL/accounts" -H 'Content-Type: application/json' -d '{"name": "Rich", "balance": 1000}' | pretty

step "Credit 100 and 50, debit 30 and 20 -> balance 100.00"
for payload in \
  '{"type": "credit", "amount": 100, "description": "Salary", "reference_id": "SAL-001"}' \
  '{"type": "credit", "amount": 50,  "description": "Bonus",  "reference_id": "BON-001"}' \
  '{"type": "debit",  "amount": 30,  "description": "Groceries", "reference_id": "GRO-001"}' \
  '{"type": "debit",  "amount": 20,  "description": "Fuel", "reference_id": "FUEL-001"}'; do
  curl -s -X POST "$BASE_URL/accounts/$ALICE/transactions" -H 'Content-Type: application/json' -d "$payload" \
    | field '["balance_after"]'
done
curl -s "$BASE_URL/accounts/$ALICE/balance" | pretty

step "Debit 150 with balance 100 -> 422 INSUFFICIENT_BALANCE (balance unchanged)"
curl -s -X POST "$BASE_URL/accounts/$ALICE/transactions" -H 'Content-Type: application/json' \
  -d '{"type": "debit", "amount": 150, "reference_id": "BIG-001"}' | pretty

step "Re-submit reference_id SAL-001 -> 409 DUPLICATE_TRANSACTION (nothing created)"
curl -s -X POST "$BASE_URL/accounts/$ALICE/transactions" -H 'Content-Type: application/json' \
  -d '{"type": "credit", "amount": 100, "reference_id": "SAL-001"}' | pretty

step "Same reference_id on a different account is fine"
curl -s -X POST "$BASE_URL/accounts/$BOB/transactions" -H 'Content-Type: application/json' \
  -d '{"type": "credit", "amount": 50, "reference_id": "SAL-001"}' | field '["balance_after"]'

step "History: latest first, filtered to debits, page 1 of size 10"
curl -s "$BASE_URL/accounts/$ALICE/transactions?type=debit&page=1&limit=10" | pretty

step "Transactions are immutable -> 405"
TXN=$(curl -s "$BASE_URL/accounts/$ALICE/transactions?limit=1" | field '["data"][0]["id"]')
curl -s -X DELETE "$BASE_URL/transactions/$TXN" | pretty

step "Transfer 30 from Alice (100) to Bob (50) -> 70 / 80"
curl -s -X POST "$BASE_URL/transfers" -H 'Content-Type: application/json' \
  -d "{\"from_account_id\": \"$ALICE\", \"to_account_id\": \"$BOB\", \"amount\": 30, \"reference_id\": \"TRF-001\"}" | pretty

step "Final balances"
curl -s "$BASE_URL/accounts/$ALICE/balance" | pretty
curl -s "$BASE_URL/accounts/$BOB/balance" | pretty

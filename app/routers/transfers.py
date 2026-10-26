"""Money transfer endpoints (bonus requirement)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, status

from app import schemas
from app.dependencies import DbSession
from app.routers.common import ERROR_RESPONSES
from app.services import ledger

router = APIRouter(prefix="/transfers", tags=["Transfers"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=schemas.TransferResponse,
    summary="Transfer money between two accounts",
    description=(
        "Debits `from_account_id` and credits `to_account_id` for the same amount in **one atomic database "
        "transaction**. Two immutable transactions (one `debit`, one `credit`) are recorded and linked through "
        "`transfer_id`.\n\n"
        "* Both accounts must exist (`404 ACCOUNT_NOT_FOUND`).\n"
        "* The source must have sufficient balance (`422 INSUFFICIENT_BALANCE`).\n"
        "* Source and destination must differ (`422 SAME_ACCOUNT_TRANSFER`).\n"
        "* If anything fails, the whole transfer is rolled back - a partial transfer is impossible.\n"
        "* An optional `reference_id` makes the transfer idempotent per account (`409 DUPLICATE_TRANSACTION`)."
    ),
    responses={
        400: ERROR_RESPONSES[400],
        404: ERROR_RESPONSES[404],
        409: ERROR_RESPONSES[409],
        422: ERROR_RESPONSES[422],
    },
)
def create_transfer(payload: schemas.TransferCreate, db: DbSession) -> schemas.TransferResponse:
    return ledger.transfer(db, payload).to_schema()


@router.get(
    "/{transfer_id}",
    response_model=schemas.TransferResponse,
    summary="Get a transfer",
    responses={404: ERROR_RESPONSES[404]},
)
def get_transfer(
    transfer_id: Annotated[str, Path(description="Transfer identifier (UUID).", min_length=1)],
    db: DbSession,
) -> schemas.TransferResponse:
    return ledger.get_transfer(db, transfer_id).to_schema()

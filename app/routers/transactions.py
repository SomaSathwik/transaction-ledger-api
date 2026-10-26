"""Transaction endpoints that are not nested under an account."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, status

from app import schemas
from app.dependencies import DbSession
from app.routers.common import ERROR_RESPONSES
from app.services import ledger

router = APIRouter(prefix="/transactions", tags=["Transactions"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=schemas.TransactionCreatedResponse,
    summary="Add a transaction (account_id in body)",
    description=(
        "Alternative to `POST /accounts/{account_id}/transactions` where the `account_id` is part of the "
        "request body. Behaviour and validation rules are identical."
    ),
    responses={
        400: ERROR_RESPONSES[400],
        404: ERROR_RESPONSES[404],
        409: ERROR_RESPONSES[409],
        422: ERROR_RESPONSES[422],
    },
)
def add_transaction(payload: schemas.TransactionCreateWithAccount, db: DbSession) -> schemas.TransactionCreatedResponse:
    body = schemas.TransactionCreate(**payload.model_dump(exclude={"account_id"}))
    posted = ledger.post_transaction(db, payload.account_id, body)
    base = ledger.transaction_to_schema(posted.transaction)
    return schemas.TransactionCreatedResponse(
        **base.model_dump(), balance_after=ledger.format_money(posted.balance_after_minor)
    )


@router.get(
    "/{transaction_id}",
    response_model=schemas.TransactionResponse,
    summary="Get a single transaction",
    responses={404: ERROR_RESPONSES[404]},
)
def get_transaction(
    transaction_id: Annotated[str, Path(description="Transaction identifier (UUID).", min_length=1)],
    db: DbSession,
) -> schemas.TransactionResponse:
    return ledger.transaction_to_schema(ledger.get_transaction(db, transaction_id))

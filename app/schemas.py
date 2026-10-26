"""Pydantic request / response schemas.

Conventions
-----------
* Request bodies use ``extra="forbid"`` so that unexpected fields are rejected
  with a validation error instead of being silently ignored.  This is what
  stops a client from setting ``balance`` when creating an account.
* Monetary values are accepted as JSON numbers *or* numeric strings with up to
  two decimal places and are always returned as fixed two-decimal strings
  (``"150.00"``) to avoid floating point artefacts in clients.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models import TransactionType

# ---------------------------------------------------------------------------
# Reusable field types
# ---------------------------------------------------------------------------
Money = Annotated[
    Decimal,
    Field(
        gt=0,
        max_digits=15,
        decimal_places=2,
        description="Positive amount in major units with at most two decimal places.",
        examples=["150.00"],
    ),
]

NonBlankStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

ReferenceId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
    Field(
        description="Client supplied idempotency key. Must be unique per account.",
        examples=["INV-2024-0001"],
    ),
]

DescriptionStr = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
Description = Annotated[
    DescriptionStr | None,
    Field(default=None, description="Free text description.", examples=["Salary for October"]),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------
class AccountCreate(StrictModel):
    name: Annotated[
        NonBlankStr,
        StringConstraints(max_length=100),
        Field(description="Account holder name.", examples=["Alice Sharma"]),
    ]


class AccountResponse(BaseModel):
    id: str = Field(examples=["7a3f2a3e-5e4c-4b3a-9d2f-1c2b3a4d5e6f"])
    name: str = Field(examples=["Alice Sharma"])
    balance: str = Field(description="Current balance as a two-decimal string.", examples=["150.00"])
    created_at: datetime


class BalanceResponse(BaseModel):
    account_id: str
    name: str
    balance: str = Field(examples=["150.00"])


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------
class TransactionCreate(StrictModel):
    """Body for ``POST /accounts/{account_id}/transactions``."""

    type: TransactionType = Field(description="`credit` increases the balance, `debit` decreases it.")
    amount: Money
    description: Description = None
    reference_id: ReferenceId


class TransactionCreateWithAccount(TransactionCreate):
    """Body for ``POST /transactions`` (account_id supplied in the body)."""

    account_id: NonBlankStr = Field(description="ID of the account to post the transaction to.")


class TransactionResponse(BaseModel):
    id: str
    account_id: str
    type: TransactionType
    amount: str = Field(examples=["50.00"])
    description: str | None
    reference_id: str
    transfer_id: str | None = Field(default=None, description="Set when the transaction is one leg of a transfer.")
    created_at: datetime


class TransactionCreatedResponse(TransactionResponse):
    balance_after: str = Field(
        description="Account balance immediately after this transaction was applied.", examples=["150.00"]
    )


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------
class PaginationMeta(BaseModel):
    page: int = Field(description="Current page (1-based).", examples=[1])
    limit: int = Field(description="Page size that was applied.", examples=[20])
    total_records: int = Field(description="Total number of matching transactions.", examples=[42])
    total_pages: int = Field(description="Total number of pages (0 when there are no records).", examples=[3])
    has_next: bool
    has_previous: bool


class TransactionListResponse(BaseModel):
    data: list[TransactionResponse]
    pagination: PaginationMeta


# ---------------------------------------------------------------------------
# Transfers
# ---------------------------------------------------------------------------
class TransferCreate(StrictModel):
    from_account_id: NonBlankStr = Field(description="Account to debit.")
    to_account_id: NonBlankStr = Field(description="Account to credit.")
    amount: Money
    description: Description = None
    reference_id: Annotated[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] | None,
        Field(
            default=None,
            description=(
                "Optional idempotency key. It is applied to both legs of the transfer and must be unique "
                "per account. When omitted the server generates one."
            ),
            examples=["TRF-2024-0001"],
        ),
    ] = None


class TransferResponse(BaseModel):
    id: str
    from_account_id: str
    to_account_id: str
    amount: str = Field(examples=["30.00"])
    description: str | None
    reference_id: str
    debit_transaction: TransactionResponse
    credit_transaction: TransactionResponse
    from_account_balance: str = Field(description="Source balance after the transfer.", examples=["70.00"])
    to_account_balance: str = Field(description="Destination balance after the transfer.", examples=["80.00"])
    created_at: datetime


# ---------------------------------------------------------------------------
# Errors (for the OpenAPI documentation)
# ---------------------------------------------------------------------------
class ErrorBody(BaseModel):
    code: str = Field(examples=["INSUFFICIENT_BALANCE"])
    message: str = Field(examples=["Insufficient balance: account 'x' has 100.00 but 150.00 was requested."])
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class HealthResponse(BaseModel):
    status: str = Field(examples=["ok"])
    database: str = Field(examples=["sqlite"])
    version: str = Field(examples=["1.0.0"])

"""Domain errors and the single, consistent error envelope used by the API.

Every error response has the shape::

    {
      "error": {
        "code": "INSUFFICIENT_BALANCE",
        "message": "Human readable explanation",
        "details": { ... optional, machine readable context ... }
      }
    }

HTTP status mapping
-------------------
400  VALIDATION_ERROR       malformed body / query / path parameters
404  *_NOT_FOUND            unknown account, transaction or transfer
405  METHOD_NOT_ALLOWED     e.g. PUT/PATCH/DELETE on immutable transactions
409  DUPLICATE_TRANSACTION  reference_id already used for this account
422  INSUFFICIENT_BALANCE   well-formed request rejected by a business rule
422  SAME_ACCOUNT_TRANSFER
500  INTERNAL_ERROR         unexpected failure (nothing was persisted)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class LedgerError(Exception):
    """Base class for all business errors raised by the service layer."""

    status_code: int = 400
    code: str = "LEDGER_ERROR"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def to_payload(self) -> dict[str, Any]:
        return error_payload(self.code, self.message, self.details)


class AccountNotFoundError(LedgerError):
    status_code = 404
    code = "ACCOUNT_NOT_FOUND"

    def __init__(self, account_id: str, *, role: str | None = None) -> None:
        label = f"{role} account" if role else "Account"
        super().__init__(f"{label} '{account_id}' was not found.", details={"account_id": account_id})


class TransactionNotFoundError(LedgerError):
    status_code = 404
    code = "TRANSACTION_NOT_FOUND"

    def __init__(self, transaction_id: str) -> None:
        super().__init__(f"Transaction '{transaction_id}' was not found.", details={"transaction_id": transaction_id})


class TransferNotFoundError(LedgerError):
    status_code = 404
    code = "TRANSFER_NOT_FOUND"

    def __init__(self, transfer_id: str) -> None:
        super().__init__(f"Transfer '{transfer_id}' was not found.", details={"transfer_id": transfer_id})


class DuplicateTransactionError(LedgerError):
    status_code = 409
    code = "DUPLICATE_TRANSACTION"

    def __init__(self, account_id: str, reference_id: str, *, existing: dict[str, Any] | None = None) -> None:
        details: dict[str, Any] = {"account_id": account_id, "reference_id": reference_id}
        if existing is not None:
            details["existing_transaction"] = existing
        super().__init__(
            f"A transaction with reference_id '{reference_id}' already exists for account '{account_id}'. "
            "No new transaction was created and the balance is unchanged.",
            details=details,
        )


class InsufficientBalanceError(LedgerError):
    status_code = 422
    code = "INSUFFICIENT_BALANCE"

    def __init__(self, account_id: str, balance: str, requested: str) -> None:
        super().__init__(
            f"Insufficient balance: account '{account_id}' has {balance} but {requested} was requested. "
            "The transaction was rejected and the balance is unchanged.",
            details={"account_id": account_id, "balance": balance, "requested_amount": requested},
        )


class SameAccountTransferError(LedgerError):
    status_code = 422
    code = "SAME_ACCOUNT_TRANSFER"

    def __init__(self, account_id: str) -> None:
        super().__init__("Source and destination accounts must be different.", details={"account_id": account_id})


class ValidationFailedError(LedgerError):
    """Raised by the service layer for semantic validation not expressible in schemas."""

    status_code = 400
    code = "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# Envelope helpers and FastAPI exception handlers
# ---------------------------------------------------------------------------
def error_payload(code: str, message: str, details: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        body["details"] = details
    return {"error": body}


def _format_location(loc: tuple[Any, ...]) -> str:
    parts = [str(part) for part in loc]
    return ".".join(parts) if parts else "request"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(LedgerError)
    async def _ledger_error_handler(_request: Request, exc: LedgerError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = []
        for err in exc.errors():
            loc = tuple(err.get("loc", ()))
            err_type = err.get("type", "value_error")
            if err_type == "json_invalid":
                # The location of a JSON syntax error is a byte offset, not a field.
                loc = ("body",)
            fields.append(
                {
                    "field": _format_location(loc),
                    "message": err.get("msg", "Invalid value"),
                    "type": err_type,
                }
            )
        return JSONResponse(
            status_code=400,
            content=error_payload(
                "VALIDATION_ERROR",
                "The request is invalid. See 'details.fields' for the individual problems.",
                {"fields": fields},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 405:
            message = f"Method {request.method} is not allowed on {request.url.path}."
            if "/transactions" in request.url.path:
                message += " Transactions are immutable: they cannot be edited or deleted."
            payload = error_payload("METHOD_NOT_ALLOWED", message)
        elif exc.status_code == 404:
            payload = error_payload("NOT_FOUND", f"No route matches {request.method} {request.url.path}.")
        else:
            payload = error_payload("HTTP_ERROR", str(exc.detail))
        headers = getattr(exc, "headers", None)
        return JSONResponse(status_code=exc.status_code, content=payload, headers=headers)

    @app.exception_handler(Exception)
    async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error while processing %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=error_payload(
                "INTERNAL_ERROR",
                "An unexpected error occurred. No changes were persisted.",
            ),
        )

"""Shared OpenAPI response documentation and small router helpers."""

from __future__ import annotations

from typing import Any

from app import schemas
from app.errors import ValidationFailedError


def _example(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        body["details"] = details
    return {"error": body}


ERROR_RESPONSES: dict[int, dict[str, Any]] = {
    400: {
        "model": schemas.ErrorResponse,
        "description": "Invalid request body, path or query parameters.",
        "content": {
            "application/json": {
                "example": _example(
                    "VALIDATION_ERROR",
                    "The request is invalid. See 'details.fields' for the individual problems.",
                    {
                        "fields": [
                            {
                                "field": "body.amount",
                                "message": "Input should be greater than 0",
                                "type": "greater_than",
                            }
                        ]
                    },
                )
            }
        },
    },
    404: {
        "model": schemas.ErrorResponse,
        "description": "The referenced resource does not exist.",
        "content": {
            "application/json": {
                "example": _example(
                    "ACCOUNT_NOT_FOUND",
                    "Account 'f2c1…' was not found.",
                    {"account_id": "f2c1…"},
                )
            }
        },
    },
    409: {
        "model": schemas.ErrorResponse,
        "description": "A transaction with the same reference_id already exists for this account.",
        "content": {
            "application/json": {
                "example": _example(
                    "DUPLICATE_TRANSACTION",
                    "A transaction with reference_id 'INV-1' already exists for account 'f2c1…'. "
                    "No new transaction was created and the balance is unchanged.",
                    {"account_id": "f2c1…", "reference_id": "INV-1", "existing_transaction": {"id": "…"}},
                )
            }
        },
    },
    422: {
        "model": schemas.ErrorResponse,
        "description": "The request was well-formed but violates a business rule.",
        "content": {
            "application/json": {
                "example": _example(
                    "INSUFFICIENT_BALANCE",
                    "Insufficient balance: account 'f2c1…' has 100.00 but 150.00 was requested. "
                    "The transaction was rejected and the balance is unchanged.",
                    {"account_id": "f2c1…", "balance": "100.00", "requested_amount": "150.00"},
                )
            }
        },
    },
}


def validation_error(message: str, *, field: str) -> ValidationFailedError:
    """Build a ``VALIDATION_ERROR`` with the same shape as schema validation failures."""
    return ValidationFailedError(
        "The request is invalid. See 'details.fields' for the individual problems.",
        details={"fields": [{"field": field, "message": message, "type": "value_error"}]},
    )

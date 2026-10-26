"""Ledger business logic.

Every public function receives a SQLAlchemy ``Session`` and is responsible for
its own transaction boundary.  Writes always happen inside a single
``with session.begin():`` block, so creating a transaction row and updating
the account balance either both succeed or both roll back.

Concurrency model
-----------------
* Account rows are locked with ``SELECT ... FOR UPDATE`` (PostgreSQL & friends).
  SQLite does not support row locks, but ``database.py`` starts every
  transaction with ``BEGIN IMMEDIATE`` which serialises writers database-wide.
* Database constraints (unique ``(account_id, reference_id)``, ``balance >= 0``)
  act as the final safety net.  If a race ever slips past the application
  checks the database rejects it, the transaction is rolled back and the error
  is translated into the same domain error the pre-check would have raised.
* For transfers both accounts are locked in a deterministic order (sorted by
  id) so two opposite transfers running concurrently cannot deadlock.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import schemas
from app.errors import (
    AccountNotFoundError,
    DuplicateTransactionError,
    InsufficientBalanceError,
    SameAccountTransferError,
    TransactionNotFoundError,
    TransferNotFoundError,
)
from app.models import Account, Transaction, TransactionType, Transfer
from app.money import format_money, to_minor

# ---------------------------------------------------------------------------
# Serialisation helpers (ORM -> API schema)
# ---------------------------------------------------------------------------


def account_to_schema(account: Account) -> schemas.AccountResponse:
    return schemas.AccountResponse(
        id=account.id,
        name=account.name,
        balance=format_money(account.balance_minor),
        created_at=account.created_at,
    )


def transaction_to_schema(txn: Transaction) -> schemas.TransactionResponse:
    return schemas.TransactionResponse(
        id=txn.id,
        account_id=txn.account_id,
        type=TransactionType(txn.type),
        amount=format_money(txn.amount_minor),
        description=txn.description,
        reference_id=txn.reference_id,
        transfer_id=txn.transfer_id,
        created_at=txn.created_at,
    )


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------


def create_account(session: Session, data: schemas.AccountCreate) -> Account:
    """Create an account.  The balance always starts at zero."""
    account = Account(name=data.name, balance_minor=0)
    with session.begin():
        session.add(account)
    return account


def get_account(session: Session, account_id: str) -> Account:
    account = session.scalar(select(Account).where(Account.id == account_id))
    if account is None:
        raise AccountNotFoundError(account_id)
    return account


def _lock_account(session: Session, account_id: str, *, role: str | None = None) -> Account:
    """Load an account inside the current transaction, locking its row for update."""
    account = session.scalar(select(Account).where(Account.id == account_id).with_for_update())
    if account is None:
        raise AccountNotFoundError(account_id, role=role)
    return account


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PostedTransaction:
    transaction: Transaction
    balance_after_minor: int


def _find_existing(session: Session, account_id: str, reference_id: str) -> Transaction | None:
    return session.scalar(
        select(Transaction).where(
            Transaction.account_id == account_id,
            Transaction.reference_id == reference_id,
        )
    )


def _apply_to_balance(account: Account, txn_type: TransactionType, amount_minor: int) -> None:
    """Mutate the (locked) account balance, enforcing the no-overdraft rule."""
    if txn_type is TransactionType.CREDIT:
        account.balance_minor += amount_minor
        return

    if account.balance_minor < amount_minor:
        raise InsufficientBalanceError(
            account.id,
            balance=format_money(account.balance_minor),
            requested=format_money(amount_minor),
        )
    account.balance_minor -= amount_minor


def post_transaction(session: Session, account_id: str, data: schemas.TransactionCreate) -> PostedTransaction:
    """Create a credit/debit transaction and update the balance atomically.

    Raises
    ------
    AccountNotFoundError      – unknown account
    DuplicateTransactionError – ``reference_id`` already used for this account
    InsufficientBalanceError  – debit larger than the current balance
    """
    amount_minor = to_minor(data.amount)

    try:
        with session.begin():
            account = _lock_account(session, account_id)

            existing = _find_existing(session, account_id, data.reference_id)
            if existing is not None:
                raise DuplicateTransactionError(
                    account_id,
                    data.reference_id,
                    existing=transaction_to_schema(existing).model_dump(mode="json"),
                )

            _apply_to_balance(account, data.type, amount_minor)

            txn = Transaction(
                account_id=account.id,
                type=data.type.value,
                amount_minor=amount_minor,
                description=data.description,
                reference_id=data.reference_id,
            )
            session.add(txn)
            session.flush()
            balance_after = account.balance_minor
    except IntegrityError as exc:
        # Safety net for races the pre-checks could not see.  The transaction
        # has already been rolled back by the context manager.
        _raise_domain_error_for(session, exc, account_id, data.reference_id, amount_minor)

    return PostedTransaction(transaction=txn, balance_after_minor=balance_after)


def _raise_domain_error_for(
    session: Session, exc: IntegrityError, account_id: str, reference_id: str, amount_minor: int
) -> None:
    """Translate a database constraint violation into the matching domain error.

    Re-raises the original ``IntegrityError`` when the violation is not one we
    know how to explain (which would surface as a 500 - nothing was persisted).
    """
    message = str(exc.orig).lower()
    if "uq_transactions_account_reference" in message or "unique" in message:
        existing = _find_existing(session, account_id, reference_id)
        session.rollback()
        raise DuplicateTransactionError(
            account_id,
            reference_id,
            existing=transaction_to_schema(existing).model_dump(mode="json") if existing else None,
        ) from exc
    if "ck_accounts_balance_non_negative" in message or "check" in message:
        account = session.scalar(select(Account).where(Account.id == account_id))
        session.rollback()
        balance = format_money(account.balance_minor) if account else "0.00"
        raise InsufficientBalanceError(account_id, balance=balance, requested=format_money(amount_minor)) from exc
    raise exc


def get_transaction(session: Session, transaction_id: str) -> Transaction:
    txn = session.scalar(select(Transaction).where(Transaction.id == transaction_id))
    if txn is None:
        raise TransactionNotFoundError(transaction_id)
    return txn


@dataclass(frozen=True)
class TransactionPage:
    items: list[Transaction]
    page: int
    limit: int
    total_records: int

    @property
    def total_pages(self) -> int:
        return math.ceil(self.total_records / self.limit) if self.total_records else 0

    def to_schema(self) -> schemas.TransactionListResponse:
        total_pages = self.total_pages
        return schemas.TransactionListResponse(
            data=[transaction_to_schema(t) for t in self.items],
            pagination=schemas.PaginationMeta(
                page=self.page,
                limit=self.limit,
                total_records=self.total_records,
                total_pages=total_pages,
                has_next=self.page < total_pages,
                has_previous=self.page > 1,
            ),
        )


def list_transactions(
    session: Session,
    account_id: str,
    *,
    txn_type: TransactionType | None,
    page: int,
    limit: int,
) -> TransactionPage:
    """Return one page of an account's transactions, latest first."""
    get_account(session, account_id)  # 404 if unknown

    filters = [Transaction.account_id == account_id]
    if txn_type is not None:
        filters.append(Transaction.type == txn_type.value)

    total = session.scalar(select(func.count()).select_from(Transaction).where(*filters)) or 0

    items = list(
        session.scalars(
            select(Transaction)
            .where(*filters)
            .order_by(Transaction.created_at.desc(), Transaction.seq.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
    )
    return TransactionPage(items=items, page=page, limit=limit, total_records=total)


def compute_balance_from_history(session: Session, account_id: str) -> Decimal:
    """Recompute the balance purely from the transaction log (used for consistency checks)."""
    credits = session.scalar(
        select(func.coalesce(func.sum(Transaction.amount_minor), 0)).where(
            Transaction.account_id == account_id, Transaction.type == TransactionType.CREDIT.value
        )
    )
    debits = session.scalar(
        select(func.coalesce(func.sum(Transaction.amount_minor), 0)).where(
            Transaction.account_id == account_id, Transaction.type == TransactionType.DEBIT.value
        )
    )
    return Decimal(int(credits) - int(debits)) / 100


# ---------------------------------------------------------------------------
# Transfers (bonus)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompletedTransfer:
    transfer: Transfer
    debit: Transaction
    credit: Transaction
    from_balance_minor: int
    to_balance_minor: int

    def to_schema(self) -> schemas.TransferResponse:
        return schemas.TransferResponse(
            id=self.transfer.id,
            from_account_id=self.transfer.from_account_id,
            to_account_id=self.transfer.to_account_id,
            amount=format_money(self.transfer.amount_minor),
            description=self.transfer.description,
            reference_id=self.transfer.reference_id,
            debit_transaction=transaction_to_schema(self.debit),
            credit_transaction=transaction_to_schema(self.credit),
            from_account_balance=format_money(self.from_balance_minor),
            to_account_balance=format_money(self.to_balance_minor),
            created_at=self.transfer.created_at,
        )


def transfer(session: Session, data: schemas.TransferCreate) -> CompletedTransfer:
    """Move money between two accounts as one atomic unit of work.

    Either both the debit and the credit are persisted or neither is.
    """
    if data.from_account_id == data.to_account_id:
        raise SameAccountTransferError(data.from_account_id)

    amount_minor = to_minor(data.amount)
    reference_id = data.reference_id or f"TRF-{uuid.uuid4()}"

    try:
        with session.begin():
            # Lock both accounts in a stable order to avoid deadlocks.
            accounts: dict[str, Account] = {}
            for acc_id in sorted({data.from_account_id, data.to_account_id}):
                role = "Source" if acc_id == data.from_account_id else "Destination"
                accounts[acc_id] = _lock_account(session, acc_id, role=role)
            source, destination = accounts[data.from_account_id], accounts[data.to_account_id]

            for acc in (source, destination):
                existing = _find_existing(session, acc.id, reference_id)
                if existing is not None:
                    raise DuplicateTransactionError(
                        acc.id,
                        reference_id,
                        existing=transaction_to_schema(existing).model_dump(mode="json"),
                    )

            _apply_to_balance(source, TransactionType.DEBIT, amount_minor)
            _apply_to_balance(destination, TransactionType.CREDIT, amount_minor)

            record = Transfer(
                from_account_id=source.id,
                to_account_id=destination.id,
                amount_minor=amount_minor,
                description=data.description,
                reference_id=reference_id,
            )
            session.add(record)
            session.flush()  # assigns record.id / created_at

            debit = Transaction(
                account_id=source.id,
                type=TransactionType.DEBIT.value,
                amount_minor=amount_minor,
                description=data.description or f"Transfer to account {destination.id}",
                reference_id=reference_id,
                transfer_id=record.id,
                created_at=record.created_at,
            )
            credit = Transaction(
                account_id=destination.id,
                type=TransactionType.CREDIT.value,
                amount_minor=amount_minor,
                description=data.description or f"Transfer from account {source.id}",
                reference_id=reference_id,
                transfer_id=record.id,
                created_at=record.created_at,
            )
            session.add_all([debit, credit])
            session.flush()
            from_balance, to_balance = source.balance_minor, destination.balance_minor
    except IntegrityError as exc:
        _raise_domain_error_for(session, exc, data.from_account_id, reference_id, amount_minor)

    return CompletedTransfer(
        transfer=record,
        debit=debit,
        credit=credit,
        from_balance_minor=from_balance,
        to_balance_minor=to_balance,
    )


def get_transfer(session: Session, transfer_id: str) -> CompletedTransfer:
    record = session.scalar(select(Transfer).where(Transfer.id == transfer_id))
    if record is None:
        raise TransferNotFoundError(transfer_id)

    legs = {t.type: t for t in record.transactions}
    source = get_account(session, record.from_account_id)
    destination = get_account(session, record.to_account_id)
    return CompletedTransfer(
        transfer=record,
        debit=legs[TransactionType.DEBIT.value],
        credit=legs[TransactionType.CREDIT.value],
        from_balance_minor=source.balance_minor,
        to_balance_minor=destination.balance_minor,
    )

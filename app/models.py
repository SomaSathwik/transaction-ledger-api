"""SQLAlchemy models.

Design notes
------------
* Money is stored as **integer minor units** (e.g. paise / cents) in
  ``*_minor`` columns.  Integers are exact in every database, which keeps the
  ``balance_minor >= 0`` check constraint and all arithmetic free from floating
  point surprises.  The API converts to/from decimal strings (see ``money.py``).
* Every table has a hidden auto-incrementing ``seq`` primary key and a public
  UUID ``id``.  ``seq`` is a strictly increasing insertion counter and is used
  as a deterministic tie-breaker when ordering transactions "latest first".
* Data-integrity rules the database itself enforces:
    - ``accounts.balance_minor >= 0``                   (no overdrafts, ever)
    - ``transactions.amount_minor > 0``
    - ``transactions.type IN ('credit', 'debit')``
    - ``UNIQUE (account_id, reference_id)``             (idempotency per account)
    - ``transfers.from_account_id <> transfers.to_account_id``
    - foreign keys from transactions/transfers to accounts
    - ``transactions`` rows can never be UPDATEd or DELETEd (triggers)
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DDL,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    """Timezone-aware current UTC time."""
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    return str(uuid.uuid4())


class UTCDateTime(TypeDecorator):
    """Store datetimes in UTC and always hand back timezone-aware values.

    SQLite has no real timestamp type and silently drops timezone information;
    this decorator normalises everything to UTC on the way in and re-attaches
    the UTC timezone on the way out so the API always emits ISO-8601 values
    with an explicit offset.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: D401 - SQLAlchemy hook
        if value is None:
            return None
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class TransactionType(str, enum.Enum):
    CREDIT = "credit"
    DEBIT = "debit"


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint("balance_minor >= 0", name="ck_accounts_balance_non_negative"),
        CheckConstraint("length(trim(name)) > 0", name="ck_accounts_name_not_blank"),
    )

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    balance_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)

    transactions: Mapped[list[Transaction]] = relationship(back_populates="account")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Account id={self.id} name={self.name!r} balance_minor={self.balance_minor}>"


class Transfer(Base):
    __tablename__ = "transfers"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_transfers_amount_positive"),
        CheckConstraint("from_account_id <> to_account_id", name="ck_transfers_distinct_accounts"),
    )

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True, default=new_uuid)
    from_account_id: Mapped[str] = mapped_column(String(36), ForeignKey("accounts.id"), nullable=False)
    to_account_id: Mapped[str] = mapped_column(String(36), ForeignKey("accounts.id"), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reference_id: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)

    transactions: Mapped[list[Transaction]] = relationship(back_populates="transfer")


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("account_id", "reference_id", name="uq_transactions_account_reference"),
        CheckConstraint("amount_minor > 0", name="ck_transactions_amount_positive"),
        CheckConstraint("type IN ('credit', 'debit')", name="ck_transactions_type"),
        # Serves the "latest first" history listing per account.
        Index("ix_transactions_account_created", "account_id", "created_at", "seq"),
    )

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(String(36), ForeignKey("accounts.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(10), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reference_id: Mapped[str] = mapped_column(String(100), nullable=False)
    transfer_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("transfers.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)

    account: Mapped[Account] = relationship(back_populates="transactions")
    transfer: Mapped[Transfer | None] = relationship(back_populates="transactions")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Transaction id={self.id} {self.type} amount_minor={self.amount_minor} ref={self.reference_id!r}>"


# ---------------------------------------------------------------------------
# Immutability: transactions are append-only at the database level too.
# The API exposes no update/delete routes, and these triggers make sure that
# not even direct SQL can rewrite history.
# ---------------------------------------------------------------------------
_IMMUTABLE_MESSAGE = "transactions are immutable"

_sqlite_triggers = (
    DDL(
        f"""
        CREATE TRIGGER IF NOT EXISTS trg_transactions_no_update
        BEFORE UPDATE ON transactions
        BEGIN
            SELECT RAISE(ABORT, '{_IMMUTABLE_MESSAGE}');
        END;
        """
    ),
    DDL(
        f"""
        CREATE TRIGGER IF NOT EXISTS trg_transactions_no_delete
        BEFORE DELETE ON transactions
        BEGIN
            SELECT RAISE(ABORT, '{_IMMUTABLE_MESSAGE}');
        END;
        """
    ),
)

_postgresql_triggers = (
    DDL(
        f"""
        CREATE OR REPLACE FUNCTION prevent_transaction_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '{_IMMUTABLE_MESSAGE}';
        END;
        $$ LANGUAGE plpgsql;
        """
    ),
    DDL(
        """
        DROP TRIGGER IF EXISTS trg_transactions_immutable ON transactions;
        CREATE TRIGGER trg_transactions_immutable
        BEFORE UPDATE OR DELETE ON transactions
        FOR EACH ROW EXECUTE FUNCTION prevent_transaction_mutation();
        """
    ),
)

for _ddl in _sqlite_triggers:
    event.listen(Transaction.__table__, "after_create", _ddl.execute_if(dialect="sqlite"))
for _ddl in _postgresql_triggers:
    event.listen(Transaction.__table__, "after_create", _ddl.execute_if(dialect="postgresql"))

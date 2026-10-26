"""Database engine and session management.

The service layer treats *every* write as one database transaction
(``with session.begin(): ...``).  This module makes sure the underlying
database honours that contract:

* **SQLite** – foreign keys are switched on per connection, WAL journaling is
  enabled for file databases and every transaction is started with
  ``BEGIN IMMEDIATE``.  The latter takes the write lock *before* the balance is
  read, so two concurrent debits can never both observe the same "old" balance
  (the classic lost-update problem).  Writers simply wait for each other
  (``timeout``) instead of failing with "database is locked".
* **PostgreSQL** (or any other RDBMS) – the service layer additionally locks
  the affected account rows with ``SELECT ... FOR UPDATE``.
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _is_sqlite_memory(url: str) -> bool:
    return _is_sqlite(url) and (":memory:" in url or url.rstrip("/") in {"sqlite://", "sqlite:///"})


def create_db_engine(database_url: str, *, echo: bool = False) -> Engine:
    """Create a SQLAlchemy engine with the right settings for the given URL."""
    kwargs: dict = {"echo": echo}

    if _is_sqlite(database_url):
        # ``timeout`` makes a connection wait (instead of failing) when another
        # connection holds the write lock.
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if _is_sqlite_memory(database_url):
            # Share one in-memory database across all connections / threads.
            kwargs["poolclass"] = StaticPool

    engine = create_engine(database_url, **kwargs)

    if _is_sqlite(database_url):
        _configure_sqlite(engine, file_based=not _is_sqlite_memory(database_url))

    return engine


def _configure_sqlite(engine: Engine, *, file_based: bool) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, _connection_record) -> None:  # pragma: no cover - driver glue
        # Disable pysqlite's implicit transaction handling so that SQLAlchemy
        # (via the ``begin`` hook below) fully controls transaction boundaries.
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        if file_based:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _on_begin(connection) -> None:
        # Acquire the write lock up-front: serialises read-modify-write cycles.
        connection.exec_driver_sql("BEGIN IMMEDIATE")


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Build a session factory bound to ``engine``.

    ``expire_on_commit=False`` lets the API serialise ORM objects after the
    transaction has been committed without issuing extra SELECTs.
    """
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    """Create all tables (and the immutability triggers) if they do not exist."""
    from app import models  # noqa: F401  (registers the models on the metadata)

    models.Base.metadata.create_all(engine)


def session_scope(session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    """Yield a session and make sure it is closed afterwards (FastAPI dependency)."""
    session = session_factory()
    try:
        yield session
    finally:
        session.close()

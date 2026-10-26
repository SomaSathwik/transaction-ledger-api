"""Application configuration.

All settings can be provided through environment variables (or a local `.env`
file).  The defaults are chosen so that the API runs locally with zero setup:
a SQLite database file is created in the project directory on first start.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the Transaction Ledger API."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Transaction Ledger API"
    app_version: str = "1.0.0"

    # SQLAlchemy database URL.  Examples:
    #   sqlite:///./ledger.db                                    (default, zero setup)
    #   postgresql+psycopg://ledger:ledger@localhost:5432/ledger  (PostgreSQL)
    database_url: str = "sqlite:///./ledger.db"

    # Log SQL statements (useful while debugging).
    sql_echo: bool = False

    # Pagination defaults / limits for the transaction history endpoint.
    default_page_size: int = 20
    max_page_size: int = 100


def get_settings() -> Settings:
    """Build a fresh ``Settings`` instance from the environment."""
    return Settings()

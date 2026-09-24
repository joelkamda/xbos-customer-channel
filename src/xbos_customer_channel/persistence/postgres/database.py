from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol

import psycopg


class PostgresUnavailable(RuntimeError):
    """Fail-closed database boundary error."""


class ConnectionLike(Protocol):
    def __enter__(self) -> "ConnectionLike": ...
    def __exit__(self, exc_type: object, exc: object, tb: object) -> object: ...
    def cursor(self, *args: object, **kwargs: object) -> Any: ...


ConnectionFactory = Callable[[], ConnectionLike]


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    database_url: str

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "DatabaseConfig":
        source = os.environ if environ is None else environ
        value = source.get("XAFPAY_CUSTOMER_CHANNEL_DATABASE_URL", "").strip()
        if not value:
            raise ValueError(
                "missing_customer_channel_configuration:"
                "XAFPAY_CUSTOMER_CHANNEL_DATABASE_URL"
            )
        if not value.startswith(("postgresql://", "postgres://")):
            raise ValueError("invalid_customer_channel_database_url")
        return cls(_require_sslmode(value))


def _require_sslmode(url: str) -> str:
    lowered = url.lower()
    if "sslmode=" in lowered:
        if "sslmode=require" not in lowered:
            raise ValueError("customer_channel_database_sslmode_require")
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}sslmode=require"


def psycopg_connection_factory(config: DatabaseConfig) -> ConnectionFactory:
    """Return a lazy connection factory; constructing it performs no I/O."""

    def factory() -> ConnectionLike:
        try:
            return psycopg.connect(
                config.database_url,
                connect_timeout=8,
                autocommit=False,
            )
        except psycopg.Error as exc:
            raise PostgresUnavailable("customer_channel_postgres_unavailable") from exc

    return factory


@contextmanager
def transaction(factory: ConnectionFactory) -> Iterator[ConnectionLike]:
    """One explicit all-or-none transaction with fail-closed error mapping."""

    try:
        connection = factory()
    except PostgresUnavailable:
        raise
    except Exception as exc:
        raise PostgresUnavailable("customer_channel_postgres_unavailable") from exc

    try:
        with connection:
            yield connection
    except PostgresUnavailable:
        raise
    except psycopg.Error as exc:
        raise PostgresUnavailable("customer_channel_postgres_transaction_failed") from exc

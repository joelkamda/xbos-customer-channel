"""PostgreSQL durable state primitives for Customer Channel.

H1S3 materializes source only. Importing this package performs no connection
and does not compose durable storage into the live runtime.
"""

from .database import DatabaseConfig, PostgresUnavailable
from .schema import ACCEPTED_TABLES, STATE_SCHEMA_VERSION

__all__ = [
    "ACCEPTED_TABLES",
    "DatabaseConfig",
    "PostgresUnavailable",
    "STATE_SCHEMA_VERSION",
]

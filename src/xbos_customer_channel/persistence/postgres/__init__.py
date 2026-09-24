"""PostgreSQL durable state primitives for Customer Channel.

H1S3 materializes source only. Importing this package performs no connection
and does not compose durable storage into the live runtime.
"""

from .database import DatabaseConfig, PostgresUnavailable
from .schema import ACCEPTED_TABLES, STATE_SCHEMA_VERSION
from .w1_runtime_state_port import (
    DuplicateProviderMessage,
    PostgresW1RuntimeStatePort,
    ProviderEndpointMismatch,
    SecureRuntimeSessionRequired,
    W1DurableStateRejected,
)

__all__ = [
    "ACCEPTED_TABLES",
    "DatabaseConfig",
    "DuplicateProviderMessage",
    "PostgresUnavailable",
    "PostgresW1RuntimeStatePort",
    "ProviderEndpointMismatch",
    "STATE_SCHEMA_VERSION",
    "SecureRuntimeSessionRequired",
    "W1DurableStateRejected",
]

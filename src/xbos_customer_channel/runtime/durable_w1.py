from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..application.w1_composition import compose_real_xbos_boundaries
from ..persistence.postgres.database import (
    DatabaseConfig,
    psycopg_connection_factory,
)
from ..persistence.postgres.runtime_state_store import PostgresRuntimeStateStore
from ..persistence.postgres.security_state_stores import (
    PostgresCustomerSessionStore,
    PostgresIdentityBindingStore,
)
from ..persistence.postgres.serialization import LocatorKeyRing
from ..persistence.postgres.transport_state_stores import (
    PostgresIdempotencyResultStore,
    PostgresProviderMessageReceiptStore,
    PostgresTransportDeliveryStore,
)
from ..persistence.postgres.w1_runtime_state_port import PostgresW1RuntimeStatePort
from .config import RuntimeConfig


@dataclass(frozen=True, slots=True)
class DurableW1SessionRuntime:
    """Materialized durable W1/session state boundaries for the FastAPI app."""

    database_config: DatabaseConfig
    runtime_store: PostgresRuntimeStateStore
    session_store: PostgresCustomerSessionStore
    identity_binding_store: PostgresIdentityBindingStore
    receipt_store: PostgresProviderMessageReceiptStore
    idempotency_store: PostgresIdempotencyResultStore
    delivery_store: PostgresTransportDeliveryStore
    state_port: PostgresW1RuntimeStatePort


def compose_durable_w1_session_runtime(
    runtime_config: RuntimeConfig,
    *,
    environ: Mapping[str, str] | None = None,
) -> DurableW1SessionRuntime:
    """Compose accepted durable stores without opening a DB or calling XBOS."""

    database_config = DatabaseConfig.from_environment(environ)
    connection_factory = psycopg_connection_factory(database_config)

    runtime_store = PostgresRuntimeStateStore(connection_factory)
    session_store = PostgresCustomerSessionStore(connection_factory)
    identity_binding_store = PostgresIdentityBindingStore(connection_factory)
    receipt_store = PostgresProviderMessageReceiptStore(connection_factory)
    idempotency_store = PostgresIdempotencyResultStore(connection_factory)
    delivery_store = PostgresTransportDeliveryStore(connection_factory)

    locator_key_ring = LocatorKeyRing.from_environment(environ)
    xbos_context, xbos_catalog = compose_real_xbos_boundaries(runtime_config)

    state_port = PostgresW1RuntimeStatePort(
        runtime_store=runtime_store,
        session_store=session_store,
        identity_binding_store=identity_binding_store,
        receipt_store=receipt_store,
        idempotency_store=idempotency_store,
        locator_key_ring=locator_key_ring,
        delivery_store=delivery_store,
        xbos_context=xbos_context,
        xbos_catalog=xbos_catalog,
        configured_endpoint_ref=runtime_config.meta.phone_number_id,
    )

    return DurableW1SessionRuntime(
        database_config=database_config,
        runtime_store=runtime_store,
        session_store=session_store,
        identity_binding_store=identity_binding_store,
        receipt_store=receipt_store,
        idempotency_store=idempotency_store,
        delivery_store=delivery_store,
        state_port=state_port,
    )

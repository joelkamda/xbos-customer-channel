from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from threading import RLock

from ...application.w1_composition import (
    REAL_XBOS_ADAPTER_STATE,
    XBOSW1ContractUnavailable,
)
from ...application.w1_whatsapp_runtime import (
    W1RuntimeConversationState,
    W1RuntimeStatePort,
)
from ...ports import XBOSCatalogPort, XBOSContextPort
from ...transports.meta_whatsapp import NormalizedInboundMessage
from .runtime_state_store import PostgresRuntimeStateStore
from .schema import STATE_SCHEMA_VERSION
from .security_state_stores import (
    PostgresCustomerSessionStore,
    PostgresIdentityBindingStore,
)
from .serialization import (
    LocatorKeyRing,
    StateSerializationError,
    deserialize_runtime_state,
    serialize_runtime_state,
)
from .transport_state_stores import (
    DeliveryConflict,
    PostgresIdempotencyResultStore,
    PostgresProviderMessageReceiptStore,
)


CHANNEL_CODE = "whatsapp"
PROVIDER_CODE = "meta_whatsapp"
IDEMPOTENCY_SCOPE = "w1_runtime_transition"


class W1DurableStateRejected(RuntimeError):
    """Fail-closed composition boundary for durable W1 state."""


class ProviderEndpointMismatch(W1DurableStateRejected):
    pass


class DuplicateProviderMessage(W1DurableStateRejected):
    pass


class SecureRuntimeSessionRequired(W1DurableStateRejected):
    pass


@dataclass(frozen=True, slots=True)
class _PendingLoad:
    event_key: str
    runtime_row_version: int
    session: object
    receipt_ref: object
    receipt_row_version: int
    idempotency_ref: object
    idempotency_row_version: int


class PostgresW1RuntimeStatePort(W1RuntimeStatePort):
    """Compose the accepted H1S3 stores behind W1RuntimeStatePort.

    This adapter resolves existing secure W1 sessions only. It deliberately
    does not bootstrap conversations or sessions and performs no fallback to
    in-memory, fake, or stale XBOS state.
    """

    def __init__(
        self,
        *,
        runtime_store: PostgresRuntimeStateStore,
        session_store: PostgresCustomerSessionStore,
        identity_binding_store: PostgresIdentityBindingStore,
        receipt_store: PostgresProviderMessageReceiptStore,
        idempotency_store: PostgresIdempotencyResultStore,
        locator_key_ring: LocatorKeyRing,
        xbos_context: XBOSContextPort,
        xbos_catalog: XBOSCatalogPort,
        configured_endpoint_ref: str | None = None,
    ) -> None:
        configured_endpoint_ref = (
            configured_endpoint_ref
            or os.environ.get("META_WHATSAPP_PHONE_NUMBER_ID", "")
        ).strip()
        if not configured_endpoint_ref:
            raise ValueError("meta_whatsapp_phone_number_id_required")
        self._runtime_store = runtime_store
        self._session_store = session_store
        self._identity_binding_store = identity_binding_store
        self._receipt_store = receipt_store
        self._idempotency_store = idempotency_store
        self._locator_key_ring = locator_key_ring
        self._xbos_context = xbos_context
        self._xbos_catalog = xbos_catalog
        self._configured_endpoint_ref = configured_endpoint_ref
        self._pending: dict[str, _PendingLoad] = {}
        self._pending_lock = RLock()

    def load(self, inbound: NormalizedInboundMessage) -> W1RuntimeConversationState:
        self._assert_endpoint(inbound)
        now = datetime.now(timezone.utc)
        endpoint = inbound.metadata_phone_number_id
        raw_sender = inbound.sender.value

        conversation = self._runtime_store.resolve_active_conversation(
            channel_code=CHANNEL_CODE,
            provider_endpoint_ref=endpoint,
            raw_sender=raw_sender,
            key_ring=self._locator_key_ring,
            now_utc=now,
        )
        if conversation is None:
            raise XBOSW1ContractUnavailable(REAL_XBOS_ADAPTER_STATE)

        session = self._session_store.resolve_active_for_conversation(
            conversation.conversation_ref,
            now_utc=now,
        )
        if session is None:
            raise XBOSW1ContractUnavailable(REAL_XBOS_ADAPTER_STATE)

        binding = self._identity_binding_store.resolve_active_for_session_context(
            session,
            now_utc=now,
        )
        if binding is None:
            raise SecureRuntimeSessionRequired("active_identity_binding_required")

        secure_session = replace(session, security_binding_complete=True)
        persisted = self._runtime_store.load(secure_session.session_ref)
        if persisted.state_schema_version != STATE_SCHEMA_VERSION:
            raise StateSerializationError(
                "unsupported_runtime_state_schema_version"
            )

        attestation = self._reattest_context(secure_session)
        catalog_projection = self._xbos_catalog.get_catalog(
            merchant_ref=secure_session.merchant_ref or "",
            location_ref=secure_session.location_ref or "",
        )
        if (
            catalog_projection.merchant_ref != secure_session.merchant_ref
            or catalog_projection.location_ref != secure_session.location_ref
        ):
            raise SecureRuntimeSessionRequired("catalog_context_mismatch")

        state = deserialize_runtime_state(
            persisted.interaction_state_json,
            entry_projection=attestation.projection,
            catalog_projection=catalog_projection,
            security_binding_complete=True,
        )
        if state.session != secure_session:
            raise SecureRuntimeSessionRequired(
                "runtime_state_session_context_mismatch"
            )

        event_key = _event_key(inbound)
        sender_lookup_hash = self._locator_key_ring.current(
            channel_code=CHANNEL_CODE,
            provider_endpoint_ref=endpoint,
            raw_sender=raw_sender,
        ).digest
        fingerprint = _request_fingerprint(
            inbound,
            sender_lookup_hash=sender_lookup_hash,
        )
        purge_after = _purge_after(secure_session.expires_at_epoch)
        receipt, receipt_created = self._receipt_store.get_or_create(
            provider_code=PROVIDER_CODE,
            provider_endpoint_ref=endpoint,
            provider_message_ref=inbound.provider_message_ref,
            event_kind="message",
            conversation_ref=conversation.conversation_ref,
            sender_lookup_hash=sender_lookup_hash,
            occurred_at_utc=_from_epoch(inbound.occurred_at_epoch),
            payload_digest=fingerprint,
            normalized_action_code=_normalized_action_code(inbound),
            now_utc=now,
            purge_after_utc=purge_after,
        )
        idempotency, idempotency_created = self._idempotency_store.reserve(
            scope=IDEMPOTENCY_SCOPE,
            idempotency_key=event_key,
            request_fingerprint=fingerprint,
            conversation_ref=conversation.conversation_ref,
            session_ref=secure_session.session_ref,
            now_utc=now,
            purge_after_utc=purge_after,
        )

        if not receipt_created or not idempotency_created:
            if idempotency_created:
                self._idempotency_store.mark_unknown(
                    idempotency_result_ref=idempotency.idempotency_result_ref,
                    expected_row_version=idempotency.row_version,
                    now_utc=now,
                )
            if persisted.last_provider_message_ref == inbound.provider_message_ref:
                raise DuplicateProviderMessage(
                    "provider_message_already_applied"
                )
            raise DuplicateProviderMessage("provider_message_already_claimed")

        try:
            processing = self._receipt_store.begin_processing(
                receipt_ref=receipt.provider_message_receipt_ref,
                expected_row_version=receipt.row_version,
                now_utc=now,
            )
        except DeliveryConflict:
            self._idempotency_store.mark_unknown(
                idempotency_result_ref=idempotency.idempotency_result_ref,
                expected_row_version=idempotency.row_version,
                now_utc=now,
            )
            raise

        pending = _PendingLoad(
            event_key=event_key,
            runtime_row_version=persisted.row_version,
            session=secure_session,
            receipt_ref=processing.provider_message_receipt_ref,
            receipt_row_version=processing.row_version,
            idempotency_ref=idempotency.idempotency_result_ref,
            idempotency_row_version=idempotency.row_version,
        )
        with self._pending_lock:
            if event_key in self._pending:
                raise DuplicateProviderMessage("provider_message_pending_in_process")
            self._pending[event_key] = pending
        return state

    def save(
        self,
        inbound: NormalizedInboundMessage,
        state: W1RuntimeConversationState,
    ) -> None:
        self._assert_endpoint(inbound)
        event_key = _event_key(inbound)
        with self._pending_lock:
            pending = self._pending.get(event_key)
        if pending is None:
            raise W1DurableStateRejected("save_without_corresponding_load")

        try:
            if state.session != pending.session:
                raise SecureRuntimeSessionRequired(
                    "next_state_session_must_match_loaded_secure_session"
                )

            now = datetime.now(timezone.utc)
            document = serialize_runtime_state(state)
            if document.get("state_schema_version") != STATE_SCHEMA_VERSION:
                raise StateSerializationError(
                    "unsupported_runtime_state_schema_version"
                )
            purge_after = _purge_after(state.session.expires_at_epoch)

            new_row_version = self._runtime_store.compare_and_swap(
                session_ref=state.session.session_ref,
                expected_row_version=pending.runtime_row_version,
                state_schema_version=STATE_SCHEMA_VERSION,
                interaction_state_json=document,
                updated_at_utc=now,
                purge_after_utc=purge_after,
                last_provider_message_ref=inbound.provider_message_ref,
                last_transition_ref=event_key,
            )
            completed = self._idempotency_store.complete(
                idempotency_result_ref=pending.idempotency_ref,
                expected_row_version=pending.idempotency_row_version,
                result_ref=event_key,
                safe_result_json={
                    "state_schema_version": STATE_SCHEMA_VERSION,
                    "runtime_row_version": new_row_version,
                },
                completed_at_utc=now,
            )
            self._receipt_store.finish_processing(
                receipt_ref=pending.receipt_ref,
                expected_row_version=pending.receipt_row_version,
                processing_state="processed",
                idempotency_result_ref=completed.idempotency_result_ref,
                transport_delivery_ref=None,
                now_utc=now,
            )
        finally:
            with self._pending_lock:
                self._pending.pop(event_key, None)

    def _assert_endpoint(self, inbound: NormalizedInboundMessage) -> None:
        if inbound.metadata_phone_number_id != self._configured_endpoint_ref:
            raise ProviderEndpointMismatch("provider_endpoint_mismatch")

    def _reattest_context(self, session):
        if (
            session.merchant_ref is None
            or session.location_ref is None
            or session.entry_purpose is None
            or session.owner_identity_ref is None
            or session.context_binding_ref is None
        ):
            raise SecureRuntimeSessionRequired("bound_context_incomplete")
        try:
            attestation = self._xbos_context.attest_context(
                merchant_ref=session.merchant_ref,
                location_ref=session.location_ref,
                table_ref=session.table_ref,
                purpose=session.entry_purpose,
                dining_area_ref=session.dining_area_ref,
            )
        except (KeyError, PermissionError, ValueError):
            raise SecureRuntimeSessionRequired(
                "entry_context_unavailable"
            ) from None
        expected = (
            session.tenant_ref,
            session.merchant_ref,
            session.location_ref,
            session.table_ref,
            session.dining_area_ref,
            session.entry_purpose,
            session.context_binding_ref,
        )
        observed = (
            attestation.tenant_ref,
            attestation.merchant_ref,
            attestation.location_ref,
            attestation.table_ref,
            attestation.dining_area_ref,
            attestation.purpose,
            attestation.context_binding_ref,
        )
        if expected != observed:
            raise SecureRuntimeSessionRequired("stale_entry_context_rejected")
        return attestation


def _event_key(inbound: NormalizedInboundMessage) -> str:
    return (
        f"{PROVIDER_CODE}:{inbound.metadata_phone_number_id}:"
        f"{inbound.provider_message_ref}"
    )


def _request_fingerprint(
    inbound: NormalizedInboundMessage,
    *,
    sender_lookup_hash: str,
) -> str:
    reply = inbound.interactive_reply
    document = {
        "context_provider_message_ref": inbound.context_provider_message_ref,
        "interactive_reply": (
            None
            if reply is None
            else {
                "kind": reply.kind.value,
                "reply_id": reply.reply_id,
                "title": reply.title,
            }
        ),
        "occurred_at_epoch": inbound.occurred_at_epoch,
        "provider_endpoint_ref": inbound.metadata_phone_number_id,
        "provider_message_ref": inbound.provider_message_ref,
        "sender_lookup_hash": sender_lookup_hash,
        "text": inbound.text,
    }
    encoded = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalized_action_code(inbound: NormalizedInboundMessage) -> str:
    reply = inbound.interactive_reply
    if reply is not None:
        return f"{reply.kind.value}:{reply.reply_id}"
    return "text"


def _from_epoch(value: int) -> datetime:
    return datetime.fromtimestamp(value, tz=timezone.utc)


def _purge_after(expires_at_epoch: int | None) -> datetime:
    if expires_at_epoch is None:
        raise SecureRuntimeSessionRequired("session_expiry_required")
    return _from_epoch(expires_at_epoch) + timedelta(days=7)

\n
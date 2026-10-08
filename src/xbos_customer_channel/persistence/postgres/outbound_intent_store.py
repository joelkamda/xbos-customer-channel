from __future__ import annotations

from datetime import datetime
from uuid import UUID

from ...channel_contract import ChannelOutboundIntent, outbound_payload_digest
from .transport_state_stores import PostgresTransportDeliveryStore, TransportDelivery


class PostgresChannelOutboundIntentStore:
    """Durable delivery-state composition for provider-neutral outbound intent.

    The existing transport_delivery table stores the delivery identity, state,
    recipient lookup hash and payload digest. Provider formatting and provider
    request material remain outside Customer Channel domain persistence.
    """

    def __init__(self, store: PostgresTransportDeliveryStore) -> None:
        self._store = store

    def reserve(
        self,
        *,
        intent: ChannelOutboundIntent,
        receipt_ref: UUID,
        render_ordinal: int,
        recipient_lookup_hash: str,
        provider_code: str,
        now_utc: datetime,
        purge_after_utc: datetime,
    ) -> TransportDelivery:
        if not recipient_lookup_hash.strip():
            raise ValueError("recipient_lookup_hash_required")
        if not provider_code.strip():
            raise ValueError("provider_code_required")
        return self._store.create_pending(
            receipt_ref=receipt_ref,
            conversation_ref=intent.conversation_ref,
            render_ordinal=render_ordinal,
            recipient_lookup_hash=recipient_lookup_hash,
            message_kind=intent.event_type,
            payload_digest=outbound_payload_digest(intent),
            provider_code=provider_code,
            now_utc=now_utc,
            purge_after_utc=purge_after_utc,
        )

    def record_send_outcome(
        self,
        *,
        reservation: TransportDelivery,
        delivery_state: str,
        provider_message_ref: str | None,
        provider_error_code: str | None,
        now_utc: datetime,
    ) -> TransportDelivery:
        return self._store.record_send_outcome(
            delivery_ref=reservation.delivery_ref,
            expected_row_version=reservation.row_version,
            delivery_state=delivery_state,
            provider_message_ref=provider_message_ref,
            provider_error_code=provider_error_code,
            now_utc=now_utc,
        )

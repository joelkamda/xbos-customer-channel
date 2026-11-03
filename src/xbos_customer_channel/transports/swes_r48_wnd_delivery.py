"""R48 TEST-only signed CC -> WND event over pinned authenticated HTTPS.

No dispatch unless called from a real first-processed and sender-bound receipt.
No provider request can choose the destination or service credential.
A failed delivery MUST remain retryable by a genuine signed duplicate within
the original bounded event TTL. WND owns final idempotent IA0 writes.
"""
from __future__ import annotations

import hmac
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import httpx

from ..persistence.postgres.hosted_twilio_receipt import ProcessedTwilioReceipt
from .swes_r48_verified_event import (
    WNDVerifiedInboundEvent,
    wnd_delivery_headers,
)

_URL = (
    "https://wnd-xafpay-demo-austin-backend.onrender.com"
    "/internal/swes/track-a-sandbox/first-ia0"
)


class WNDDeliveryUnavailable(RuntimeError):
    """Missing independent service trust or unverified/expired event."""


@dataclass(frozen=True, slots=True)
class WNDEventDeliveryConfig:
    bearer: str

    @classmethod
    def from_environment(cls, env: Mapping[str, str] | None = None) -> WNDEventDeliveryConfig:
        values = os.environ if env is None else env
        if values.get("CC_R48_WND_IA0_DELIVERY_ENABLED") != "1":
            raise WNDDeliveryUnavailable("cc_wnd_delivery_default_off")
        bearer = values.get("CC_R48_WND_IA0_BEARER", "")
        if (
            not isinstance(bearer, str) or not 32 <= len(bearer) <= 256
            or any(ord(ch) < 33 or ord(ch) > 126 for ch in bearer)
        ):
            raise WNDDeliveryUnavailable("wnd_private_service_credential_not_admitted")
        return cls(bearer=bearer)


@dataclass(frozen=True, slots=True)
class WNDSessionAcknowledgment:
    event_id: str
    correlation_ref: str
    conversation_public_id: UUID
    participant_public_id: UUID
    session_public_id: UUID


def send_verified_cc_event(
    *, event: WNDVerifiedInboundEvent, receipt: ProcessedTwilioReceipt,
    config: WNDEventDeliveryConfig, now: datetime,
    http_client: httpx.Client | None = None,
) -> WNDSessionAcknowledgment:
    """Send only a source-verified receipt with persisted matching sender HMAC."""
    if (
        not isinstance(event, WNDVerifiedInboundEvent)
        or not isinstance(receipt, ProcessedTwilioReceipt)
        or not isinstance(config, WNDEventDeliveryConfig)
        or now.tzinfo is None or now.utcoffset() is None
    ):
        raise WNDDeliveryUnavailable("untrusted_cc_event")
    if (
        not isinstance(receipt.sender_lookup_hash, str)
        or not hmac.compare_digest(receipt.sender_lookup_hash, event.sender_lookup_ref)
        or receipt.processing_state != "processed"
        or receipt.processing_attempt != 1
        or receipt.row_version < 2
        or not hmac.compare_digest(receipt.provider_message_ref_hash, event.receipt_ref)
        or not hmac.compare_digest(receipt.payload_digest, event.form_payload_sha256)
    ):
        raise WNDDeliveryUnavailable("sender_bound_first_receipt_required")
    try:
        received = datetime.fromisoformat(event.server_received_at_utc)
        expiry = datetime.fromisoformat(event.delivered_before_utc)
    except (TypeError, ValueError) as exc:
        raise WNDDeliveryUnavailable("invalid_expiry") from exc
    if (
        received.tzinfo is None or expiry.tzinfo is None
        or not (received <= now < expiry)
        or not (received <= expiry <= received + __import__("datetime").timedelta(minutes=5))
    ):
        raise WNDDeliveryUnavailable("expired_verified_event")
    headers = wnd_delivery_headers(event, bearer=config.bearer)
    local_client = http_client is None
    client = http_client or httpx.Client(
        trust_env=False, follow_redirects=False,
        timeout=httpx.Timeout(connect=2.0, read=3.0, write=3.0, pool=2.0),
    )
    try:
        try:
            response = client.post(
                _URL, json=event.as_private_payload(), headers=dict(headers),
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise WNDDeliveryUnavailable("wnd_private_https_delivery_failed") from exc
        if response.status_code not in (200, 202):
            raise WNDDeliveryUnavailable("wnd_ia0_receiver_not_admitted")
        try:
            result = response.json()
            if (
                type(result) is not dict
                or result.get("event_id") != event.event_id
                or result.get("correlation_ref") != event.correlation_ref
                or result.get("verification_state") != "channel_verified"
            ):
                raise ValueError("untrusted_response")
            identifiers = [
                UUID(result[name]) for name in (
                    "ia0_conversation_public_id",
                    "ia0_participant_public_id",
                    "ia0_session_public_id",
                )
            ]
            if any(identifier.int == 0 for identifier in identifiers):
                raise ValueError("empty_ia0_reference")
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise WNDDeliveryUnavailable("wnd_ia0_acknowledgment_unqualified") from exc
        return WNDSessionAcknowledgment(
            event_id=event.event_id,
            correlation_ref=event.correlation_ref,
            conversation_public_id=identifiers[0],
            participant_public_id=identifiers[1],
            session_public_id=identifiers[2],
        )
    finally:
        if local_client:
            client.close()

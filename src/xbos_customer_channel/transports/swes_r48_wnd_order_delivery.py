"""R48 TEST-only, signed order-intent handoff to WND canonical commerce.

The caller supplies the already-verified real Twilio Sandbox ingress action,
first durable sender-bound CC receipt, and independently admitted WND IA0
acknowledgment. This separate checkout bearer never substitutes for IA0 bearer.
No provider payment or synthetic success is created by CC.
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
from .swes_r48_verified_event import WNDVerifiedInboundEvent
from .swes_r48_wnd_delivery import WNDSessionAcknowledgment
from .twilio_signed_ingress import VerifiedSandboxIngress

_ORDER_URL = (
    "https://wnd-xafpay-demo-austin-backend.onrender.com"
    "/internal/swes/track-a-sandbox/canonical-order"
)
_ACTION = "order wnd test"
_MERCHANT = "4b196022-55a1-548c-b122-f1f9e4b78bea"


class WNDOrderDeliveryUnavailable(RuntimeError):
    """Protected, default-off order handoff not qualified."""


@dataclass(frozen=True, slots=True)
class WNDOrderDeliveryConfig:
    bearer: str

    @classmethod
    def from_environment(cls, env: Mapping[str, str] | None = None):
        values = os.environ if env is None else env
        bearer = values.get("CC_R48_WND_ORDER_BEARER", "")
        ia0_bearer = values.get("CC_R48_WND_IA0_BEARER", "")
        if (
            values.get("CC_R48_WND_ORDER_DELIVERY_ENABLED") != "1"
            or values.get("CC_R48_WND_IA0_DELIVERY_ENABLED") != "1"
            or values.get("CC_R48_SENDER_BINDING_ENABLED") != "1"
            or not isinstance(bearer, str)
            or not isinstance(ia0_bearer, str)
            or not 32 <= len(bearer) <= 256
            or not 32 <= len(ia0_bearer) <= 256
            or any(not (33 <= ord(ch) <= 126) for ch in bearer + ia0_bearer)
            or hmac.compare_digest(bearer, ia0_bearer)
        ):
            raise WNDOrderDeliveryUnavailable("test_order_bilateral_authority_not_admitted")
        return cls(bearer)


@dataclass(frozen=True, slots=True)
class WNDOrderAcknowledgment:
    order_ref: UUID
    context_binding_ref: UUID
    correlation_ref: str
    canonical_amount: int
    order_state: str


def send_verified_cc_order(
    *,
    verified: VerifiedSandboxIngress,
    event: WNDVerifiedInboundEvent,
    receipt: ProcessedTwilioReceipt,
    ia0: WNDSessionAcknowledgment,
    config: WNDOrderDeliveryConfig,
    now: datetime,
    http_client: httpx.Client | None = None,
) -> WNDOrderAcknowledgment:
    """Only a signed first/duplicate order-intent may reach WND commerce."""
    if (
        not isinstance(verified, VerifiedSandboxIngress)
        or verified.action != "test_order"
        or verified.disposition not in ("accepted", "duplicate")
        or not isinstance(event, WNDVerifiedInboundEvent)
        or not isinstance(receipt, ProcessedTwilioReceipt)
        or not isinstance(ia0, WNDSessionAcknowledgment)
        or not isinstance(config, WNDOrderDeliveryConfig)
        or now.tzinfo is None
        or now.utcoffset() is None
        or event.event_id != ia0.event_id
        or event.correlation_ref != ia0.correlation_ref
        or ia0.context_binding_ref.int == 0
        or receipt.processing_state != "processed"
        or receipt.processing_attempt != 1
        or receipt.row_version < 2
        or not isinstance(receipt.sender_lookup_hash, str)
        or not hmac.compare_digest(receipt.sender_lookup_hash, event.sender_lookup_ref)
        or not hmac.compare_digest(receipt.provider_message_ref_hash, event.receipt_ref)
        or not hmac.compare_digest(receipt.payload_digest, event.form_payload_sha256)
    ):
        raise WNDOrderDeliveryUnavailable("fresh_signed_order_intent_required")
    try:
        received = datetime.fromisoformat(event.server_received_at_utc)
        expires = datetime.fromisoformat(event.delivered_before_utc)
    except (TypeError, ValueError) as exc:
        raise WNDOrderDeliveryUnavailable("invalid_signed_event_timestamps") from exc
    if (
        received.tzinfo is None or expires.tzinfo is None
        or not (received <= now < expires)
        or (expires - received).total_seconds() > 300
    ):
        raise WNDOrderDeliveryUnavailable("expired_signed_order_intent")
    body = {
        "context_binding_ref": str(ia0.context_binding_ref),
        "correlation_ref": event.correlation_ref,
        "event_id": event.event_id,
        "action": _ACTION,
    }
    headers = {
        "Authorization": "Bearer " + config.bearer,
        "X-CC-Event-Id": event.event_id,
        "X-Correlation-Ref": event.correlation_ref,
        "Content-Type": "application/json",
    }
    own_client = http_client is None
    client = http_client or httpx.Client(
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(connect=2.0, read=3.0, write=3.0, pool=2.0),
    )
    try:
        try:
            response = client.post(
                _ORDER_URL, json=body, headers=headers, follow_redirects=False
            )
        except httpx.HTTPError as exc:
            raise WNDOrderDeliveryUnavailable("wnd_test_order_https_unavailable") from exc
        if response.status_code != 201:
            raise WNDOrderDeliveryUnavailable("wnd_test_order_not_admitted")
        try:
            result = response.json()
            if (
                type(result) is not dict
                or result.get("schema") != "xafpay.wnd.swes.track_a.canonical_order.v1"
                or result.get("tenant_id") != 910001
                or result.get("location_id") != 5
                or result.get("merchant_public_id") != _MERCHANT
                or result.get("canonical_amount") != "1000"
                or result.get("currency") != "XAF"
                or result.get("order_state") != "SUBMITTED"
                or result.get("payment_state") != "NOT_INITIATED"
                or result.get("context_binding_ref") != str(ia0.context_binding_ref)
                or result.get("correlation_ref") != event.correlation_ref
            ):
                raise ValueError("wrong_signed_order_response")
            order_ref = UUID(result["order_ref"])
            if order_ref.int == 0:
                raise ValueError("empty_order_uuid")
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise WNDOrderDeliveryUnavailable("wnd_canonical_order_response_unqualified") from exc
        return WNDOrderAcknowledgment(
            order_ref=order_ref,
            context_binding_ref=ia0.context_binding_ref,
            correlation_ref=event.correlation_ref,
            canonical_amount=1000,
            order_state="SUBMITTED",
        )
    finally:
        if own_client:
            client.close()

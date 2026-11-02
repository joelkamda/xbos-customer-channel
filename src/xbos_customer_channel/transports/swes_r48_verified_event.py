"""R48 Track A: real signed Twilio ingress -> authenticated CC-to-WND event v1.

Pure source-side evidence adapter; intentionally NOT a deployed IA0 writer or
bearer issuer. A caller must first finalize the real restricted hosted receipt.
WND independently authenticates the service principal and persists IA0 state.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid5

from ..persistence.postgres.hosted_twilio_receipt import ProcessedTwilioReceipt
from ..persistence.postgres.serialization import LocatorKeyRing
from .twilio_signed_ingress import (
    SandboxIngressConfig,
    VerifiedSandboxIngress,
    parse_bounded_form,
)
from .twilio_whatsapp import (
    TwilioWebhookRejected,
    TwilioWhatsAppInboundAdapter,
    canonical_twilio_form_digest,
)

SCHEMA = "xafpay.cc.signed_twilio_ia0_ingress.v1"
TENANT = 910001
MERCHANT = "4b196022-55a1-548c-b122-f1f9e4b78bea"
CHANNEL = "twilio_whatsapp"
WND_AUDIENCE = "wnd-austin-track-a-ia0"
CONTEXT_PURPOSE = "restaurant.customer_channel.checkout"
CORRELATION_NAMESPACE = UUID("ea8f1a22-a8cf-4d32-850e-249ac27c7f39")
MAX_EVIDENCE_AGE = timedelta(minutes=2)
MAX_DELIVERY_AGE = timedelta(minutes=5)
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class VerifiedEventNotAdmitted(PermissionError):
    """No authenticated or durable first-receipt event may be produced."""


@dataclass(frozen=True, slots=True)
class WNDVerifiedInboundEvent:
    """Wire data; the body is NOT authentication or a session by itself."""

    event_id: str
    correlation_ref: str
    provider_message_id: str
    sender_lookup_ref: str
    sender_lookup_version: str
    provider_endpoint_ref: str
    account_ref: str
    server_received_at_utc: str
    delivered_before_utc: str
    receipt_ref: str
    form_payload_sha256: str

    def as_private_payload(self) -> dict[str, Any]:
        """Stable, bounded JSON body for WND's independently authorized writer."""
        return {
            "schema": SCHEMA,
            "tenant_id": TENANT,
            "merchant_public_id": MERCHANT,
            "audience": WND_AUDIENCE,
            "context_purpose": CONTEXT_PURPOSE,
            "channel": CHANNEL,
            "event_id": self.event_id,
            "correlation_ref": self.correlation_ref,
            "provider_message_id": self.provider_message_id,
            "sender_reference": {
                "scheme": "cc_locator_hmac_sha256_v1",
                "version": self.sender_lookup_version,
                "value": self.sender_lookup_ref,
            },
            "provider_endpoint_ref": self.provider_endpoint_ref,
            "provider_account_ref": self.account_ref,
            "server_received_at_utc": self.server_received_at_utc,
            "delivered_before_utc": self.delivered_before_utc,
            "receipt": {
                "authority": "xafpay.cc.hosted_pg17_signed_twilio_receipt",
                "reference": self.receipt_ref,
                "payload_sha256": self.form_payload_sha256,
                "processing_state": "processed",
                "processing_attempt": 1,
            },
            "verification": {
                "source": "twilio_signed_webhook_verified_by_cc",
                "provider_signature_verified": True,
                "sender_enrolled_test_only": True,
                "identity_resolution_state": "channel_identified",
                "verification_state": "channel_verified",
                "authentication_authority": None,
                "authentication_reference": None,
                "timestamp_source": "cc_server_received",
            },
        }


def build_verified_wnd_event(
    *, settings: SandboxIngressConfig, verified: VerifiedSandboxIngress,
    raw_body: bytes, signature: str, content_type: str,
    received_at: datetime, receipt: ProcessedTwilioReceipt,
    sender_key_ring: LocatorKeyRing, allow_processed_duplicate: bool = False,
) -> WNDVerifiedInboundEvent:
    """Re-verify the original provider-signed form, require true PG17 readback.

    Call only from the trusted signed-ingress path *after* receipt finalization.
    WND must reject this JSON without its own independently authorized bearer
    with exact audience, location/tenant scope, expiry and replay protection.
    """
    if (
        not isinstance(verified, VerifiedSandboxIngress)
        or verified.disposition not in (
            ("accepted", "duplicate") if allow_processed_duplicate else ("accepted",)
        )
        or verified.merchant_dispatch_enabled
        or not isinstance(receipt, ProcessedTwilioReceipt)
        or not isinstance(sender_key_ring, LocatorKeyRing)
        or received_at.tzinfo is None
        or received_at.utcoffset() is None
        or received_at.astimezone(UTC) >= settings.tester_expires_utc
    ):
        raise VerifiedEventNotAdmitted("first_signed_enrolled_receipt_required")
    try:
        form = parse_bounded_form(raw_body, content_type)
        message = TwilioWhatsAppInboundAdapter(settings.provider).parse_verified(
            form=form, signature=signature, received_at=received_at
        )
    except (ValueError, TwilioWebhookRejected) as exc:
        raise VerifiedEventNotAdmitted("provider_signature_or_form_invalid") from exc
    if (
        message.account_sid != settings.provider.account_sid
        or not hmac.compare_digest(message.sender, settings.allowed_tester)
        or not hmac.compare_digest(message.recipient, settings.provider.sender)
        or form.get("NumMedia", "0") != "0"
        or len(message.body) > 500
        or not re.fullmatch(r"SM[A-Za-z0-9]{2,64}", message.provider_message_ref)
    ):
        raise VerifiedEventNotAdmitted("provider_test_sender_not_admitted")
    key = hashlib.sha256(
        (message.provider_message_ref + "\x00" + message.recipient + "\x00"
         + settings.provider.account_sid).encode("utf-8")
    ).hexdigest()
    form_digest = canonical_twilio_form_digest(form)
    if (
        not isinstance(verified.receipt_key, str)
        or not hmac.compare_digest(key, verified.receipt_key)
        or verified.event_digest != key[:20]
        or not _HEX64.fullmatch(receipt.provider_message_ref_hash)
        or not hmac.compare_digest(key, receipt.provider_message_ref_hash)
        or not _HEX64.fullmatch(receipt.payload_digest)
        or not hmac.compare_digest(form_digest, receipt.payload_digest)
        or receipt.processing_state != "processed"
        or type(receipt.processing_attempt) is not int
        or receipt.processing_attempt != 1
        or type(receipt.row_version) is not int
        or receipt.row_version < 2
    ):
        raise VerifiedEventNotAdmitted("hosted_first_processed_receipt_required")
    if (
        receipt.occurred_at_utc.tzinfo is None
        or receipt.occurred_at_utc.utcoffset() is None
        or abs(received_at - receipt.occurred_at_utc) > MAX_EVIDENCE_AGE
    ):
        raise VerifiedEventNotAdmitted("receipt_timestamp_provenance_mismatch")
    sender = sender_key_ring.current(
        channel_code="whatsapp",
        provider_endpoint_ref=message.normalized_recipient,
        raw_sender=message.normalized_sender,
    )
    if (
        not _HEX64.fullmatch(sender.digest)
        or not sender.key_version.strip()
        or len(sender.key_version) > 48
    ):
        raise VerifiedEventNotAdmitted("keyed_sender_reference_invalid")
    if allow_processed_duplicate and (
        not isinstance(receipt.sender_lookup_hash, str)
        or not hmac.compare_digest(receipt.sender_lookup_hash, sender.digest)
    ):
        raise VerifiedEventNotAdmitted("durable_sender_binding_required")
    correlation = str(uuid5(CORRELATION_NAMESPACE, "cc-r48-ia0:" + key))
    event_id = hashlib.sha256(("cc-r48-v1:" + key).encode("ascii")).hexdigest()
    received = received_at.astimezone(UTC)
    return WNDVerifiedInboundEvent(
        event_id=event_id,
        correlation_ref=correlation,
        provider_message_id=message.provider_message_ref,
        sender_lookup_ref=sender.digest,
        sender_lookup_version=sender.key_version,
        provider_endpoint_ref=message.normalized_recipient,
        account_ref=settings.provider.account_sid,
        server_received_at_utc=received.isoformat(),
        delivered_before_utc=(received + MAX_DELIVERY_AGE).isoformat(),
        receipt_ref=key,
        form_payload_sha256=form_digest,
    )


def wnd_delivery_headers(
    event: WNDVerifiedInboundEvent, *, bearer: str
) -> Mapping[str, str]:
    """Private HTTPS delivery only; WND checks bearer, never trusts body claims."""
    if not bearer or not bearer.strip() or "\n" in bearer or "\r" in bearer:
        raise VerifiedEventNotAdmitted("wnd_service_credential_required")
    if not _HEX64.fullmatch(event.event_id):
        raise VerifiedEventNotAdmitted("invalid_event_id")
    return {
        "Authorization": "Bearer " + bearer,
        "X-Correlation-Ref": event.correlation_ref,
        "X-CC-Event-Id": event.event_id,
        "X-Idempotency-Key": event.event_id,
        "X-CC-Event-Schema": SCHEMA,
        "Content-Type": "application/json",
    }

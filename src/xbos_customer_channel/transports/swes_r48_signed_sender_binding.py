"""R48 sender-to-receipt binding from the original real signed Twilio form.

Run only for the first accepted durable provider claim, in the signed ingress
handler. The resulting HMAC is persisted atomically with receipt finalization.
Never derive this trust from a later WND-supplied JSON claim.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime

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

_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class SignedSenderBindingUnavailable(PermissionError):
    """No exact first provider-signed, tenant-isolated sender binding."""


def derive_first_signed_sender_hash(
    *, settings: SandboxIngressConfig, verified: VerifiedSandboxIngress,
    raw_body: bytes, signature: str | None, content_type: str | None,
    received_at: datetime, key_ring: LocatorKeyRing,
) -> str:
    if (
        verified.disposition != "accepted"
        or verified.merchant_dispatch_enabled
        or not isinstance(verified.receipt_key, str)
        or not _HEX64.fullmatch(verified.receipt_key)
        or not isinstance(verified.form_payload_digest, str)
        or not _HEX64.fullmatch(verified.form_payload_digest)
        or received_at.tzinfo is None
        or received_at.utcoffset() is None
        or received_at >= settings.tester_expires_utc
        or not isinstance(key_ring, LocatorKeyRing)
    ):
        raise SignedSenderBindingUnavailable("first_signed_durable_claim_required")
    try:
        form = parse_bounded_form(raw_body, content_type)
        if not hmac.compare_digest(
            canonical_twilio_form_digest(form), verified.form_payload_digest
        ):
            raise SignedSenderBindingUnavailable("signed_form_digest_changed")
        message = TwilioWhatsAppInboundAdapter(settings.provider).parse_verified(
            form=form, signature=signature, received_at=received_at,
        )
    except (ValueError, TwilioWebhookRejected) as exc:
        raise SignedSenderBindingUnavailable("signed_provider_form_required") from exc
    if (
        form.get("AccountSid") != settings.provider.account_sid
        or message.account_sid != settings.provider.account_sid
        or not hmac.compare_digest(message.sender, settings.allowed_tester)
        or not hmac.compare_digest(message.recipient, settings.provider.sender)
        or form.get("NumMedia", "0") != "0"
        or len(message.body) > 500
        or not re.fullmatch(r"SM[A-Za-z0-9]{2,64}", message.provider_message_ref)
    ):
        raise SignedSenderBindingUnavailable("signed_test_sender_scope_invalid")
    receipt_key = hashlib.sha256(
        (message.provider_message_ref + "\x00" + message.recipient + "\x00"
         + settings.provider.account_sid).encode("utf-8")
    ).hexdigest()
    if (
        not hmac.compare_digest(receipt_key, verified.receipt_key)
        or not hmac.compare_digest(receipt_key[:20], verified.event_digest)
    ):
        raise SignedSenderBindingUnavailable("signed_provider_receipt_mismatch")
    try:
        opaque = key_ring.current(
            channel_code="whatsapp",
            provider_endpoint_ref=message.normalized_recipient,
            raw_sender=message.normalized_sender,
        )
    except ValueError as exc:
        raise SignedSenderBindingUnavailable("sender_locator_unavailable") from exc
    if not _HEX64.fullmatch(opaque.digest):
        raise SignedSenderBindingUnavailable("invalid_keyed_sender_digest")
    return opaque.digest

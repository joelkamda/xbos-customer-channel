"""R48: authenticated, independent CC hosted receipt-only attestation.

Never infers verified sender/customer authentication from the receipt table.
Requires separate WND-to-CC bearer (SHA256 trust anchor, staged out of source)
and the real restricted hosted PG17 receipt reader. Default-off.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid5

from ..persistence.postgres.hosted_twilio_receipt import HostedTwilioReceiptLedger
from ..persistence.postgres.serialization import LocatorKeyRing
from .swes_r48_verified_event import (
    CORRELATION_NAMESPACE,
    MERCHANT,
    SCHEMA,
    TENANT,
    WND_AUDIENCE,
)
from .twilio_signed_ingress import SandboxIngressConfig

_HEX64 = re.compile(r"[a-f0-9]{64}\Z")
_SID = re.compile(r"SM[A-Za-z0-9]{2,64}\Z")
_SOURCE = "xafpay.cc.hosted_pg17_signed_twilio_receipt"


class ReceiptAttestationDenied(PermissionError):
    """Private credential, event identity or genuine receipt unqualified."""


@dataclass(frozen=True, slots=True)
class WNDAttestorTrust:
    """Independent WND-to-CC staging auth, not a customer/session credential."""

    token_sha256: str
    expires_utc: datetime

    @classmethod
    def from_environment(cls, values: Mapping[str, str]) -> WNDAttestorTrust:
        if values.get("CC_R48_WND_ATTEST_ENABLED") != "1":
            raise ReceiptAttestationDenied("receipt_attestation_disabled")
        digest = values.get("CC_R48_WND_ATTEST_TOKEN_SHA256", "").strip()
        if not _HEX64.fullmatch(digest):
            raise ReceiptAttestationDenied("receipt_attestor_digest_missing")
        try:
            expiry = datetime.fromisoformat(
                values["CC_R48_WND_ATTEST_EXPIRES_UTC"]
            )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ReceiptAttestationDenied("receipt_attestor_expiry_missing") from exc
        if expiry.tzinfo is None or expiry.utcoffset() is None:
            raise ReceiptAttestationDenied("receipt_attestor_expiry_invalid")
        return cls(digest, expiry.astimezone(UTC))


def attest_cc_processed_receipt(
    *, payload: dict, bearer: str, trust: WNDAttestorTrust,
    settings: SandboxIngressConfig, ledger: HostedTwilioReceiptLedger,
    now: datetime,
) -> dict:
    """One DB-backed receipt attestation, not a sender/IA0 authorization.

    WND must authenticate its calling principal independently and validate
    the response against exact request fields. Do not call the IA0 writer
    from a mere processed-receipt boolean.
    """
    if (
        now.tzinfo is None or now.utcoffset() is None
        or now >= trust.expires_utc
        or not isinstance(bearer, str)
        or len(bearer) < 32 or len(bearer) > 256
        or any(ord(char) < 33 or ord(char) > 126 for char in bearer)
    ):
        raise ReceiptAttestationDenied("wnd_to_cc_private_identity_unavailable")
    digest = hashlib.sha256(bearer.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(digest, trust.token_sha256):
        raise ReceiptAttestationDenied("wnd_to_cc_private_identity_unavailable")
    if not isinstance(payload, dict):
        raise ReceiptAttestationDenied("invalid_event")
    if (
        payload.get("schema") != SCHEMA or type(payload.get("tenant_id")) is not int
        or payload["tenant_id"] != TENANT or payload.get("merchant_public_id") != MERCHANT
        or payload.get("audience") != WND_AUDIENCE
        or payload.get("channel") != "twilio_whatsapp"
        or payload.get("context_purpose") != "restaurant.customer_channel.checkout"
    ):
        raise ReceiptAttestationDenied("wrong_test_scope")
    receipt = payload.get("receipt")
    if not isinstance(receipt, dict) or receipt.get("authority") != _SOURCE:
        raise ReceiptAttestationDenied("unsupported_receipt")
    provider_sid = payload.get("provider_message_id")
    endpoint = payload.get("provider_endpoint_ref")
    account = payload.get("provider_account_ref")
    if (
        not isinstance(provider_sid, str) or not _SID.fullmatch(provider_sid)
        or endpoint != settings.provider.sender.removeprefix("whatsapp:")
        or account != settings.provider.account_sid
    ):
        raise ReceiptAttestationDenied("provider_identity_mismatch")
    key = hashlib.sha256(
        (provider_sid+"\x00"+settings.provider.sender+"\x00"+account).encode()
    ).hexdigest()
    if not hmac.compare_digest(key, str(receipt.get("reference", ""))):
        raise ReceiptAttestationDenied("provider_receipt_identity_mismatch")
    if (
        not isinstance(receipt.get("payload_sha256"), str)
        or not _HEX64.fullmatch(receipt["payload_sha256"])
        or receipt.get("processing_state") != "processed"
        or type(receipt.get("processing_attempt")) is not int
        or receipt["processing_attempt"] != 1
    ):
        raise ReceiptAttestationDenied("invalid_first_receipt_claim")
    event_id = hashlib.sha256(("cc-r48-v1:" + key).encode("ascii")).hexdigest()
    correlation = str(uuid5(CORRELATION_NAMESPACE, "cc-r48-ia0:" + key))
    if payload.get("event_id") != event_id or payload.get("correlation_ref") != correlation:
        raise ReceiptAttestationDenied("event_or_correlation_mismatch")
    try:
        timestamp = datetime.fromisoformat(
            payload["server_received_at_utc"]
        )
        expiry = datetime.fromisoformat(
            payload["delivered_before_utc"]
        )
    except (KeyError, ValueError, AttributeError, TypeError) as exc:
        raise ReceiptAttestationDenied("event_timestamp_invalid") from exc
    if (
        timestamp.tzinfo is None or expiry.tzinfo is None
        or not (timestamp <= now < expiry <= timestamp + __import__("datetime").timedelta(minutes=5))
    ):
        raise ReceiptAttestationDenied("event_expired")
    processed = ledger.read_processed_first(
        key=key, payload_digest=receipt["payload_sha256"]
    )
    if (
        processed is None
        or abs((processed.occurred_at_utc - timestamp).total_seconds()) > 120
    ):
        raise ReceiptAttestationDenied("restricted_hosted_processed_receipt_not_found")
    sender = payload.get("sender_reference")
    sender_attested = False
    if (
        isinstance(sender, dict)
        and sender.get("scheme") == "cc_locator_hmac_sha256_v1"
        and isinstance(sender.get("version"), str)
        and isinstance(sender.get("value"), str)
        and _HEX64.fullmatch(sender["value"])
        and isinstance(processed.sender_lookup_hash, str)
        and _HEX64.fullmatch(processed.sender_lookup_hash)
    ):
        try:
            key_ring = LocatorKeyRing.from_environment()
            allowed_versions = {
                key_ring.current_version, key_ring.previous_version
            }
            sender_attested = (
                sender["version"] in allowed_versions
                and hmac.compare_digest(
                    sender["value"], processed.sender_lookup_hash
                )
            )
        except ValueError:
            sender_attested = False
    return {
        "schema": "xafpay.cc.restricted_receipt_attestation.v1",
        "status": "processed_receipt_verified",
        "event_id": event_id,
        "correlation_ref": correlation,
        "receipt_reference": key,
        "payload_sha256": processed.payload_digest,
        "first_processed_attempt": 1,
        "server_received_at_utc": timestamp.astimezone(UTC).isoformat(),
        # Only the same signed-ingress transaction may set the persisted hash.
        # Legacy NULL receipts never qualify, including historical WhatsApp tests.
        "sender_provenance_attested": sender_attested,
        "customer_authenticated_external": False,
    }

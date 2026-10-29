"""Sandbox-only signed inbound boundary.

No commerce dispatch, message send, secret logging, customer database writes,
or production sender registration. All live activation is separately gated.
The replay ledger is *process local*: it is not a durable merchant CAS store.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from urllib.parse import parse_qsl, urlsplit

from .twilio_whatsapp import (
    TwilioWhatsAppConfig,
    TwilioWhatsAppInboundAdapter,
    TwilioWebhookRejected,
    canonical_twilio_form_digest,
)

WEBHOOK_PATH = "/webhooks/twilio/whatsapp"
FLAG = "CC_TWILIO_SANDBOX_INGRESS_ENABLED"
TESTER = "CC_TWILIO_ALLOWED_TESTER"
EXPIRES = "CC_TWILIO_TESTER_JOIN_EXPIRES_UTC"
NO_MONEY = "CC_TWILIO_NONFINANCIAL_ONLY"
MAX_FORM = 8192
MAX_FIELDS = 64
MAX_LEDGER = 128
LEDGER_SECONDS = 3600


class IngressNotConfigured(RuntimeError):
    pass


class IngressRejected(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SandboxIngressConfig:
    provider: TwilioWhatsAppConfig
    allowed_tester: str
    tester_expires_utc: datetime
    expected_host: str

    @classmethod
    def from_environment(
        cls, environ: Mapping[str, str] | None = None
    ) -> "SandboxIngressConfig":
        env = os.environ if environ is None else environ
        # Do not inspect credentials until the explicit feature gate passes.
        if env.get(FLAG) != "1" or env.get(NO_MONEY) != "1":
            raise IngressNotConfigured("sandbox_ingress_disabled")
        try:
            provider = TwilioWhatsAppConfig.from_environment(env)
        except ValueError as exc:
            raise IngressNotConfigured("existing_signing_configuration_unavailable") from exc
        url = urlsplit(provider.webhook_url)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.port not in (None, 443)
            or url.query or url.fragment
            or url.path != WEBHOOK_PATH
            or url.netloc.lower() != url.hostname.lower()
        ):
            raise IngressNotConfigured("canonical_public_https_webhook_url_required")
        tester = env.get(TESTER, "")
        if not re.fullmatch(r"whatsapp:\+[1-9][0-9]{7,14}", tester):
            raise IngressNotConfigured("enrolled_sandbox_tester_unverified")
        expiry = env.get(EXPIRES, "")
        try:
            when = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise IngressNotConfigured("sandbox_join_expiry_unverified") from exc
        if when.tzinfo is None or when.utcoffset() is None:
            raise IngressNotConfigured("sandbox_join_expiry_unverified")
        return cls(
            provider=provider,
            allowed_tester=tester,
            tester_expires_utc=when.astimezone(timezone.utc),
            expected_host=url.hostname,
        )


def parse_bounded_form(raw_body: bytes, content_type: str | None) -> dict[str, str]:
    if not content_type or content_type.lower().split(";", 1)[0].strip() != (
        "application/x-www-form-urlencoded"
    ):
        raise IngressRejected("form_encoding_required")
    if not raw_body or len(raw_body) > MAX_FORM:
        raise IngressRejected("form_size_out_of_bounds")
    try:
        text = raw_body.decode("utf-8", errors="strict")
        pairs = parse_qsl(
            text, keep_blank_values=True,
            strict_parsing=True, encoding="utf-8", errors="strict",
            max_num_fields=MAX_FIELDS,
        )
    except (UnicodeError, ValueError) as exc:
        raise IngressRejected("malformed_form") from exc
    if not pairs:
        raise IngressRejected("empty_form")
    result: dict[str, str] = {}
    for key, value in pairs:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", key):
            raise IngressRejected("invalid_form_field")
        if key in result or len(value) > 4096:
            raise IngressRejected("duplicate_or_excessive_form_field")
        result[key] = value
    return result


class LocalNoCommerceReplayLedger:
    """Finite process-local duplicate receipt guard, never a payment CAS."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._entries: dict[str, tuple[str, int]] = {}

    def check_and_claim(
        self, *, key: str, digest: str, now_epoch: int
    ) -> str:
        with self._lock:
            self._entries = {
                k: v for k, v in self._entries.items()
                if v[1] > now_epoch
            }
            prior = self._entries.get(key)
            if prior:
                return "duplicate" if hmac.compare_digest(prior[0], digest) else "collision"
            if len(self._entries) >= MAX_LEDGER:
                return "unavailable"
            self._entries[key] = (digest, now_epoch + LEDGER_SECONDS)
            return "accepted"


@dataclass(frozen=True, slots=True)
class VerifiedSandboxIngress:
    disposition: str
    event_digest: str  # non-reversible correlation, no PII or provider SID
    action: str
    merchant_dispatch_enabled: bool = False


def _r5_test_action(body: str) -> str:
    normalized = " ".join(body.strip().casefold().split())
    if normalized == "hello wnd":
        return "greeting"
    if normalized == "order wnd test":
        return "test_order"
    return "receipt_only"


def verify_sandbox_inbound(
    *,
    settings: SandboxIngressConfig,
    ledger: LocalNoCommerceReplayLedger,
    raw_body: bytes,
    content_type: str | None,
    signature: str | None,
    request_host: str | None,
    forwarded_proto: str | None,
    now: datetime,
) -> VerifiedSandboxIngress:
    # The public signature URL comes only from config, NOT raw proxy headers.
    if (
        request_host != settings.expected_host
        or (forwarded_proto is not None and forwarded_proto != "https")
    ):
        raise IngressRejected("wrong_public_host_or_scheme")
    if now.tzinfo is None or now.utcoffset() is None:
        raise IngressRejected("clock_must_be_utc_aware")
    if now.astimezone(timezone.utc) >= settings.tester_expires_utc:
        raise IngressNotConfigured("tester_join_expired")
    form = parse_bounded_form(raw_body, content_type)
    try:
        message = TwilioWhatsAppInboundAdapter(
            settings.provider
        ).parse_verified(
            form=form, signature=signature, received_at=now,
        )
    except TwilioWebhookRejected as exc:
        raise IngressRejected("signature_or_payload_rejected") from exc
    if (
        form.get("AccountSid") != settings.provider.account_sid
        or not hmac.compare_digest(
            message.recipient.encode(), settings.provider.sender.encode()
        )
        or not hmac.compare_digest(
            message.sender.encode(), settings.allowed_tester.encode()
        )
        or form.get("NumMedia", "0") != "0"
        or len(message.body) > 500
        or not re.fullmatch(r"SM[A-Za-z0-9]{2,64}", message.provider_message_ref)
    ):
        raise IngressRejected("sandbox_sender_account_or_content_not_admitted")
    # Never trust sender text, MessageSid or signature as tenant authorization.
    # This endpoint is ingress-only, not a WND order/merchant identity bridge.
    key = hashlib.sha256(
        (message.provider_message_ref + "\x00"
         + message.recipient + "\x00"
         + settings.provider.account_sid).encode("utf-8")
    ).hexdigest()
    outcome = ledger.check_and_claim(
        key=key, digest=canonical_twilio_form_digest(form),
        now_epoch=int(now.timestamp()),
    )
    if outcome in ("collision", "unavailable"):
        raise IngressRejected("provider_receipt_conflict_or_capacity")
    return VerifiedSandboxIngress(
        disposition=outcome,
        event_digest=key[:20],
        action=_r5_test_action(message.body),
        merchant_dispatch_enabled=False,
    )

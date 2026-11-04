"""TEST-only customer-visible WND order confirmations, driven by canonical WND ACK.

No greeting/replay/customer-supplied financial claim can authorize a send.
The separately-scoped outbound PostgreSQL identity reserves a durable key
before Twilio REST and blocks retries after any uncertain provider outcome.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import UTC, datetime
from urllib.parse import urlsplit

from ..channel_contract import ChannelOutboundIntent
from ..persistence.postgres.database import DatabaseConfig, psycopg_connection_factory
from ..persistence.postgres.schema import DATABASE_URL_ENV
from ..persistence.postgres.transport_state_stores import PostgresIdempotencyResultStore
from .swes_r48_wnd_order_delivery import WNDOrderAcknowledgment
from .swes_twilio_durable_outbound import (
    SWESDurableSandboxStatusSender,
    SWESOutboundDeliveryResult,
)
from .swes_twilio_sandbox_outbound_gate import (
    SWESSandboxOutboundPolicy,
    SWESSandboxStatusSender,
)
from .twilio_signed_ingress import SandboxIngressConfig, VerifiedSandboxIngress
from .twilio_whatsapp import TwilioWhatsAppOutboundAdapter

_FLAG = "CC_R48_WND_ORDER_CONFIRMATION_ENABLED"
_DB = "CC_R48_ORDER_CONFIRMATION_DATABASE_URL"
_MERCHANT = "4b196022-55a1-548c-b122-f1f9e4b78bea"


class SWESOrderConfirmationUnavailable(RuntimeError):
    """Separate, default-off TEST-only notification custody is not ready."""


class _VerifiedOrderEvidence:
    def __init__(self, ack: WNDOrderAcknowledgment) -> None:
        self.ack = ack

    def verify(self, *, intent: ChannelOutboundIntent) -> bool:
        return (
            intent.event_type == "wnd_order_status"
            and intent.correlation_id == str(self.ack.order_ref)
            and intent.session_ref == str(self.ack.context_binding_ref)
            and intent.merchant_ref == _MERCHANT
            and self.ack.canonical_amount == 1000
            and self.ack.order_state == "SUBMITTED"
            and self.ack.order_ref.int != 0
        )


def make_order_confirmation(
    *, ack: WNDOrderAcknowledgment, verified: VerifiedSandboxIngress,
    settings: SandboxIngressConfig, now: datetime,
    env: Mapping[str, str],
) -> ChannelOutboundIntent:
    if (
        env.get(_FLAG) != "1"
        or env.get("CC_R48_WND_ORDER_DELIVERY_ENABLED") != "1"
        or env.get("CC_TWILIO_NONFINANCIAL_ONLY") != "1"
        or env.get("CC_R48_ORDER_CONFIRMATION_TESTER_CONSENTED") != "1"
        or not isinstance(ack, WNDOrderAcknowledgment)
        or ack.order_ref.int == 0 or ack.context_binding_ref.int == 0
        or ack.canonical_amount != 1000 or ack.order_state != "SUBMITTED"
        or not isinstance(verified, VerifiedSandboxIngress)
        or verified.disposition != "accepted"
        or verified.action != "test_order"
        or not verified.receipt_key or len(verified.receipt_key) != 64
        or now.tzinfo is None or now.utcoffset() is None
        or now.astimezone(UTC) >= settings.tester_expires_utc
    ):
        raise SWESOrderConfirmationUnavailable("verified_first_order_confirmation_not_admitted")
    return ChannelOutboundIntent(
        recipient=settings.allowed_tester,
        channel="whatsapp",
        merchant_ref=_MERCHANT,
        conversation_ref="swes:r48:" + verified.receipt_key,
        session_ref=str(ack.context_binding_ref),
        purpose="sandbox_status_confirmation",
        event_type="wnd_order_status",
        projection={"body": (
            "Wine & Dine / XafPay SANDBOX TEST ONLY\n"
            "Order received: " + str(ack.order_ref) + "\n"
            "Total: 1,000 XAF\n"
            "Order status: SUBMITTED\n"
            "Payment: NOT INITIATED\n"
            "This is a test order. No payment has been made."
        )},
        template_variables={},
        correlation_id=str(ack.order_ref),
    )


def send_order_confirmation(
    *, ack: WNDOrderAcknowledgment, verified: VerifiedSandboxIngress,
    settings: SandboxIngressConfig, now: datetime,
    env: Mapping[str, str] | None = None,
    sender: SWESDurableSandboxStatusSender | None = None,
) -> SWESOutboundDeliveryResult:
    values = os.environ if env is None else env
    intent = make_order_confirmation(
        ack=ack, verified=verified, settings=settings, now=now, env=values,
    )
    if sender is None:
        # An independently scoped DB identity must have INSERT/SELECT/UPDATE
        # on idempotency_result, not schema-owner or general CC app privileges.
        url = values.get(_DB, "").strip()
        general = values.get(DATABASE_URL_ENV, "").strip()
        receipt = values.get("CC_HOSTED_SANDBOX_RECEIPT_DATABASE_URL", "").strip()
        if not url or url in (general, receipt):
            raise SWESOrderConfirmationUnavailable("restricted_outbound_identity_required")
        parsed = urlsplit(url)
        if parsed.scheme not in ("postgres", "postgresql") or not parsed.hostname:
            raise SWESOrderConfirmationUnavailable("restricted_outbound_database_invalid")
        config = DatabaseConfig.from_environment({DATABASE_URL_ENV: url})
        store = PostgresIdempotencyResultStore(psycopg_connection_factory(config))
        provider = TwilioWhatsAppOutboundAdapter(config=settings.provider)
        policy = SWESSandboxOutboundPolicy(
            enabled=True,
            nonfinancial_only=True,
            tester_consented=True,
            allowed_tester=settings.allowed_tester,
            approved_sender=settings.provider.sender,
            tester_join_expires_utc=settings.tester_expires_utc,
        )
        gate = SWESSandboxStatusSender(
            config=settings.provider,
            policy=policy,
            evidence=_VerifiedOrderEvidence(ack),
            transport=provider,
        )
        sender = SWESDurableSandboxStatusSender(gate=gate, provider=provider, store=store)
    return sender.send(intent=intent, now=now)

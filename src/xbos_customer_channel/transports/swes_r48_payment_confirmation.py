"""SWES R48 payment-status consumer contract. NOT hosted-routed or active.

No direct Gateway callback or WhatsApp payload is an authority to say PAID.
An installed authenticated Gateway proof reader, WND settled-order reader,
and CC sender-binding reader must independently agree before a durable
TEST-only outbound notification may be reserved/sent.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from ..channel_contract import ChannelOutboundIntent
from .swes_twilio_durable_outbound import (
    SWESDurableSandboxStatusSender,
    SWESOutboundDeliveryResult,
)
from .twilio_signed_ingress import SandboxIngressConfig

WND_MERCHANT = UUID("4b196022-55a1-548c-b122-f1f9e4b78bea")
GATEWAY_MERCHANT = "mch_01a0a40c-d9fa-7962-95b6-706633e3bd1c"
TENANT = 910001
LOCATION = 5
AMOUNT = 1000
CURRENCY = "XAF"
FLAG = "CC_R48_GATEWAY_PAYMENT_CONFIRMATION_ENABLED"


class PaymentConfirmationNotAdmitted(PermissionError):
    """No trusted, settled and reconciled proof; never send success."""


@dataclass(frozen=True, slots=True)
class VerifiedGatewaySettlement:
    """Materialized by a future independently authenticated Gateway reader."""

    order_ref: UUID
    gateway_merchant: str
    provider: str
    payment_ref: str
    provider_transaction_ref: str
    amount: int
    currency: str
    state: str  # must be VERIFIED_SETTLED, not CHECKOUT_CREATED
    callback_evidence_ref: str
    verified_at_utc: datetime


@dataclass(frozen=True, slots=True)
class VerifiedWNDReconciliation:
    """Materialized by WND read-only canonical order+ledger authority."""

    order_ref: UUID
    merchant_ref: UUID
    tenant_id: int
    location_id: int
    amount: int
    currency: str
    order_state: str
    payment_state: str
    payment_ref: str
    gateway_provider_transaction_ref: str
    reconciled_at_utc: datetime


@dataclass(frozen=True, slots=True)
class VerifiedCCOrderRecipient:
    """Rehydrated from restricted first signed receipt, never caller-supplied To."""

    order_ref: UUID
    whatsapp_sender: str
    receipt_reference: str
    sender_provenance_verified: bool


class GatewaySettlementAuthority(Protocol):
    def read_verified_settlement(self, *, order_ref: UUID) -> VerifiedGatewaySettlement: ...


class WNDSettlementAuthority(Protocol):
    def read_reconciled_order(self, *, order_ref: UUID) -> VerifiedWNDReconciliation: ...


class CCRecipientAuthority(Protocol):
    def read_signed_order_recipient(self, *, order_ref: UUID) -> VerifiedCCOrderRecipient: ...


def qualify_payment_confirmation(
    *, order_ref: UUID, gateway: GatewaySettlementAuthority,
    wnd: WNDSettlementAuthority, recipient: CCRecipientAuthority,
    settings: SandboxIngressConfig, now: datetime,
    env: Mapping[str, str],
) -> ChannelOutboundIntent:
    """Require 3 server-side authorities; one claimed callback cannot authorize."""
    if (
        env.get(FLAG) != "1"
        or env.get("CC_R48_ORDER_CONFIRMATION_TESTER_CONSENTED") != "1"
        or env.get("CC_TWILIO_NONFINANCIAL_ONLY") != "1"
        or not isinstance(order_ref, UUID) or order_ref.int == 0
        or not isinstance(now, datetime) or now.tzinfo is None
        or now.utcoffset() is None
        or now.astimezone(UTC) >= settings.tester_expires_utc
    ):
        raise PaymentConfirmationNotAdmitted("payment_status_custody_not_admitted")
    # Readers are intentionally injected. This module exposes NO public endpoint
    # and cannot authenticate the provider or WND by itself.
    g = gateway.read_verified_settlement(order_ref=order_ref)
    w = wnd.read_reconciled_order(order_ref=order_ref)
    r = recipient.read_signed_order_recipient(order_ref=order_ref)
    if (
        not isinstance(g, VerifiedGatewaySettlement)
        or not isinstance(w, VerifiedWNDReconciliation)
        or not isinstance(r, VerifiedCCOrderRecipient)
        or g.order_ref != order_ref or w.order_ref != order_ref
        or r.order_ref != order_ref
        or g.gateway_merchant != GATEWAY_MERCHANT
        or w.merchant_ref != WND_MERCHANT
        or w.tenant_id != TENANT or w.location_id != LOCATION
        or g.provider != "tranzak"
        or not g.payment_ref or len(g.payment_ref) > 128
        or not g.provider_transaction_ref or len(g.provider_transaction_ref) > 128
        or not g.callback_evidence_ref or len(g.callback_evidence_ref) > 256
        or g.state != "VERIFIED_SETTLED"
        or w.payment_state != "PAID"
        or w.order_state not in {"SUBMITTED", "COMPLETED"}
        or g.amount != AMOUNT or w.amount != AMOUNT
        or g.currency != CURRENCY or w.currency != CURRENCY
        or w.payment_ref != g.payment_ref
        or w.gateway_provider_transaction_ref != g.provider_transaction_ref
        or not r.sender_provenance_verified or len(r.receipt_reference) != 64
        or any(c not in "0123456789abcdef" for c in r.receipt_reference)
        or r.whatsapp_sender != settings.allowed_tester
        or g.verified_at_utc.tzinfo is None
        or w.reconciled_at_utc.tzinfo is None
        or g.verified_at_utc > now or w.reconciled_at_utc > now
    ):
        raise PaymentConfirmationNotAdmitted("gateway_wnd_cc_evidence_mismatch")
    return ChannelOutboundIntent(
        channel="whatsapp", recipient=settings.allowed_tester,
        merchant_ref=str(WND_MERCHANT),
        conversation_ref="swes:r48:" + r.receipt_reference,
        session_ref=str(order_ref),
        purpose="sandbox_status_confirmation",
        event_type="gateway_payment_status",
        correlation_id=g.payment_ref,
        projection={"body": (
            "Wine & Dine / XafPay SANDBOX TEST ONLY\n"
            "Payment confirmed for order: " + str(order_ref) + "\n"
            "Paid: 1,000 XAF\n"
            "Status: PAID (Gateway verified + WND reconciled)\n"
            "This is a sandbox transaction."
        )},
        template_variables={},
    )


def send_verified_payment_confirmation(
    *, order_ref: UUID, gateway: GatewaySettlementAuthority,
    wnd: WNDSettlementAuthority, recipient: CCRecipientAuthority,
    settings: SandboxIngressConfig, now: datetime,
    sender: SWESDurableSandboxStatusSender,
    env: Mapping[str, str] | None = None,
) -> SWESOutboundDeliveryResult:
    """Stage-only composition; no live route until all three readers are installed."""
    intent = qualify_payment_confirmation(
        order_ref=order_ref, gateway=gateway, wnd=wnd, recipient=recipient,
        settings=settings, now=now, env=os.environ if env is None else env,
    )
    return sender.send(intent=intent, now=now)

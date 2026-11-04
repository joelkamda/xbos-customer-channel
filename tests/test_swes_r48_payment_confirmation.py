"""No network: Gateway callback alone cannot produce a paid WhatsApp message."""
from __future__ import annotations

import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xbos_customer_channel.transports.swes_r48_payment_confirmation import (
    GATEWAY_MERCHANT,
    WND_MERCHANT,
    PaymentConfirmationNotAdmitted,
    VerifiedCCOrderRecipient,
    VerifiedGatewaySettlement,
    VerifiedWNDReconciliation,
    qualify_payment_confirmation,
    send_verified_payment_confirmation,
)
from xbos_customer_channel.transports.swes_twilio_durable_outbound import (
    SWESOutboundDeliveryResult,
)
from xbos_customer_channel.transports.twilio_signed_ingress import SandboxIngressConfig
from xbos_customer_channel.transports.twilio_whatsapp import TwilioWhatsAppConfig

NOW = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)
ORDER = uuid4()
SETTINGS = SandboxIngressConfig(
    provider=TwilioWhatsAppConfig(
        account_sid="AC" + "1" * 32,
        auth_token="fake-not-live",
        sender="whatsapp:+14155238886",
        webhook_url="https://xafpay-customer-channel-staging.onrender.com/webhooks/twilio/whatsapp",
    ),
    allowed_tester="whatsapp:+237600000001",
    tester_expires_utc=NOW + timedelta(hours=24),
    expected_host="xafpay-customer-channel-staging.onrender.com",
)
ENV = {
    "CC_R48_GATEWAY_PAYMENT_CONFIRMATION_ENABLED": "1",
    "CC_R48_ORDER_CONFIRMATION_TESTER_CONSENTED": "1",
    "CC_TWILIO_NONFINANCIAL_ONLY": "1",
}
G = VerifiedGatewaySettlement(
    order_ref=ORDER, gateway_merchant=GATEWAY_MERCHANT,
    provider="tranzak", payment_ref="pay-r48-real-proof",
    provider_transaction_ref="sandbox-transaction-attested",
    amount=1000, currency="XAF", state="VERIFIED_SETTLED",
    callback_evidence_ref="independent-proof-ref",
    verified_at_utc=NOW - timedelta(seconds=5),
)
W = VerifiedWNDReconciliation(
    order_ref=ORDER, merchant_ref=WND_MERCHANT,
    tenant_id=910001, location_id=5, amount=1000, currency="XAF",
    order_state="SUBMITTED", payment_state="PAID",
    payment_ref=G.payment_ref,
    gateway_provider_transaction_ref=G.provider_transaction_ref,
    reconciled_at_utc=NOW - timedelta(seconds=1),
)
R = VerifiedCCOrderRecipient(
    order_ref=ORDER, whatsapp_sender=SETTINGS.allowed_tester,
    receipt_reference="a" * 64, sender_provenance_verified=True,
)


class Gateway:
    def __init__(self, value=G):
        self.value, self.calls = value, 0

    def read_verified_settlement(self, *, order_ref):
        assert order_ref == ORDER
        self.calls += 1
        return self.value


class WND:
    def __init__(self, value=W):
        self.value, self.calls = value, 0

    def read_reconciled_order(self, *, order_ref):
        assert order_ref == ORDER
        self.calls += 1
        return self.value


class Recipient:
    def __init__(self, value=R):
        self.value, self.calls = value, 0

    def read_signed_order_recipient(self, *, order_ref):
        assert order_ref == ORDER
        self.calls += 1
        return self.value


class Sender:
    def __init__(self):
        self.calls = []

    def send(self, *, intent, now):
        self.calls.append(intent)
        return SWESOutboundDeliveryResult("SM" + "1" * 32, False)


def qualify(**changes):
    args = {
        "order_ref": ORDER, "gateway": Gateway(), "wnd": WND(),
        "recipient": Recipient(), "settings": SETTINGS,
        "now": NOW, "env": ENV,
    }
    args.update(changes)
    return qualify_payment_confirmation(**args)


def test_three_independent_matching_readbacks_allow_paid_intent():
    intent = qualify()
    assert intent.event_type == "gateway_payment_status"
    assert intent.correlation_id == G.payment_ref
    assert intent.session_ref == str(ORDER)
    assert intent.recipient == SETTINGS.allowed_tester
    assert str(ORDER) in intent.projection["body"]
    assert "PAID" in intent.projection["body"]
    assert "sandbox" in intent.projection["body"].lower()


@pytest.mark.parametrize("changes", [
    {"gateway": Gateway(replace(G, state="CHECKOUT_CREATED"))},
    {"gateway": Gateway(replace(G, state="CALLBACK_RECEIVED"))},
    {"gateway": Gateway(replace(G, amount=999))},
    {"gateway": Gateway(replace(G, gateway_merchant="wrong"))},
    {"gateway": Gateway(replace(G, provider="twilio"))},
    {"gateway": Gateway(replace(G, payment_ref="different"))},
    {"gateway": Gateway(replace(G, provider_transaction_ref="other"))},
    {"gateway": Gateway(replace(G, order_ref=uuid4()))},
    {"gateway": Gateway(replace(G, verified_at_utc=NOW + timedelta(minutes=1)))},
    {"wnd": WND(replace(W, payment_state="NOT_INITIATED"))},
    {"wnd": WND(replace(W, payment_state="PENDING"))},
    {"wnd": WND(replace(W, payment_ref="other"))},
    {"wnd": WND(replace(W, gateway_provider_transaction_ref="other"))},
    {"wnd": WND(replace(W, merchant_ref=uuid4()))},
    {"wnd": WND(replace(W, tenant_id=2))},
    {"wnd": WND(replace(W, amount=999))},
    {"wnd": WND(replace(W, reconciled_at_utc=NOW + timedelta(seconds=1)))},
    {"recipient": Recipient(replace(R, sender_provenance_verified=False))},
    {"recipient": Recipient(replace(R, whatsapp_sender="whatsapp:+237600000002"))},
    {"recipient": Recipient(replace(R, receipt_reference="wrong"))},
    {"recipient": Recipient(replace(R, order_ref=uuid4()))},
    {"env": {**ENV, "CC_R48_GATEWAY_PAYMENT_CONFIRMATION_ENABLED": "0"}},
    {"env": {**ENV, "CC_R48_ORDER_CONFIRMATION_TESTER_CONSENTED": "0"}},
    {"now": SETTINGS.tester_expires_utc},
])
def test_checkout_or_callback_without_full_reconciliation_never_paid(changes):
    with pytest.raises(PaymentConfirmationNotAdmitted):
        qualify(**changes)


def test_gate_denial_prevents_any_reader_access():
    g, w, r = Gateway(), WND(), Recipient()
    with pytest.raises(PaymentConfirmationNotAdmitted):
        qualify(env={}, gateway=g, wnd=w, recipient=r)
    assert g.calls == w.calls == r.calls == 0


def test_bounded_injected_sender_called_only_on_verified_settlement():
    sender = Sender()
    result = send_verified_payment_confirmation(
        order_ref=ORDER, gateway=Gateway(), wnd=WND(),
        recipient=Recipient(), settings=SETTINGS, now=NOW,
        sender=sender, env=ENV,
    )
    assert result.provider_message_ref.startswith("SM")
    assert len(sender.calls) == 1
    assert sender.calls[0].correlation_id == G.payment_ref
    denied = Sender()
    with pytest.raises(PaymentConfirmationNotAdmitted):
        send_verified_payment_confirmation(
            order_ref=ORDER, gateway=Gateway(),
            wnd=WND(replace(W, payment_state="NOT_INITIATED")),
            recipient=Recipient(), settings=SETTINGS,
            now=NOW, sender=denied, env=ENV,
        )
    assert not denied.calls

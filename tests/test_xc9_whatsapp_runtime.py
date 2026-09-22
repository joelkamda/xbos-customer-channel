from __future__ import annotations

import hashlib
import hmac
import json
import sys
import unittest
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xbos_customer_channel.adapters.fake_catalog import FakeXBOSCatalogClient
from xbos_customer_channel.adapters.fake_context import FakeXBOSContextClient
from xbos_customer_channel.application.catalog_service import CatalogQuoteService
from xbos_customer_channel.application.w1_checkout_ux import (
    W1CheckoutUX,
    W1CustomerSafePaymentOptions,
)
from xbos_customer_channel.application.w1_composition import compose_w1_whatsapp_runtime
from xbos_customer_channel.application.w1_whatsapp_runtime import W1RuntimeConversationState
from xbos_customer_channel.catalog import AvailabilityState, QuoteLineSnapshot
from xbos_customer_channel.entry_context import EntryPurpose, ResolvedEntryContext
from xbos_customer_channel.order import (
    AuthoritativeOrderConfirmationSnapshot,
    CanonicalOrderProjection,
    CanonicalOrderState,
    ServiceMode,
)
from xbos_customer_channel.payment_experience import PaymentMethodCode
from xbos_customer_channel.session_state import ChannelState, CustomerSessionSnapshot
from xbos_customer_channel.transports.meta_whatsapp import (
    MetaHttpResponse,
    MetaWhatsAppConfig,
    MetaWhatsAppInboundAdapter,
    MetaWhatsAppOutboundAdapter,
)


def config() -> MetaWhatsAppConfig:
    return MetaWhatsAppConfig("v99.0", "phone-id", "secret", "verify", "token")


def payload(action: str, *, list_reply: bool = False) -> bytes:
    kind = "list_reply" if list_reply else "button_reply"
    message = {
        "id": f"wamid.{action}",
        "from": "raw-phone",
        "timestamp": "10",
        "type": "interactive",
        "interactive": {
            "type": kind,
            kind: {"id": action, "title": action},
        },
    }
    value = {
        "metadata": {"phone_number_id": "phone-id"},
        "messages": [message],
    }
    return json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [{"changes": [{"field": "messages", "value": value}]}],
        }
    ).encode()


def signature(raw: bytes) -> str:
    return "sha256=" + hmac.new(b"secret", raw, hashlib.sha256).hexdigest()


class RecordingHttp:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def post(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> MetaHttpResponse:
        del url, headers, timeout_seconds
        self.calls.append(json.loads(body))
        return MetaHttpResponse(
            200,
            json.dumps({"messages": [{"id": f"wamid.out.{len(self.calls)}"}]}).encode(),
        )


def catalog_with_cart():
    context = FakeXBOSContextClient().resolve_context(
        merchant_ref="merchant:fixture:alpha",
        location_ref="location:fixture:one",
        table_ref="table:fixture:a1",
        purpose=EntryPurpose.DINE_IN,
    )
    entry = ResolvedEntryContext(
        "entry:runtime",
        "merchant:fixture:alpha",
        "location:fixture:one",
        "table:fixture:a1",
        "area:fixture:main",
        EntryPurpose.DINE_IN,
        context,
    )
    service = CatalogQuoteService(FakeXBOSCatalogClient())
    return service, service.add_to_cart(
        service.browse(entry),
        item_ref="item:fixture:one",
        quantity=1,
    )


def secure_session() -> CustomerSessionSnapshot:
    return CustomerSessionSnapshot(
        "sess:runtime",
        "conversation:runtime",
        "corr:runtime",
        ChannelState.BROWSING,
        owner_identity_ref="identity:runtime",
        merchant_ref="merchant:fixture:alpha",
        location_ref="location:fixture:one",
        table_ref="table:fixture:a1",
        dining_area_ref="area:fixture:main",
        entry_purpose=EntryPurpose.DINE_IN,
        context_binding_ref="ctxbind:runtime",
        security_binding_complete=True,
    )


class RuntimeBoundary:
    def __init__(self) -> None:
        self.order_calls = 0
        self.payment_request_calls = 0

    def prepare_order_review(self, *, session, catalog_session, service_mode):
        del session, catalog_session
        return AuthoritativeOrderConfirmationSnapshot(
            confirmation_ref=f"confirmation:runtime:{service_mode.value}",
            quote_ref="quote:runtime",
            quote_version="v1",
            merchant_ref="merchant:fixture:alpha",
            location_ref="location:fixture:one",
            service_context_ref=f"service:runtime:{service_mode.value}",
            service_mode=service_mode,
            lines=(
                QuoteLineSnapshot(
                    item_ref="item:fixture:one",
                    quantity=1,
                    unit_price=Decimal("3000"),
                    line_total=Decimal("3000"),
                    availability=AvailabilityState.AVAILABLE,
                ),
            ),
            delivery_fee=None,
            taxes_charges=(),
            total=Decimal("3000"),
            currency="XAF",
            expires_at_epoch=5000,
        )

    def submit_order(self, *, session, confirmation):
        del session
        self.order_calls += 1
        return CanonicalOrderProjection(
            order_ref="order:runtime:1",
            client_submit_ref="client-submit:runtime:1",
            correlation_ref="corr:runtime",
            confirmation_ref=confirmation.confirmation_ref,
            state=CanonicalOrderState.CONFIRMED,
            evidence_ref="evidence:runtime:order",
        )

    def payment_options(self, *, session, order):
        del session
        self.payment_request_calls += 1
        return W1CustomerSafePaymentOptions(
            payment_request_ref="payment-request:runtime:1",
            order_ref=order.order_ref,
            canonical_amount=Decimal("3000"),
            currency="XAF",
            permitted_methods=(
                PaymentMethodCode.PAY_AT_COUNTER,
                PaymentMethodCode.MTN_MOBILE_MONEY,
                PaymentMethodCode.ORANGE_MONEY,
            ),
        )


class StateStore:
    def __init__(self, state: W1RuntimeConversationState) -> None:
        self.state = state
        self.loads = 0
        self.saves = 0

    def load(self, inbound):
        del inbound
        self.loads += 1
        return self.state

    def save(self, inbound, state):
        del inbound
        self.saves += 1
        self.state = state


class FailingStateStore:
    def load(self, inbound):
        del inbound
        raise RuntimeError("raw_internal_error_should_not_escape")

    def save(self, inbound, state):
        del inbound, state
        raise AssertionError("not_reached")


class XC9WhatsAppRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.http = RecordingHttp()
        catalog_service, catalog_session = catalog_with_cart()
        self.boundary = RuntimeBoundary()
        self.store = StateStore(
            W1RuntimeConversationState(
                session=secure_session(),
                catalog_session=catalog_session,
            )
        )
        self.runtime = compose_w1_whatsapp_runtime(
            inbound=MetaWhatsAppInboundAdapter(config()),
            outbound=MetaWhatsAppOutboundAdapter(
                config=config(),
                http_client=self.http,
            ),
            catalog=catalog_service,
            checkout=W1CheckoutUX(self.boundary),
            state_port=self.store,
        )

    def send(self, action: str, *, list_reply: bool = False):
        raw = payload(action, list_reply=list_reply)
        return self.runtime.handle_webhook(raw_body=raw, signature=signature(raw))

    def test_interactive_reply_router_and_outbound_composition(self) -> None:
        self.send("cart")
        self.send("checkout")
        self.send("service:dine_in")
        self.send("order:confirm")
        self.send("pay:mtn_mobile_money", list_reply=True)

        self.assertEqual(self.store.loads, 5)
        self.assertEqual(self.store.saves, 5)
        self.assertEqual(self.boundary.order_calls, 1)
        self.assertEqual(self.boundary.payment_request_calls, 1)
        self.assertEqual(
            [call["type"] for call in self.http.calls],
            ["interactive", "interactive", "interactive", "interactive", "text"],
        )
        self.assertEqual(self.http.calls[0]["interactive"]["type"], "button")
        self.assertEqual(self.http.calls[3]["interactive"]["type"], "list")
        self.assertIn(
            "has not started yet",
            self.http.calls[4]["text"]["body"],
        )

    def test_payment_method_list_has_only_current_c3_methods(self) -> None:
        self.send("checkout")
        self.send("service:dine_in")
        self.send("order:confirm")
        payment_message = self.http.calls[-1]["interactive"]
        rows = payment_message["action"]["sections"][0]["rows"]
        ids = [row["id"] for row in rows]
        self.assertEqual(
            ids,
            [
                "pay:pay_at_counter",
                "pay:mtn_mobile_money",
                "pay:orange_money",
            ],
        )
        self.assertNotIn("pay:xafpay_wallet", ids)
        self.assertNotIn("pay:card", ids)

    def test_meta_composition_requires_no_real_network(self) -> None:
        sent = self.send("cart")
        self.assertEqual(len(sent), 1)
        self.assertEqual(len(self.http.calls), 1)
        self.assertEqual(self.http.calls[0]["to"], "raw-phone")

    def test_runtime_fail_closed_does_not_expose_internal_error(self) -> None:
        http = RecordingHttp()
        catalog_service, _ = catalog_with_cart()
        runtime = compose_w1_whatsapp_runtime(
            inbound=MetaWhatsAppInboundAdapter(config()),
            outbound=MetaWhatsAppOutboundAdapter(
                config=config(),
                http_client=http,
            ),
            catalog=catalog_service,
            checkout=W1CheckoutUX(self.boundary),
            state_port=FailingStateStore(),
        )
        raw = payload("checkout")
        runtime.handle_webhook(raw_body=raw, signature=signature(raw))
        body = http.calls[0]["text"]["body"]
        self.assertIn("could not continue", body)
        self.assertNotIn("raw_internal_error", body)

    def test_method_selection_is_not_payment_pending_or_success(self) -> None:
        self.send("checkout")
        self.send("service:dine_in")
        self.send("order:confirm")
        self.send("pay:orange_money", list_reply=True)
        body = self.http.calls[-1]["text"]["body"].casefold()
        self.assertNotIn("await_push", body)
        self.assertNotIn("payment pending", body)
        self.assertNotIn("payment succeeded", body)
        self.assertNotIn("payment confirmed", body)


if __name__ == "__main__":
    unittest.main()

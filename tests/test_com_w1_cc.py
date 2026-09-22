from __future__ import annotations

import hashlib
import hmac
import json
import sys
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xbos_customer_channel.adapters.fake_catalog import FakeXBOSCatalogClient
from xbos_customer_channel.adapters.fake_context import FakeXBOSContextClient
from xbos_customer_channel.application.catalog_service import CatalogQuoteService
from xbos_customer_channel.application.w1_composition import (
    REAL_XBOS_ADAPTER_STATE,
    XBOSW1ContractUnavailable,
    require_real_xbos_w1_contract,
)
from xbos_customer_channel.application.w1_conversation import (
    W1ConversationRouter,
    W1NavigationCursor,
    W1RenderKind,
)
from xbos_customer_channel.entry_context import EntryPurpose, ResolvedEntryContext
from xbos_customer_channel.session_state import ChannelState, CustomerSessionSnapshot
from xbos_customer_channel.transports.meta_whatsapp import (
    DeliveryState,
    InteractiveButton,
    InteractiveListRow,
    InteractiveListSection,
    InvalidWebhookSignature,
    MetaHttpResponse,
    MetaProviderError,
    MetaRateLimitError,
    MetaWhatsAppConfig,
    MetaWhatsAppInboundAdapter,
    MetaWhatsAppOutboundAdapter,
    MissingWebhookSignature,
    NormalizedInboundMessage,
    ProviderPayloadRejected,
    UntrustedProviderUserRef,
)


def config() -> MetaWhatsAppConfig:
    return MetaWhatsAppConfig("v99.0", "phone-id", "secret", "verify", "token")


def inbound_payload(*, message: dict[str, object] | None = None, status: dict[str, object] | None = None) -> bytes:
    value: dict[str, object] = {"metadata": {"phone_number_id": "phone-id"}}
    if message is not None:
        value["messages"] = [message]
    if status is not None:
        value["statuses"] = [status]
    return json.dumps({"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": value}]}]}).encode()


def signed(raw_body: bytes) -> str:
    return "sha256=" + hmac.new(b"secret", raw_body, hashlib.sha256).hexdigest()


class RecordingHttp:
    def __init__(self, response: MetaHttpResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, Mapping[str, str], dict[str, Any]]] = []

    def post(self, *, url: str, headers: Mapping[str, str], body: bytes, timeout_seconds: float) -> MetaHttpResponse:
        del timeout_seconds
        self.calls.append((url, headers, json.loads(body)))
        return self.response


class MetaInboundTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = MetaWhatsAppInboundAdapter(config())

    def test_webhook_subscription_verification(self) -> None:
        self.assertEqual(self.adapter.verify_subscription(mode="subscribe", verify_token="verify", challenge="challenge"), "challenge")
        self.assertIsNone(self.adapter.verify_subscription(mode="subscribe", verify_token="wrong", challenge="challenge"))

    def test_missing_and_invalid_signatures_reject_before_trust(self) -> None:
        raw = inbound_payload(message={"id": "wamid.1", "from": "raw-phone", "timestamp": "1", "type": "text", "text": {"body": "Menu"}})
        with self.assertRaises(MissingWebhookSignature):
            self.adapter.receive(raw_body=raw, signature=None)
        with self.assertRaises(InvalidWebhookSignature):
            self.adapter.receive(raw_body=raw, signature="sha256=wrong")

    def test_malformed_and_unsupported_payloads_reject_safely(self) -> None:
        raw = b"{"
        with self.assertRaises(ProviderPayloadRejected):
            self.adapter.receive(raw_body=raw, signature=signed(raw))
        unsupported = inbound_payload(message={"id": "wamid.1", "from": "raw-phone", "timestamp": "1", "type": "image"})
        with self.assertRaisesRegex(ProviderPayloadRejected, "unsupported_message_type"):
            self.adapter.receive(raw_body=unsupported, signature=signed(unsupported))

    def test_text_message_preserves_provider_reference_and_untrusted_locator(self) -> None:
        raw = inbound_payload(message={"id": "wamid.1", "from": "raw-phone", "timestamp": "1", "type": "text", "text": {"body": "Menu"}, "context": {"id": "wamid.parent"}})
        message = self.adapter.receive(raw_body=raw, signature=signed(raw)).messages[0]
        self.assertEqual(message.provider_message_ref, "wamid.1")
        self.assertEqual(message.sender.value, "raw-phone")
        self.assertEqual(message.text, "Menu")
        self.assertIsNone(message.interactive_reply)
        self.assertEqual(message.context_provider_message_ref, "wamid.parent")
        self.assertNotIn("identity", message.__dataclass_fields__)

    def test_button_and_list_replies_normalize(self) -> None:
        for kind in ("button_reply", "list_reply"):
            raw = inbound_payload(message={"id": "wamid.2", "from": "raw-phone", "timestamp": "2", "type": "interactive", "interactive": {"type": kind, kind: {"id": "menu", "title": "Menu"}}})
            interaction = self.adapter.receive(raw_body=raw, signature=signed(raw)).messages[0].interactive_reply
            self.assertIsNotNone(interaction)
            assert interaction is not None
            self.assertEqual(interaction.reply_id, "menu")

    def test_delivery_status_normalizes_without_customer_authority(self) -> None:
        raw = inbound_payload(status={"id": "wamid.out", "status": "delivered", "timestamp": "3", "recipient_id": "raw-phone"})
        status = self.adapter.receive(raw_body=raw, signature=signed(raw)).delivery_statuses[0]
        self.assertEqual(status.state, DeliveryState.DELIVERED)
        self.assertEqual(status.recipient, UntrustedProviderUserRef("raw-phone"))


class MetaOutboundTests(unittest.TestCase):
    def setUp(self) -> None:
        self.http = RecordingHttp(MetaHttpResponse(200, b'{"messages":[{"id":"wamid.out"}]}'))
        self.adapter = MetaWhatsAppOutboundAdapter(config=config(), http_client=self.http)
        self.recipient = UntrustedProviderUserRef("raw-phone")

    def test_text_button_and_list_mapping(self) -> None:
        self.assertEqual(self.adapter.send_text(recipient=self.recipient, body="Hello").provider_message_ref, "wamid.out")
        self.adapter.send_buttons(recipient=self.recipient, body="Choose", buttons=(InteractiveButton("menu", "Menu"),))
        self.adapter.send_list(recipient=self.recipient, body="Browse", button_label="Menu", sections=(InteractiveListSection("Main", (InteractiveListRow("item:1", "One"),)),))
        self.assertEqual([call[2]["type"] for call in self.http.calls], ["text", "interactive", "interactive"])
        self.assertEqual(self.http.calls[1][2]["interactive"]["type"], "button")
        self.assertEqual(self.http.calls[2][2]["interactive"]["type"], "list")
        self.assertNotIn("identity", self.http.calls[0][2])

    def test_rate_limit_and_provider_errors_are_typed(self) -> None:
        limited = MetaWhatsAppOutboundAdapter(config=config(), http_client=RecordingHttp(MetaHttpResponse(429, b'{"error":{"code":4}}')))
        with self.assertRaises(MetaRateLimitError):
            limited.send_text(recipient=self.recipient, body="Hello")
        failed = MetaWhatsAppOutboundAdapter(config=config(), http_client=RecordingHttp(MetaHttpResponse(400, b'{"error":{"code":100}}')))
        with self.assertRaises(MetaProviderError):
            failed.send_text(recipient=self.recipient, body="Hello")


def secure_session() -> CustomerSessionSnapshot:
    return CustomerSessionSnapshot("sess_server", "conversation_server", "corr_server", ChannelState.BROWSING, owner_identity_ref="identity_server", security_binding_complete=True)


def catalog_session():
    context = FakeXBOSContextClient().resolve_context(merchant_ref="merchant:fixture:alpha", location_ref="location:fixture:one", table_ref="table:fixture:a1", purpose=EntryPurpose.DINE_IN)
    entry = ResolvedEntryContext("entry_server", "merchant:fixture:alpha", "location:fixture:one", "table:fixture:a1", "area:fixture:main", EntryPurpose.DINE_IN, context)
    return CatalogQuoteService(FakeXBOSCatalogClient()).browse(entry)


def inbound_action(action: str) -> NormalizedInboundMessage:
    raw = inbound_payload(message={"id": "wamid.route", "from": "raw-phone", "timestamp": "4", "type": "interactive", "interactive": {"type": "button_reply", "button_reply": {"id": action, "title": action}}})
    return MetaWhatsAppInboundAdapter(config()).receive(raw_body=raw, signature=signed(raw)).messages[0]


class W1ConversationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = CatalogQuoteService(FakeXBOSCatalogClient())
        self.router = W1ConversationRouter(self.catalog)
        self.session = secure_session()
        self.browse = catalog_session()

    def test_greeting_category_product_quantity_and_cart_use_existing_primitives(self) -> None:
        root = self.router.route(session=self.session, catalog_session=self.browse, navigation=W1NavigationCursor(), inbound=inbound_action("menu"))
        self.assertEqual(root.rendered.kind, W1RenderKind.LIST)
        section_ref = self.browse.projection.sections[0].section_ref
        section = self.router.route(session=self.session, catalog_session=root.catalog_session, navigation=root.navigation, inbound=inbound_action(f"section:{section_ref}"))
        item_ref = self.browse.projection.items[0].item_ref
        item = self.router.route(session=self.session, catalog_session=section.catalog_session, navigation=section.navigation, inbound=inbound_action(f"item:{item_ref}"))
        quantity = self.router.route(session=self.session, catalog_session=item.catalog_session, navigation=item.navigation, inbound=inbound_action("qty:2"))
        added = self.router.route(session=self.session, catalog_session=quantity.catalog_session, navigation=quantity.navigation, inbound=inbound_action("add"))
        self.assertEqual(added.catalog_session.cart.lines[0].quantity, 2)
        self.assertEqual(added.catalog_session.cart.lines[0].item_ref, item_ref)
        self.assertIn("cart", {button.reply_id for button in added.rendered.buttons})

    def test_u1_runtime_composition_has_no_hardwired_fake_domain_clients(self) -> None:
        app_root = Path(__file__).resolve().parents[1] / "src" / "xbos_customer_channel" / "application"
        source = "\n".join(
            (app_root / name).read_text(encoding="utf-8")
            for name in ("w1_composition.py", "w1_whatsapp_runtime.py", "w1_conversation.py")
        )
        self.assertNotIn("FakeXBOSOrderClient", source)
        self.assertNotIn("FakeXBOSPaymentRequestClient", source)

    def test_help_back_restart_and_unsupported_are_safe(self) -> None:
        for action in ("help", "back", "restart", "unknown"):
            result = self.router.route(session=self.session, catalog_session=self.browse, navigation=W1NavigationCursor(), inbound=inbound_action(action))
            self.assertTrue(result.rendered.body)

    def test_router_requires_existing_server_issued_session_binding(self) -> None:
        unbound = CustomerSessionSnapshot("sess", "conv", "corr", ChannelState.BROWSING)
        with self.assertRaisesRegex(PermissionError, "server_session_binding_required"):
            self.router.route(session=unbound, catalog_session=self.browse, navigation=W1NavigationCursor(), inbound=inbound_action("menu"))

    def test_real_xbos_composition_waits_for_contract_and_never_uses_fixture(self) -> None:
        self.assertEqual(REAL_XBOS_ADAPTER_STATE, "WAITING_FOR_XBOS_CONTRACT")
        with self.assertRaises(XBOSW1ContractUnavailable):
            require_real_xbos_w1_contract(context=FakeXBOSContextClient(), catalog=FakeXBOSCatalogClient())


if __name__ == "__main__":
    unittest.main()

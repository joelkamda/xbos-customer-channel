from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xbos_customer_channel.channel_contract import (
    ChannelCommandType,
    ChannelOutboundIntent,
)
from xbos_customer_channel.persistence.postgres.transport_state_stores import (
    ProviderMessageReceipt,
    TransportDelivery,
)
from xbos_customer_channel.transports.twilio_demo_runtime import (
    TwilioWhatsAppDemoRuntime,
)
from xbos_customer_channel.transports.twilio_whatsapp import (
    TwilioChannelContext,
    TwilioHttpResponse,
    TwilioWebhookSignatureInvalid,
    TwilioWhatsAppConfig,
    TwilioWhatsAppInboundAdapter,
    TwilioWhatsAppOutboundAdapter,
    twilio_signature,
)


NOW = datetime(2026, 10, 8, 16, 0, tzinfo=timezone.utc)
AUTH_TOKEN = "test-auth-token"
WEBHOOK_URL = "https://demo.example.test/demo/twilio/whatsapp"


def config() -> TwilioWhatsAppConfig:
    return TwilioWhatsAppConfig(
        account_sid="AC00000000000000000000000000000001",
        auth_token=AUTH_TOKEN,
        sender="whatsapp:+14155238886",
        webhook_url=WEBHOOK_URL,
    )


def form(*, sid: str = "SM0001", body: str = "menu") -> dict[str, str]:
    return {
        "AccountSid": "AC00000000000000000000000000000001",
        "MessageSid": sid,
        "From": "whatsapp:+237600000001",
        "To": "whatsapp:+14155238886",
        "Body": body,
        "NumMedia": "0",
        "WaId": "237600000001",
    }


class RecordingHttp:
    def __init__(self, *, status_code: int = 201) -> None:
        self.status_code = status_code
        self.calls: list[dict[str, object]] = []

    def post_form(self, **kwargs):
        safe = dict(kwargs)
        safe["auth_token"] = "<redacted>"
        self.calls.append(safe)
        if 200 <= self.status_code < 300:
            body = json.dumps({"sid": "SMOUT0001"}).encode()
        else:
            body = json.dumps({"code": 21614}).encode()
        return TwilioHttpResponse(self.status_code, body)


class FakeReceiptStore:
    def __init__(self) -> None:
        self.by_key: dict[tuple[str, str, str, str], ProviderMessageReceipt] = {}
        self.finished: list[dict[str, object]] = []

    def get_or_create(self, **kwargs):
        key = (
            kwargs["provider_code"],
            kwargs["provider_endpoint_ref"],
            kwargs["provider_message_ref"],
            kwargs["event_kind"],
        )
        current = self.by_key.get(key)
        if current is not None:
            return current, False
        row = ProviderMessageReceipt(
            provider_message_receipt_ref=UUID(
                "00000000-0000-0000-0000-000000000701"
            ),
            provider_code=kwargs["provider_code"],
            provider_endpoint_ref=kwargs["provider_endpoint_ref"],
            provider_message_ref=kwargs["provider_message_ref"],
            event_kind=kwargs["event_kind"],
            processing_state="received",
            processing_attempt=0,
            row_version=1,
        )
        self.by_key[key] = row
        return row, True

    def begin_processing(self, **kwargs):
        return ProviderMessageReceipt(
            provider_message_receipt_ref=kwargs["receipt_ref"],
            provider_code="twilio_whatsapp",
            provider_endpoint_ref="+14155238886",
            provider_message_ref="SM0001",
            event_kind="message",
            processing_state="processing",
            processing_attempt=1,
            row_version=kwargs["expected_row_version"] + 1,
        )

    def finish_processing(self, **kwargs):
        self.finished.append(kwargs)
        return ProviderMessageReceipt(
            provider_message_receipt_ref=kwargs["receipt_ref"],
            provider_code="twilio_whatsapp",
            provider_endpoint_ref="+14155238886",
            provider_message_ref="SM0001",
            event_kind="message",
            processing_state=kwargs["processing_state"],
            processing_attempt=1,
            row_version=kwargs["expected_row_version"] + 1,
        )


class FakeDeliveryStore:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []
        self.outcomes: list[dict[str, object]] = []

    def create_pending(self, **kwargs):
        self.created.append(kwargs)
        return TransportDelivery(
            delivery_ref=UUID("00000000-0000-0000-0000-000000000702"),
            receipt_ref=kwargs["receipt_ref"],
            render_ordinal=kwargs["render_ordinal"],
            provider_code=kwargs["provider_code"],
            provider_message_ref=None,
            delivery_state="pending_send",
            row_version=1,
        )

    def record_send_outcome(self, **kwargs):
        self.outcomes.append(kwargs)
        return TransportDelivery(
            delivery_ref=kwargs["delivery_ref"],
            receipt_ref=UUID("00000000-0000-0000-0000-000000000701"),
            render_ordinal=0,
            provider_code="twilio_whatsapp",
            provider_message_ref=kwargs["provider_message_ref"],
            delivery_state=kwargs["delivery_state"],
            row_version=kwargs["expected_row_version"] + 1,
        )


class CCWAOrderR2TwilioTests(unittest.TestCase):
    def test_twilio_inbound_maps_to_same_channel_inbound_event(self):
        cfg = config()
        adapter = TwilioWhatsAppInboundAdapter(cfg)
        values = form()
        signature = twilio_signature(
            auth_token=AUTH_TOKEN,
            url=WEBHOOK_URL,
            form=values,
        )
        context = TwilioChannelContext(
            conversation_ref="conv:r2",
            session_ref="sess:r2",
            merchant_ref="merchant:wnd",
            subject_ref="subject:r2",
            correlation_ref="corr:r2",
            trusted_session=True,
        )
        event = adapter.receive(
            form=values,
            signature=signature,
            context=context,
            received_at=NOW,
        )
        self.assertEqual(event.channel, "whatsapp")
        self.assertEqual(event.provider_message_id, "SM0001")
        self.assertEqual(event.sender_identity, "+237600000001")
        self.assertEqual(event.recipient_identity, "+14155238886")
        self.assertEqual(event.message_type, "text")
        self.assertEqual(event.payload, {"text": "menu"})
        self.assertEqual(event.occurred_at, NOW)
        self.assertEqual(event.safe_provider_metadata["provider"], "twilio_whatsapp")

    def test_invalid_twilio_signature_fails_closed(self):
        adapter = TwilioWhatsAppInboundAdapter(config())
        with self.assertRaises(TwilioWebhookSignatureInvalid):
            adapter.receive(
                form=form(),
                signature="invalid",
                context=TwilioChannelContext.unbound("cevt_" + "a" * 64),
                received_at=NOW,
            )

    def test_twilio_outbound_consumes_channel_outbound_intent(self):
        http = RecordingHttp()
        adapter = TwilioWhatsAppOutboundAdapter(
            config=config(),
            http_client=http,
        )
        intent = ChannelOutboundIntent(
            recipient="+237600000001",
            channel="whatsapp",
            merchant_ref="transport-unbound:merchant",
            conversation_ref="transport-unbound:conversation:1",
            session_ref="transport-unbound:session:1",
            purpose="transport_qualification",
            event_type="twilio_demo_response",
            projection={"kind": "text", "body": "Transport qualification passed."},
            template_variables={},
            correlation_id="corr:r2",
        )
        result = adapter.send(intent)
        self.assertEqual(result.provider_message_ref, "SMOUT0001")
        self.assertEqual(len(http.calls), 1)
        sent_form = http.calls[0]["form"]
        self.assertEqual(sent_form["From"], "whatsapp:+14155238886")
        self.assertEqual(sent_form["To"], "whatsapp:+237600000001")
        self.assertEqual(sent_form["Body"], "Transport qualification passed.")

    def test_duplicate_message_has_no_second_effective_event_or_send(self):
        receipt = FakeReceiptStore()
        delivery = FakeDeliveryStore()
        http = RecordingHttp()
        cfg = config()
        runtime = TwilioWhatsAppDemoRuntime(
            config=cfg,
            inbound=TwilioWhatsAppInboundAdapter(cfg),
            outbound=TwilioWhatsAppOutboundAdapter(
                config=cfg,
                http_client=http,
            ),
            receipt_store=receipt,
            delivery_store=delivery,
        )
        values = form()
        signature = twilio_signature(
            auth_token=AUTH_TOKEN,
            url=WEBHOOK_URL,
            form=values,
        )
        first = runtime.handle(
            form=values,
            signature=signature,
            received_at=NOW,
        )
        second = runtime.handle(
            form=values,
            signature=signature,
            received_at=NOW,
        )
        self.assertFalse(first.duplicate)
        self.assertTrue(second.duplicate)
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(len(delivery.created), 1)

    def test_menu_creates_command_but_unbound_transport_cannot_dispatch_commerce(self):
        receipt = FakeReceiptStore()
        delivery = FakeDeliveryStore()
        cfg = config()
        runtime = TwilioWhatsAppDemoRuntime(
            config=cfg,
            inbound=TwilioWhatsAppInboundAdapter(cfg),
            outbound=TwilioWhatsAppOutboundAdapter(
                config=cfg,
                http_client=RecordingHttp(),
            ),
            receipt_store=receipt,
            delivery_store=delivery,
        )
        values = form(body="menu")
        signature = twilio_signature(
            auth_token=AUTH_TOKEN,
            url=WEBHOOK_URL,
            form=values,
        )
        result = runtime.handle(
            form=values,
            signature=signature,
            received_at=NOW,
        )
        self.assertIsNotNone(result.command)
        self.assertEqual(result.command.command_type, ChannelCommandType.SHOW_CATALOG)
        self.assertEqual(result.session_blocker, "trusted_session_absent")
        self.assertIsNone(result.validation)
        self.assertEqual(result.outbound_state, "sent")

    def test_durable_delivery_is_reserved_before_provider_send(self):
        receipt = FakeReceiptStore()
        delivery = FakeDeliveryStore()
        http = RecordingHttp()
        cfg = config()
        runtime = TwilioWhatsAppDemoRuntime(
            config=cfg,
            inbound=TwilioWhatsAppInboundAdapter(cfg),
            outbound=TwilioWhatsAppOutboundAdapter(config=cfg, http_client=http),
            receipt_store=receipt,
            delivery_store=delivery,
        )
        values = form()
        result = runtime.handle(
            form=values,
            signature=twilio_signature(
                auth_token=AUTH_TOKEN,
                url=WEBHOOK_URL,
                form=values,
            ),
            received_at=NOW,
        )
        self.assertEqual(result.outbound_state, "sent")
        self.assertEqual(delivery.created[0]["provider_code"], "twilio_whatsapp")
        self.assertEqual(delivery.outcomes[0]["delivery_state"], "sent")
        self.assertEqual(receipt.finished[0]["processing_state"], "processed")
        self.assertEqual(
            receipt.finished[0]["transport_delivery_ref"],
            UUID("00000000-0000-0000-0000-000000000702"),
        )

    def test_failed_send_is_durably_reconcilable(self):
        receipt = FakeReceiptStore()
        delivery = FakeDeliveryStore()
        cfg = config()
        runtime = TwilioWhatsAppDemoRuntime(
            config=cfg,
            inbound=TwilioWhatsAppInboundAdapter(cfg),
            outbound=TwilioWhatsAppOutboundAdapter(
                config=cfg,
                http_client=RecordingHttp(status_code=400),
            ),
            receipt_store=receipt,
            delivery_store=delivery,
        )
        values = form()
        result = runtime.handle(
            form=values,
            signature=twilio_signature(
                auth_token=AUTH_TOKEN,
                url=WEBHOOK_URL,
                form=values,
            ),
            received_at=NOW,
        )
        self.assertEqual(result.outbound_state, "failed")
        self.assertEqual(delivery.outcomes[0]["delivery_state"], "failed")
        self.assertEqual(
            receipt.finished[0]["processing_state"],
            "failed_retryable",
        )

    def test_twilio_provider_types_do_not_leak_above_transport(self):
        root = Path(__file__).resolve().parents[1] / "src" / "xbos_customer_channel"
        domain_files = [root / "channel_contract.py"]
        domain_files.extend(sorted((root / "application").glob("*.py")))
        combined = "\n".join(
            path.read_text(encoding="utf-8") for path in domain_files
        )
        self.assertNotIn("Twilio", combined)
        self.assertNotIn("twilio_", combined)

    def test_twilio_adapter_has_no_xbos_write_or_payment_dependency(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "xbos_customer_channel"
            / "transports"
            / "twilio_whatsapp.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("xbos_", source.casefold())
        self.assertNotIn("gateway", source.casefold())
        self.assertNotIn("payment", source.casefold())


if __name__ == "__main__":
    unittest.main()

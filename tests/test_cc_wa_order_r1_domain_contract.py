from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xbos_customer_channel.application.command_validation import CommandValidator
from xbos_customer_channel.application.handoff import (
    activate_human,
    request_handoff,
    resume_automation,
)
from xbos_customer_channel.application.outbound_intent import (
    build_w1_outbound_intent,
)
from xbos_customer_channel.application.w1_conversation import (
    W1RenderKind,
    W1RenderedMessage,
)
from xbos_customer_channel.application.w1_whatsapp_runtime import (
    W1RuntimeConversationState,
)
from xbos_customer_channel.channel_contract import (
    ChannelCommandType,
    ChannelInboundEvent,
    ControlOwner,
    build_channel_command,
    command_fingerprint,
)
from xbos_customer_channel.entry_context import EntryPurpose
from xbos_customer_channel.persistence.postgres.channel_command_idempotency import (
    CHANNEL_COMMAND_SCOPE,
    PostgresChannelCommandIdempotencyBinder,
)
from xbos_customer_channel.persistence.postgres.outbound_intent_store import (
    PostgresChannelOutboundIntentStore,
)
from xbos_customer_channel.persistence.postgres.transport_state_stores import (
    IdempotencyConflict,
    IdempotencyResult,
    TransportDelivery,
)
from xbos_customer_channel.session_state import ChannelState, CustomerSessionSnapshot
from xbos_customer_channel.transports.meta_whatsapp import (
    InteractionKind,
    NormalizedInboundMessage,
    NormalizedInteractiveReply,
    NormalizedProviderCallback,
    ProviderOutboundMessage,
    UntrustedProviderUserRef,
    to_channel_inbound_event,
)
from xbos_customer_channel.transports.meta_w1_runtime import W1WhatsAppRuntime


NOW = datetime(2026, 10, 8, 14, 0, tzinfo=timezone.utc)
NOW_EPOCH = int(NOW.timestamp())


def secure_session(
    *,
    state: ChannelState = ChannelState.BROWSING,
    merchant_ref: str = "merchant:wnd",
) -> CustomerSessionSnapshot:
    return CustomerSessionSnapshot(
        session_ref="sess:r1",
        conversation_ref="conv:r1",
        correlation_ref="corr:r1",
        state=state,
        owner_identity_ref="identity:channel:r1",
        tenant_ref="tenant:2",
        merchant_ref=merchant_ref,
        location_ref="location:wnd",
        table_ref=None,
        dining_area_ref=None,
        entry_purpose=EntryPurpose.TAKEAWAY,
        context_binding_ref="ctx:wnd:r1",
        created_at_epoch=NOW_EPOCH - 60,
        expires_at_epoch=NOW_EPOCH + 3600,
        security_binding_complete=True,
    )


def meta_event(*, reply_id: str = "menu") -> ChannelInboundEvent:
    normalized = NormalizedInboundMessage(
        provider_message_ref="wamid.r1.1",
        sender=UntrustedProviderUserRef("raw-provider-locator"),
        occurred_at_epoch=NOW_EPOCH,
        text=None,
        interactive_reply=NormalizedInteractiveReply(
            InteractionKind.BUTTON_REPLY,
            reply_id,
            reply_id,
        ),
        context_provider_message_ref=None,
        metadata_phone_number_id="provider-endpoint-1",
    )
    return to_channel_inbound_event(
        normalized,
        conversation_ref="conv:r1",
        session_ref="sess:r1",
        merchant_ref="merchant:wnd",
        subject_ref="identity:channel:r1",
        correlation_ref="corr:r1",
    )


class FakeIdempotencyStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], IdempotencyResult] = {}

    def reserve(self, **kwargs):
        key = (kwargs["scope"], kwargs["idempotency_key"])
        current = self.rows.get(key)
        if current is not None:
            if current.request_fingerprint != kwargs["request_fingerprint"]:
                raise IdempotencyConflict(
                    "idempotency_key_reused_with_different_fingerprint"
                )
            return current
        row = IdempotencyResult(
            idempotency_result_ref=UUID("00000000-0000-0000-0000-000000000101"),
            scope=kwargs["scope"],
            idempotency_key=kwargs["idempotency_key"],
            request_fingerprint=kwargs["request_fingerprint"],
            status="in_progress",
            result_ref=None,
            safe_result_json=None,
            row_version=1,
        )
        self.rows[key] = row
        return row

    def complete(self, **kwargs):
        for key, current in self.rows.items():
            if current.idempotency_result_ref == kwargs["idempotency_result_ref"]:
                row = IdempotencyResult(
                    idempotency_result_ref=current.idempotency_result_ref,
                    scope=current.scope,
                    idempotency_key=current.idempotency_key,
                    request_fingerprint=current.request_fingerprint,
                    status="completed",
                    result_ref=kwargs["result_ref"],
                    safe_result_json=kwargs["safe_result_json"],
                    row_version=current.row_version + 1,
                )
                self.rows[key] = row
                return row
        raise AssertionError("reservation_not_found")


class FakeDeliveryStore:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []
        self.outcomes: list[dict[str, object]] = []

    def create_pending(self, **kwargs):
        self.created.append(kwargs)
        return TransportDelivery(
            delivery_ref=UUID("00000000-0000-0000-0000-000000000201"),
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
            receipt_ref=UUID("00000000-0000-0000-0000-000000000301"),
            render_ordinal=0,
            provider_code="meta_whatsapp",
            provider_message_ref=kwargs["provider_message_ref"],
            delivery_state=kwargs["delivery_state"],
            row_version=kwargs["expected_row_version"] + 1,
        )


class FakeInboundAdapter:
    def __init__(self, message: NormalizedInboundMessage) -> None:
        self.message = message

    def receive(self, *, raw_body: bytes, signature: str | None):
        del raw_body, signature
        return NormalizedProviderCallback((self.message,), ())


class FakeOutboundAdapter:
    def __init__(self, calls: list[str], *, fail: bool = False) -> None:
        self.calls = calls
        self.fail = fail

    def send_text(self, *, recipient, body: str):
        del recipient, body
        self.calls.append("send")
        if self.fail:
            raise RuntimeError("provider_down")
        return ProviderOutboundMessage("wamid.out.r1")

    def send_buttons(self, **kwargs):
        del kwargs
        raise AssertionError("not_expected")

    def send_list(self, **kwargs):
        del kwargs
        raise AssertionError("not_expected")


class FakeRouter:
    def route(self, *, session, catalog_session, navigation, inbound, checkout_state):
        del session, inbound, checkout_state
        return SimpleNamespace(
            rendered=W1RenderedMessage(W1RenderKind.TEXT, "Safe response."),
            catalog_session=catalog_session,
            navigation=navigation,
            checkout=None,
        )


class FakeDurableRuntimeStatePort:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.state = W1RuntimeConversationState(
            session=secure_session(),
            catalog_session=object(),
        )

    def load(self, inbound):
        del inbound
        self.calls.append("load")
        return self.state

    def reserve_outbound(self, inbound, intent, *, render_ordinal: int = 0):
        del inbound, intent, render_ordinal
        self.calls.append("reserve")
        return TransportDelivery(
            delivery_ref=UUID("00000000-0000-0000-0000-000000000601"),
            receipt_ref=UUID("00000000-0000-0000-0000-000000000602"),
            render_ordinal=0,
            provider_code="meta_whatsapp",
            provider_message_ref=None,
            delivery_state="pending_send",
            row_version=1,
        )

    def save(self, inbound, state):
        del inbound
        self.calls.append("save")
        self.state = state

    def record_outbound_outcome(
        self,
        reservation,
        *,
        delivery_state,
        provider_message_ref,
        provider_error_code,
    ):
        del reservation, provider_message_ref, provider_error_code
        self.calls.append(f"record:{delivery_state}")
        return None


class CCWAOrderR1Tests(unittest.TestCase):
    def test_meta_normalized_message_maps_to_provider_neutral_event(self):
        event = meta_event()
        self.assertEqual(event.channel, "whatsapp")
        self.assertEqual(event.provider_message_id, "wamid.r1.1")
        self.assertEqual(event.conversation_ref, "conv:r1")
        self.assertEqual(event.session_ref, "sess:r1")
        self.assertEqual(event.merchant_ref, "merchant:wnd")
        self.assertEqual(event.subject_ref, "identity:channel:r1")
        self.assertEqual(event.payload["reply_id"], "menu")
        self.assertNotIn("raw", event.safe_provider_metadata)

    def test_provider_types_do_not_leak_above_transport_adapter(self):
        root = Path(__file__).resolve().parents[1] / "src" / "xbos_customer_channel"
        surfaces = [root / "channel_contract.py"]
        surfaces.extend(sorted((root / "application").glob("*.py")))
        combined = "\n".join(path.read_text(encoding="utf-8") for path in surfaces)
        self.assertNotIn("NormalizedInboundMessage", combined)
        self.assertNotIn("MetaWhatsApp", combined)
        self.assertNotIn("InteractionKind", combined)
        self.assertNotIn("meta_whatsapp", combined)

    def test_valid_show_catalog_command_passes(self):
        event = meta_event()
        command = build_channel_command(
            event=event,
            command_type=ChannelCommandType.SHOW_CATALOG,
            payload={},
        )
        result = CommandValidator().validate(
            event=event,
            command=command,
            session=secure_session(),
            now_epoch=NOW_EPOCH,
        )
        self.assertTrue(result.valid)
        self.assertTrue(result.dispatchable)
        self.assertEqual(result.reason, "validated")

    def test_invalid_session_fails_closed(self):
        event = meta_event()
        command = build_channel_command(
            event=event,
            command_type=ChannelCommandType.SHOW_CATALOG,
            payload={},
        )
        session = secure_session()
        session = CustomerSessionSnapshot(
            **{
                field: getattr(session, field)
                for field in session.__dataclass_fields__
                if field != "security_binding_complete"
            },
            security_binding_complete=False,
        )
        result = CommandValidator().validate(
            event=event,
            command=command,
            session=session,
            now_epoch=NOW_EPOCH,
        )
        self.assertFalse(result.valid)
        self.assertFalse(result.dispatchable)
        self.assertEqual(result.reason, "secure_session_binding_required")

    def test_wrong_merchant_fails_closed(self):
        event = meta_event()
        command = build_channel_command(
            event=event,
            command_type=ChannelCommandType.SHOW_CATALOG,
            payload={},
        )
        result = CommandValidator().validate(
            event=event,
            command=command,
            session=secure_session(merchant_ref="merchant:other"),
            now_epoch=NOW_EPOCH,
        )
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "merchant_binding_mismatch")

    def test_invalid_state_fails_closed(self):
        event = meta_event()
        command = build_channel_command(
            event=event,
            command_type=ChannelCommandType.SUBMIT_ORDER,
            payload={"confirmation_ref": "conf:1"},
        )
        result = CommandValidator(real_xbos_write_available=True).validate(
            event=event,
            command=command,
            session=secure_session(state=ChannelState.BROWSING),
            now_epoch=NOW_EPOCH,
        )
        self.assertFalse(result.valid)
        self.assertIn("command_not_allowed_in_state", result.reason)

    def test_human_control_blocks_mutating_automation(self):
        event = meta_event(reply_id="add")
        command = build_channel_command(
            event=event,
            command_type=ChannelCommandType.ADD_LINE,
            payload={"item_ref": "item:1", "quantity": 1},
        )
        result = CommandValidator(real_xbos_write_available=True).validate(
            event=event,
            command=command,
            session=secure_session(state=ChannelState.BROWSING),
            now_epoch=NOW_EPOCH,
            control_owner=ControlOwner.HUMAN,
        )
        self.assertFalse(result.valid)
        self.assertEqual(
            result.reason,
            "human_control_blocks_automated_mutation",
        )

    def test_handoff_requested_active_and_resume_have_bounded_control_owner(self):
        requested = request_handoff("handoff:r1")
        self.assertEqual(requested.control_owner, ControlOwner.HUMAN)
        active = activate_human(requested)
        self.assertEqual(active.control_owner, ControlOwner.HUMAN)
        resumed = resume_automation(active)
        self.assertEqual(resumed.control_owner, ControlOwner.AUTOMATION)

    def test_duplicate_provider_event_maps_to_one_command_identity(self):
        event = meta_event()
        a = build_channel_command(
            event=event,
            command_type=ChannelCommandType.SHOW_CATALOG,
            payload={},
        )
        b = build_channel_command(
            event=event,
            command_type=ChannelCommandType.SHOW_CATALOG,
            payload={},
        )
        self.assertEqual(a.command_id, b.command_id)
        self.assertEqual(a.idempotency_key, b.idempotency_key)
        self.assertEqual(command_fingerprint(a), command_fingerprint(b))

        store = FakeIdempotencyStore()
        binder = PostgresChannelCommandIdempotencyBinder(store)
        first = binder.reserve(
            command=a,
            now_utc=NOW,
            purge_after_utc=NOW + timedelta(days=7),
        )
        second = binder.reserve(
            command=b,
            now_utc=NOW,
            purge_after_utc=NOW + timedelta(days=7),
        )
        self.assertEqual(first.idempotency_result_ref, second.idempotency_result_ref)
        self.assertEqual(first.scope, CHANNEL_COMMAND_SCOPE)

    def test_conflicting_duplicate_event_fails_closed(self):
        event = meta_event()
        a = build_channel_command(
            event=event,
            command_type=ChannelCommandType.SHOW_ITEM,
            payload={"item_ref": "item:a"},
        )
        b = build_channel_command(
            event=event,
            command_type=ChannelCommandType.SHOW_ITEM,
            payload={"item_ref": "item:b"},
        )
        self.assertEqual(a.idempotency_key, b.idempotency_key)
        self.assertNotEqual(command_fingerprint(a), command_fingerprint(b))

        binder = PostgresChannelCommandIdempotencyBinder(FakeIdempotencyStore())
        binder.reserve(
            command=a,
            now_utc=NOW,
            purge_after_utc=NOW + timedelta(days=7),
        )
        with self.assertRaises(IdempotencyConflict):
            binder.reserve(
                command=b,
                now_utc=NOW,
                purge_after_utc=NOW + timedelta(days=7),
            )

    def test_submit_order_without_real_xbos_write_is_non_dispatchable(self):
        event = meta_event(reply_id="order:confirm")
        command = build_channel_command(
            event=event,
            command_type=ChannelCommandType.SUBMIT_ORDER,
            payload={
                "confirmation_ref": "confirmation:r1",
                "client_submit_ref": "submit:r1",
            },
        )
        result = CommandValidator(real_xbos_write_available=False).validate(
            event=event,
            command=command,
            session=secure_session(state=ChannelState.REVIEW),
            now_epoch=NOW_EPOCH,
        )
        self.assertTrue(result.valid)
        self.assertFalse(result.dispatchable)
        self.assertEqual(result.reason, "real_xbos_write_adapter_unavailable")

    def test_outbound_intent_is_durably_representable(self):
        event = meta_event()
        rendered = W1RenderedMessage(W1RenderKind.TEXT, "Menu available.")
        intent = build_w1_outbound_intent(event=event, rendered=rendered)
        fake = FakeDeliveryStore()
        store = PostgresChannelOutboundIntentStore(fake)
        reservation = store.reserve(
            intent=intent,
            receipt_ref=UUID("00000000-0000-0000-0000-000000000401"),
            render_ordinal=0,
            recipient_lookup_hash="lookup-hash",
            provider_code="meta_whatsapp",
            now_utc=NOW,
            purge_after_utc=NOW + timedelta(days=7),
        )
        self.assertEqual(reservation.delivery_state, "pending_send")
        self.assertEqual(len(fake.created), 1)
        self.assertTrue(fake.created[0]["payload_digest"])

    def test_outbound_failure_remains_reconcilable(self):
        event = meta_event()
        intent = build_w1_outbound_intent(
            event=event,
            rendered=W1RenderedMessage(W1RenderKind.TEXT, "Safe response."),
        )
        fake = FakeDeliveryStore()
        store = PostgresChannelOutboundIntentStore(fake)
        reservation = store.reserve(
            intent=intent,
            receipt_ref=UUID("00000000-0000-0000-0000-000000000501"),
            render_ordinal=0,
            recipient_lookup_hash="lookup-hash",
            provider_code="meta_whatsapp",
            now_utc=NOW,
            purge_after_utc=NOW + timedelta(days=7),
        )
        failed = store.record_send_outcome(
            reservation=reservation,
            delivery_state="failed",
            provider_message_ref=None,
            provider_error_code="provider_unavailable",
            now_utc=NOW + timedelta(seconds=1),
        )
        self.assertEqual(failed.delivery_state, "failed")
        self.assertEqual(len(fake.outcomes), 1)


    def test_durable_runtime_reserves_before_state_save_and_provider_send(self):
        calls: list[str] = []
        message = NormalizedInboundMessage(
            provider_message_ref="wamid.r1.runtime",
            sender=UntrustedProviderUserRef("raw-provider-locator"),
            occurred_at_epoch=NOW_EPOCH,
            text="menu",
            interactive_reply=None,
            context_provider_message_ref=None,
            metadata_phone_number_id="provider-endpoint-1",
        )
        runtime = W1WhatsAppRuntime(
            inbound=FakeInboundAdapter(message),
            outbound=FakeOutboundAdapter(calls),
            router=FakeRouter(),
            state_port=FakeDurableRuntimeStatePort(calls),
        )
        sent = runtime.handle_webhook(raw_body=b"{}", signature=None)
        self.assertEqual(len(sent), 1)
        self.assertEqual(
            calls,
            ["load", "reserve", "save", "send", "record:sent"],
        )

    def test_durable_runtime_records_failure_and_emits_no_unreserved_fallback(self):
        calls: list[str] = []
        message = NormalizedInboundMessage(
            provider_message_ref="wamid.r1.runtime.fail",
            sender=UntrustedProviderUserRef("raw-provider-locator"),
            occurred_at_epoch=NOW_EPOCH,
            text="menu",
            interactive_reply=None,
            context_provider_message_ref=None,
            metadata_phone_number_id="provider-endpoint-1",
        )
        runtime = W1WhatsAppRuntime(
            inbound=FakeInboundAdapter(message),
            outbound=FakeOutboundAdapter(calls, fail=True),
            router=FakeRouter(),
            state_port=FakeDurableRuntimeStatePort(calls),
        )
        sent = runtime.handle_webhook(raw_body=b"{}", signature=None)
        self.assertEqual(sent, ())
        self.assertEqual(
            calls,
            ["load", "reserve", "save", "send", "record:failed"],
        )


if __name__ == "__main__":
    unittest.main()

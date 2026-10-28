"""P0D R1 merchant WND private HTTP candidate. MockTransport only; no live calls."""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xbos_customer_channel.adapters.wnd_merchant_http import (
    WNDMerchantAdmissionRequired, WNDMerchantBinding, WNDMerchantPrivateTransport,
)
from xbos_customer_channel.adapters.wnd_r1_order_port import WNDMerchantOrderPort
from xbos_customer_channel.application.w1_composition import (
    XBOSW1ContractUnavailable, compose_wnd_merchant_order_port,
)
from xbos_customer_channel.catalog import (
    AvailabilityState, CommercialQuoteSnapshot, QuoteLineSnapshot,
)
from xbos_customer_channel.order import (
    CanonicalOrderState, OrderSubmissionConflict, OrderSubmitRequest, ServiceMode,
)

def u(n: int) -> str:
    return str(UUID(f"10000000-0000-4000-8000-{n:012d}"))

MERCHANT, LOCATION, CONTEXT, SERVICE, CONTACT, ITEM, CONFIRM, ORDER = map(u, range(1, 9))
HOST = "wnd-private.internal.test"
BASE = "https://" + HOST
TOKEN = "offline-nonsecret-wnd-fixture"
REF = "wnd-channel-service:fixture-reference"
CORRELATION = "p0d-merchant-no-money"
NOW = datetime(2026, 10, 28, 16, 0, tzinfo=timezone.utc)


def bind():
    return WNDMerchantBinding(
        tenant_id=910001, merchant_public_id=MERCHANT,
        location_public_id=LOCATION, location_id=9,
        context_binding_public_id=CONTEXT,
        service_identity_public_id=SERVICE,
        correlation_ref=CORRELATION,
    )


class WNDMockBackend:
    def __init__(self):
        self.calls = []
        self.order_count = 0
        self.requests_by_key = {}
        self.auth_error = False
        self.price = Decimal("1750")
        self.invalid_uuid = False
        self.merchant_override = MERCHANT
        self.status = "submitted"

    def __call__(self, req):
        self.calls.append((req.method, str(req.url), dict(req.headers), req.content))
        if (
            self.auth_error or req.headers.get("authorization") != "Bearer " + TOKEN
            or req.headers.get("x-xbos-context-binding-ref") != CONTEXT
            or req.headers.get("x-correlation-ref") != CORRELATION
            or req.url.host != HOST
        ):
            return httpx.Response(403, json={"error": {"code": "SERVICE_AUTH_FORBIDDEN"}})
        path = req.url.path
        if req.method == "POST" and path.endswith("/order-confirmations"):
            import json
            body = json.loads(req.content)
            if any(k in body for k in (
                "amount", "total", "tenant_id", "merchant_public_id", "currency", "unit_price",
            )):
                return httpx.Response(400, json={"error": {"code": "CALLER_PRICE_DENIED"}})
            if body["cart_lines"][0]["quantity"] != "2":
                return httpx.Response(400, json={"error": {"code": "QUANTITY_DENIED"}})
            return httpx.Response(200, json={
                "confirmation_ref": CONFIRM, "merchant_ref": self.merchant_override,
                "location_ref": 9, "service_context_ref": CONTEXT,
                "service_mode": "takeaway",
                "currency": "XAF",
                "observed_quote_ref": "source-quote:fixture",
                "observed_quote_version": "source-version:1",
                "authoritative_lines": [{
                    "catalog_entry_ref": ITEM, "quantity": "2",
                    "unit_price": str(self.price), "currency": "XAF",
                }],
                "canonical_total": str(self.price * 2),
                "expires_at": (NOW + timedelta(minutes=5)).isoformat(),
            })
        if req.method == "POST" and path.endswith("/orders"):
            import json
            body = json.loads(req.content)
            key = body["client_submit_ref"]
            if key not in self.requests_by_key:
                self.order_count += 1
                self.requests_by_key[key] = {
                    "order_ref": "not-a-uuid" if self.invalid_uuid else ORDER,
                    "order_state": self.status,
                }
            return httpx.Response(200, json=self.requests_by_key[key])
        if req.method == "GET" and "/orders/by-client-submit-ref/" in path:
            from urllib.parse import unquote
            key = unquote(path.rsplit("/", 1)[-1])
            if key in self.requests_by_key:
                return httpx.Response(200, json=self.requests_by_key[key])
            return httpx.Response(404, json={"error": {"code": "UNKNOWN"}})
        return httpx.Response(404, json={"error": {"code": "INVALID_TEST_ROUTE"}})


def quote():
    return CommercialQuoteSnapshot(
        quote_ref="source-quote:fixture", merchant_ref=MERCHANT,
        location_ref=LOCATION, currency="XAF", total=Decimal("3500"),
        version="source-version:1", expires_at_epoch=int(NOW.timestamp()) + 600,
        lines=(QuoteLineSnapshot(
            item_ref=ITEM, quantity=2, unit_price=Decimal("1750"),
            line_total=Decimal("3500"), availability=AvailabilityState.AVAILABLE,
        ),),
    )


class P0DMerchantChannelTests(unittest.TestCase):
    def setUp(self):
        self.no_external = patch("httpx.HTTPTransport.handle_request",
                                 side_effect=AssertionError("external_network_forbidden"))
        self.mock_external = self.no_external.start()
        self.addCleanup(self.no_external.stop)
        self.backend = WNDMockBackend()
        self.client = httpx.Client(transport=httpx.MockTransport(self.backend))
        self.addCleanup(self.client.close)
        self.binding = bind()

    def compose(self, *, enabled=True, merchant=True, service=True,
                url=BASE, host=HOST, token=TOKEN, ref=REF):
        return compose_wnd_merchant_order_port(
            enabled=enabled,
            merchant_identity_admitted=merchant,
            service_identity_admitted=service,
            binding=self.binding,
            base_url=url,
            expected_host=host,
            wnd_service_bearer=token,
            wnd_service_credential_ref=ref,
            http_client=self.client,
        )

    def prepare(self, port):
        mode = port.resolve_service_context(
            merchant_ref=MERCHANT, location_ref=LOCATION,
            mode=ServiceMode.TAKEAWAY, table_ref=None,
            contact_ref=CONTACT, delivery_address_ref=None,
        )
        return port.prepare_order_confirmation(
            quote=quote(), service_context=mode, now_epoch=int(NOW.timestamp())
        )

    def test_disabled_by_default(self):
        with self.assertRaisesRegex(XBOSW1ContractUnavailable, "FEATURE_DISABLED"):
            compose_wnd_merchant_order_port(
                binding=self.binding, base_url=BASE, expected_host=HOST,
                wnd_service_bearer=TOKEN, wnd_service_credential_ref=REF,
                http_client=self.client,
            )
        self.mock_external.assert_not_called()
        self.assertEqual(self.backend.calls, [])

    def test_current_owner_and_service_admission_required(self):
        for merchant, service in ((False, True), (True, False), (False, False)):
            with self.subTest(merchant=merchant, service=service):
                with self.assertRaisesRegex(XBOSW1ContractUnavailable, "NOT_ADMITTED"):
                    self.compose(merchant=merchant, service=service)
        self.assertEqual(self.backend.calls, [])

    def test_distinct_wnd_service_reference_not_gateway_payment_credential(self):
        for ref in ("gateway:mch_test", "mch_abc", "pay_abc", "wnd-other:invalid"):
            with self.subTest(ref=ref):
                with self.assertRaises(XBOSW1ContractUnavailable):
                    self.compose(ref=ref)
        self.assertEqual(self.backend.calls, [])

    def test_https_expected_private_host_and_injected_client_required(self):
        for url in ("http://" + HOST, "https://evil.test", BASE + ":8443",
                    BASE + "/redirect"):
            with self.subTest(url=url):
                with self.assertRaises(XBOSW1ContractUnavailable):
                    self.compose(url=url)
        with self.assertRaises(XBOSW1ContractUnavailable):
            compose_wnd_merchant_order_port(
                enabled=True, merchant_identity_admitted=True,
                service_identity_admitted=True, binding=self.binding,
                base_url=BASE, expected_host=HOST,
                wnd_service_bearer=TOKEN, wnd_service_credential_ref=REF,
            )
        self.assertEqual(self.backend.calls, [])

    def test_real_tenant_metadata_is_not_inferred_from_phone_or_bearer(self):
        self.assertEqual(self.binding.tenant_id, 910001)
        self.assertEqual(self.binding.merchant_public_id, MERCHANT)
        self.assertFalse(hasattr(self.binding, "provider_phone"))
        self.assertFalse(hasattr(self.binding, "gateway_payment_credential"))

    def test_server_price_3500_and_auth_scoped_one_uuid_replay(self):
        port = self.compose()
        confirmation = self.prepare(port)
        self.assertEqual(confirmation.total, Decimal("3500"))
        req = OrderSubmitRequest(
            client_submit_ref="p0d-source-merchant-test",
            correlation_ref=CORRELATION, confirmation_ref=CONFIRM,
        )
        a = port.submit_order(req)
        b = port.submit_order(req)
        self.assertEqual(a, b)
        self.assertEqual(a.order_ref, ORDER)
        self.assertEqual(a.state, CanonicalOrderState.CONFIRMED)
        self.assertEqual(self.backend.order_count, 1)
        self.assertEqual(port.get_order_by_client_ref(req.client_submit_ref), a)
        self.mock_external.assert_not_called()
        self.assertFalse(hasattr(port, "mark_paid"))

    def test_invalid_server_price_fails_closed(self):
        self.backend.price = Decimal("1760")
        port = self.compose()
        with self.assertRaisesRegex(Exception, "price_or_line_changed"):
            self.prepare(port)
        self.assertEqual(self.backend.order_count, 0)

    def test_wrong_merchant_source_confirmation_fails_closed(self):
        self.backend.merchant_override = u(99)
        with self.assertRaisesRegex(Exception, "confirmation_binding_mismatch"):
            self.prepare(self.compose())
        self.assertEqual(self.backend.order_count, 0)

    def test_wrong_actual_bearer_denied_without_order(self):
        port = self.compose(token="wrong-fake-bearer")
        with self.assertRaises(WNDMerchantAdmissionRequired):
            self.prepare(port)
        self.assertEqual(self.backend.order_count, 0)

    def test_wrong_merchant_runtime_context_denied_locally(self):
        port = self.compose()
        with self.assertRaisesRegex(Exception, "merchant_location_mismatch"):
            port.resolve_service_context(
                merchant_ref=u(88), location_ref=LOCATION,
                mode=ServiceMode.TAKEAWAY, table_ref=None,
                contact_ref=CONTACT, delivery_address_ref=None,
            )
        self.assertEqual(self.backend.calls, [])

    def test_server_order_uuid_invalid_denied(self):
        self.backend.invalid_uuid = True
        port = self.compose()
        self.prepare(port)
        with self.assertRaisesRegex(Exception, "canonical_uuid_required"):
            port.submit_order(OrderSubmitRequest(
                client_submit_ref="p0d-bad-order",
                correlation_ref=CORRELATION, confirmation_ref=CONFIRM,
            ))

    def test_percent_encoded_reconciliation_key_uses_same_order(self):
        port = self.compose()
        self.prepare(port)
        key = "p0d:merchant:source"
        created = port.submit_order(OrderSubmitRequest(
            client_submit_ref=key,
            correlation_ref=CORRELATION, confirmation_ref=CONFIRM,
        ))
        recovered = port.get_order_by_client_ref(key)
        self.assertEqual(recovered, created)
        self.assertEqual(self.backend.order_count, 1)

    def test_private_route_traversal_and_payment_endpoint_rejected(self):
        port = self.compose()
        for path in (
            "/internal/customer-channel/v1/../payments",
            "/internal/customer-channel/v1/orders/" + ORDER + "/payment-request",
            "/internal/customer-channel/v1/order-drafts/not-a-uuid/submit",
            "https://other-domain.test/checkout",
        ):
            with self.subTest(path=path):
                with self.assertRaises(WNDMerchantAdmissionRequired):
                    port._call("POST", path, {})
        self.assertEqual(self.backend.calls, [])

    def test_server_pending_is_not_paid_or_canonical_submit(self):
        self.backend.status = "pending"
        port = self.compose()
        self.prepare(port)
        with self.assertRaisesRegex(Exception, "wnd_order_not_submitted"):
            port.submit_order(OrderSubmitRequest(
                client_submit_ref="p0d-pending",
                correlation_ref=CORRELATION, confirmation_ref=CONFIRM,
            ))
        self.assertFalse(hasattr(port, "mark_paid"))

    def test_same_key_new_confirmation_ref_conflicts(self):
        port = self.compose()
        self.prepare(port)
        port.submit_order(OrderSubmitRequest(
            client_submit_ref="p0d-key",
            correlation_ref=CORRELATION, confirmation_ref=CONFIRM,
        ))
        other = u(15)
        port._confirmations[other] = port._confirmations[CONFIRM]
        with self.assertRaisesRegex(OrderSubmissionConflict, "idempotency_payload_conflict"):
            port.submit_order(OrderSubmitRequest(
                client_submit_ref="p0d-key",
                correlation_ref=CORRELATION, confirmation_ref=other,
            ))
        self.assertEqual(self.backend.order_count, 1)


class P0DMerchantWhatsAppHandoffTests(unittest.TestCase):
    """Command-to-domain seam only, never a live provider handler."""

    def setUp(self):
        from unittest.mock import Mock
        from xbos_customer_channel.channel_contract import (
            ChannelInboundEvent, ChannelCommandType, build_channel_command,
            stable_channel_event_id,
        )
        from xbos_customer_channel.session_state import (
            CustomerSessionSnapshot, ChannelState,
        )
        from xbos_customer_channel.application.w1_whatsapp_runtime import (
            W1WNDMerchantOrderDispatch, WNDMerchantCheckoutNotAdmitted,
        )
        self.dispatch_class = W1WNDMerchantOrderDispatch
        self.failure = WNDMerchantCheckoutNotAdmitted
        event_id = stable_channel_event_id(
            channel="whatsapp", recipient_identity="whatsapp:+14155550100",
            provider_message_id="offline-WND-P0D-1",
        )
        self.event = ChannelInboundEvent(
            channel_event_id=event_id, provider_message_id="offline-WND-P0D-1",
            channel="whatsapp", sender_identity="whatsapp:+237600000000",
            recipient_identity="whatsapp:+14155550100",
            conversation_ref="p0d-conversation",
            session_ref="p0d-session",
            merchant_ref=MERCHANT,
            subject_ref="p0d-owner-fixture",
            occurred_at=NOW,
            message_type="text", payload={"text": "order:confirm"},
            correlation_ref=CORRELATION, safe_provider_metadata={},
        )
        self.key = "cc-submit:" + event_id
        self.command = build_channel_command(
            event=self.event, command_type=ChannelCommandType.SUBMIT_ORDER,
            payload={"confirmation_ref": "server-issued-handle", "client_submit_ref": self.key},
        )
        self.session = CustomerSessionSnapshot(
            session_ref="p0d-session", conversation_ref="p0d-conversation",
            correlation_ref=CORRELATION, state=ChannelState.REVIEW,
            owner_identity_ref="p0d-owner-fixture",
            tenant_ref="tenant:fixture:910001", merchant_ref=MERCHANT,
            location_ref=LOCATION, context_binding_ref=CONTEXT,
            security_binding_complete=True,
            expires_at_epoch=int(NOW.timestamp()) + 1200,
        )
        self.order_service = Mock()
        self.order_service.submit.return_value = "server-order-projection-only"

    def bridge(self, *, enabled=True, admitted=True):
        return self.dispatch_class(
            enabled=enabled, merchant_admitted=admitted,
            binding=bind(), expected_tenant_ref="tenant:fixture:910001",
            order_service=self.order_service,
        )

    def call(self, bridge, session=None, command=None, event=None):
        return bridge.submit_confirmed(
            event=event or self.event,
            command=command or self.command,
            session=session or self.session,
            now_epoch=int(NOW.timestamp()),
        )

    def test_whatsapp_dispatch_disabled_by_default(self):
        with self.assertRaisesRegex(self.failure, "feature_disabled"):
            self.call(self.bridge(enabled=False))
        self.order_service.submit.assert_not_called()

    def test_whatsapp_dispatch_requires_merchant_authority(self):
        with self.assertRaisesRegex(self.failure, "not_verified"):
            self.call(self.bridge(admitted=False))
        self.order_service.submit.assert_not_called()

    def test_server_issued_confirmation_is_handed_to_existing_order_service(self):
        result = self.call(self.bridge())
        self.assertEqual(result, "server-order-projection-only")
        self.order_service.submit.assert_called_once_with(
            session_ref="p0d-session",
            confirmation_handle_ref="server-issued-handle",
            client_submit_ref=self.key,
            now_epoch=int(NOW.timestamp()),
        )

    def test_channel_merchant_location_context_mismatch_never_dispatches(self):
        from dataclasses import replace
        for session in (
            replace(self.session, tenant_ref="tenant:2"),
            replace(self.session, merchant_ref=u(77)),
            replace(self.session, location_ref=u(78)),
            replace(self.session, context_binding_ref=u(79)),
            replace(self.session, security_binding_complete=False),
        ):
            with self.subTest(session=session):
                with self.assertRaises(self.failure):
                    self.call(self.bridge(), session=session)
        self.order_service.submit.assert_not_called()

    def test_expired_or_rotated_session_never_dispatches(self):
        from dataclasses import replace
        for session in (
            replace(self.session, expires_at_epoch=int(NOW.timestamp())),
            replace(self.session, rotated_to_session_ref="p0d-next-session"),
        ):
            with self.subTest(session=session):
                with self.assertRaisesRegex(self.failure, "not_dispatchable"):
                    self.call(self.bridge(), session=session)
        self.order_service.submit.assert_not_called()

    def test_changed_client_submit_ref_never_dispatches(self):
        from dataclasses import replace
        changed = replace(self.command, payload={
            "confirmation_ref": "server-issued-handle",
            "client_submit_ref": "user-supplied-arbitrary-order",
        })
        with self.assertRaisesRegex(self.failure, "submit_key_mismatch"):
            self.call(self.bridge(), command=changed)
        self.order_service.submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()

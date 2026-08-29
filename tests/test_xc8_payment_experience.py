from __future__ import annotations

import unittest
from decimal import Decimal

from xbos_customer_channel.adapters.fake_payment_request import FakeXBOSPaymentRequestClient
from xbos_customer_channel.adapters.fake_payment_ux import FakePaymentUXAdapter
from xbos_customer_channel.application.payment_experience_service import PaymentExperienceService
from xbos_customer_channel.payment_experience import (
    FakePaymentOutcome,
    NextActionType,
    PaymentMethodCode,
    PaymentMethodNotPermitted,
    PaymentRequestAuthorityMismatch,
)
from xbos_customer_channel.persistence.session_records import InMemoryCustomerSessionStore
from xbos_customer_channel.session_state import ChannelState, CustomerSessionSnapshot


class XC8PaymentExperienceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryCustomerSessionStore()
        self.xbos = FakeXBOSPaymentRequestClient()
        self.fake_payment = FakePaymentUXAdapter()
        self.service = PaymentExperienceService(
            payment_request_port=self.xbos,
            session_store=self.store,
            fake_payment_ux=self.fake_payment,
        )
        self.store.put(CustomerSessionSnapshot(
            session_ref="session:xc8:1",
            conversation_ref="conversation:xc8:1",
            correlation_ref="correlation:xc8:1",
            state=ChannelState.ORDER_CREATED,
            order_ref="order:xc8:1",
        ))

    def register(self, *, pay_at_counter: bool = False, methods=None) -> None:
        if methods is None:
            methods = (
                PaymentMethodCode.XAFPAY_WALLET,
                PaymentMethodCode.MTN_MOBILE_MONEY,
                PaymentMethodCode.ORANGE_MONEY,
                PaymentMethodCode.CARD,
                PaymentMethodCode.PAY_AT_COUNTER,
            )
        self.xbos.register_order_payment_policy(
            order_ref="order:xc8:1",
            correlation_ref="correlation:xc8:1",
            canonical_amount=Decimal("12500.00"),
            currency="XAF",
            permitted_methods=methods,
            pay_at_counter_permitted=pay_at_counter,
        )

    def test_payment_request_uses_xbos_canonical_order_amount_currency(self) -> None:
        self.register()
        request = self.service.payment_request(session_ref="session:xc8:1")
        self.assertEqual(request.order_ref, "order:xc8:1")
        self.assertEqual(request.canonical_amount, Decimal("12500.00"))
        self.assertEqual(request.currency, "XAF")

    def test_payment_request_requires_canonical_order_reference(self) -> None:
        self.store.put(CustomerSessionSnapshot(
            session_ref="session:no-order",
            conversation_ref="conversation:no-order",
            correlation_ref="correlation:no-order",
            state=ChannelState.REVIEW,
        ))
        with self.assertRaisesRegex(PaymentRequestAuthorityMismatch, "canonical_order_reference_required"):
            self.service.payment_request(session_ref="session:no-order")

    def test_correlation_mismatch_fails_closed(self) -> None:
        self.xbos.register_order_payment_policy(
            order_ref="order:xc8:1",
            correlation_ref="correlation:different",
            canonical_amount=Decimal("12500.00"),
            currency="XAF",
            permitted_methods=(PaymentMethodCode.CARD,),
        )
        with self.assertRaisesRegex(PaymentRequestAuthorityMismatch, "canonical_order_correlation_mismatch"):
            self.service.payment_request(session_ref="session:xc8:1")

    def test_all_authorized_method_codes_can_be_presented(self) -> None:
        self.register(pay_at_counter=True)
        request = self.service.payment_request(session_ref="session:xc8:1")
        self.assertEqual({p.method for p in request.methods}, {
            PaymentMethodCode.XAFPAY_WALLET, PaymentMethodCode.MTN_MOBILE_MONEY,
            PaymentMethodCode.ORANGE_MONEY, PaymentMethodCode.CARD, PaymentMethodCode.PAY_AT_COUNTER,
        })

    def test_pay_at_counter_hidden_when_xbos_policy_forbids(self) -> None:
        self.register(pay_at_counter=False)
        request = self.service.payment_request(session_ref="session:xc8:1")
        self.assertNotIn(PaymentMethodCode.PAY_AT_COUNTER, {p.method for p in request.methods})

    def test_pay_at_counter_shown_only_when_xbos_policy_permits(self) -> None:
        self.register(pay_at_counter=True)
        request = self.service.payment_request(session_ref="session:xc8:1")
        self.assertIn(PaymentMethodCode.PAY_AT_COUNTER, {p.method for p in request.methods})

    def test_unpermitted_method_cannot_be_selected_by_channel(self) -> None:
        self.register(methods=(PaymentMethodCode.CARD,))
        request = self.service.payment_request(session_ref="session:xc8:1")
        with self.assertRaisesRegex(PaymentMethodNotPermitted, "payment_method_not_permitted"):
            self.service.present_method(payment_request=request, method=PaymentMethodCode.XAFPAY_WALLET)

    def test_wallet_next_action_is_presentation_only(self) -> None:
        self.register(methods=(PaymentMethodCode.XAFPAY_WALLET,))
        request = self.service.payment_request(session_ref="session:xc8:1")
        self.assertEqual(self.service.present_method(payment_request=request, method=PaymentMethodCode.XAFPAY_WALLET).action, NextActionType.OPEN_WALLET)

    def test_card_next_action_is_presentation_only(self) -> None:
        self.register(methods=(PaymentMethodCode.CARD,))
        request = self.service.payment_request(session_ref="session:xc8:1")
        self.assertEqual(self.service.present_method(payment_request=request, method=PaymentMethodCode.CARD).action, NextActionType.OPEN_URL)

    def test_mobile_money_next_action_is_await_push_presentation(self) -> None:
        self.register(methods=(PaymentMethodCode.MTN_MOBILE_MONEY, PaymentMethodCode.ORANGE_MONEY))
        request = self.service.payment_request(session_ref="session:xc8:1")
        for method in (PaymentMethodCode.MTN_MOBILE_MONEY, PaymentMethodCode.ORANGE_MONEY):
            self.assertEqual(self.service.present_method(payment_request=request, method=method).action, NextActionType.AWAIT_PUSH)

    def test_fake_pending_is_test_fixture_only(self) -> None:
        self.register(methods=(PaymentMethodCode.CARD,))
        request = self.service.payment_request(session_ref="session:xc8:1")
        result = self.service.simulate_fake_payment(payment_request=request, fixture_ref="fixture:pending", outcome=FakePaymentOutcome.PENDING)
        self.assertTrue(result.test_fixture_only)
        self.assertEqual(result.outcome, FakePaymentOutcome.PENDING)
        self.assertEqual(result.next_action.action, NextActionType.WAIT)

    def test_fake_succeeded_is_not_financial_truth_or_xbos_mutation(self) -> None:
        self.register(methods=(PaymentMethodCode.CARD,))
        request = self.service.payment_request(session_ref="session:xc8:1")
        result = self.service.simulate_fake_payment(payment_request=request, fixture_ref="fixture:success", outcome=FakePaymentOutcome.SUCCEEDED)
        self.assertTrue(result.test_fixture_only)
        self.assertFalse(result.financial_system_mutation)
        self.assertFalse(result.xbos_canonical_payment_mutation)

    def test_fake_succeeded_does_not_mutate_session_to_paid(self) -> None:
        self.register(methods=(PaymentMethodCode.CARD,))
        request = self.service.payment_request(session_ref="session:xc8:1")
        before = self.store.get("session:xc8:1")
        self.service.simulate_fake_payment(payment_request=request, fixture_ref="fixture:success", outcome=FakePaymentOutcome.SUCCEEDED)
        self.assertEqual(self.store.get("session:xc8:1"), before)
        self.assertEqual(self.store.get("session:xc8:1").state, ChannelState.ORDER_CREATED)

    def test_all_fake_outcomes_are_deterministic_non_financial_fixtures(self) -> None:
        self.register(methods=(PaymentMethodCode.CARD,))
        request = self.service.payment_request(session_ref="session:xc8:1")
        for outcome in FakePaymentOutcome:
            first = self.service.simulate_fake_payment(payment_request=request, fixture_ref=f"fixture:{outcome.value}", outcome=outcome)
            second = self.service.simulate_fake_payment(payment_request=request, fixture_ref=f"fixture:{outcome.value}", outcome=outcome)
            self.assertEqual(first, second)
            self.assertFalse(first.financial_system_mutation)
            self.assertFalse(first.xbos_canonical_payment_mutation)

    def test_redirect_or_navigation_action_does_not_imply_success(self) -> None:
        self.register(methods=(PaymentMethodCode.CARD,))
        request = self.service.payment_request(session_ref="session:xc8:1")
        action = self.service.present_method(payment_request=request, method=PaymentMethodCode.CARD)
        self.assertEqual(action.action, NextActionType.OPEN_URL)
        self.assertEqual(self.store.get("session:xc8:1").state, ChannelState.ORDER_CREATED)

    def test_payment_service_exposes_no_provider_routing_operation(self) -> None:
        public = {name.lower() for name in dir(self.service) if not name.startswith("_")}
        self.assertTrue(public.isdisjoint({"route_provider", "select_provider", "create_provider_session", "handle_webhook"}))

    def test_payment_service_exposes_no_ledger_or_canonical_amount_calculation(self) -> None:
        public = {name.lower() for name in dir(self.service) if not name.startswith("_")}
        self.assertTrue(public.isdisjoint({"ledger", "post_payment", "calculate_amount", "calculate_total", "set_amount", "mark_paid"}))

    def test_fake_adapter_has_no_external_provider_or_financial_client(self) -> None:
        attrs = {name.lower() for name in vars(self.fake_payment)}
        self.assertTrue(attrs.isdisjoint({"wallet", "gateway", "core", "provider", "http", "client"}))


if __name__ == "__main__":
    unittest.main()

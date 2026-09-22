from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xbos_customer_channel.adapters.fake_catalog import FakeXBOSCatalogClient
from xbos_customer_channel.adapters.fake_context import FakeXBOSContextClient
from xbos_customer_channel.application.catalog_service import CatalogQuoteService
from xbos_customer_channel.application.w1_checkout_ux import (
    W1CheckoutDomainError,
    W1CheckoutFailureCode,
    W1CheckoutStage,
    W1CheckoutUX,
    W1CustomerSafePaymentOptions,
)
from xbos_customer_channel.entry_context import EntryPurpose, ResolvedEntryContext
from xbos_customer_channel.order import (
    AuthoritativeOrderConfirmationSnapshot,
    CanonicalOrderProjection,
    CanonicalOrderState,
    ServiceMode,
)
from xbos_customer_channel.catalog import QuoteLineSnapshot, AvailabilityState
from xbos_customer_channel.payment_experience import PaymentMethodCode
from xbos_customer_channel.session_state import ChannelState, CustomerSessionSnapshot


def browse_with_cart():
    context = FakeXBOSContextClient().resolve_context(
        merchant_ref="merchant:fixture:alpha",
        location_ref="location:fixture:one",
        table_ref="table:fixture:a1",
        purpose=EntryPurpose.DINE_IN,
    )
    entry = ResolvedEntryContext(
        "entry:xc9:u1",
        "merchant:fixture:alpha",
        "location:fixture:one",
        "table:fixture:a1",
        "area:fixture:main",
        EntryPurpose.DINE_IN,
        context,
    )
    catalog = CatalogQuoteService(FakeXBOSCatalogClient())
    session = catalog.browse(entry)
    return catalog.add_to_cart(session, item_ref="item:fixture:one", quantity=1)


def secure_session() -> CustomerSessionSnapshot:
    return CustomerSessionSnapshot(
        "sess:xc9:u1",
        "conversation:xc9:u1",
        "corr:xc9:u1",
        ChannelState.BROWSING,
        owner_identity_ref="identity:fixture:customer",
        merchant_ref="merchant:fixture:alpha",
        location_ref="location:fixture:one",
        table_ref="table:fixture:a1",
        dining_area_ref="area:fixture:main",
        entry_purpose=EntryPurpose.DINE_IN,
        context_binding_ref="ctxbind:fixture:u1",
        security_binding_complete=True,
    )


class RecordingBoundary:
    def __init__(self) -> None:
        self.prepare_error: W1CheckoutFailureCode | None = None
        self.submit_error: W1CheckoutFailureCode | None = None
        self.payment_error: W1CheckoutFailureCode | None = None
        self.prepare_calls = 0
        self.submit_calls = 0
        self.payment_calls = 0
        self.last_total = Decimal("3000")

    def prepare_order_review(self, *, session, catalog_session, service_mode):
        del session, catalog_session
        self.prepare_calls += 1
        if self.prepare_error is not None:
            raise W1CheckoutDomainError(self.prepare_error)
        delivery_fee = Decimal("750") if service_mode is ServiceMode.DELIVERY else None
        total = Decimal("3750") if service_mode is ServiceMode.DELIVERY else Decimal("3000")
        self.last_total = total
        return AuthoritativeOrderConfirmationSnapshot(
            confirmation_ref=f"confirmation:u1:{service_mode.value}",
            quote_ref="quote:u1",
            quote_version="v1",
            merchant_ref="merchant:fixture:alpha",
            location_ref="location:fixture:one",
            service_context_ref=f"service:u1:{service_mode.value}",
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
            delivery_fee=delivery_fee,
            taxes_charges=(),
            total=total,
            currency="XAF",
            expires_at_epoch=5000,
        )

    def submit_order(self, *, session, confirmation):
        del session
        self.submit_calls += 1
        if self.submit_error is not None:
            raise W1CheckoutDomainError(self.submit_error)
        return CanonicalOrderProjection(
            order_ref="order:u1:1001",
            client_submit_ref="client-submit:u1:1001",
            correlation_ref="corr:xc9:u1",
            confirmation_ref=confirmation.confirmation_ref,
            state=CanonicalOrderState.CONFIRMED,
            evidence_ref="evidence:u1:order",
        )

    def payment_options(self, *, session, order):
        del session
        self.payment_calls += 1
        if self.payment_error is not None:
            raise W1CheckoutDomainError(self.payment_error)
        return W1CustomerSafePaymentOptions(
            payment_request_ref="payment-request:u1:1001",
            order_ref=order.order_ref,
            canonical_amount=self.last_total,
            currency="XAF",
            permitted_methods=(
                PaymentMethodCode.PAY_AT_COUNTER,
                PaymentMethodCode.MTN_MOBILE_MONEY,
                PaymentMethodCode.ORANGE_MONEY,
            ),
        )


class XC9WhatsAppCheckoutUXTests(unittest.TestCase):
    def setUp(self) -> None:
        self.boundary = RecordingBoundary()
        self.ux = W1CheckoutUX(self.boundary)
        self.catalog = browse_with_cart()
        self.session = secure_session()

    def review(self, mode: ServiceMode = ServiceMode.DINE_IN):
        return self.ux.select_service_mode(
            session=self.session,
            catalog_session=self.catalog,
            service_mode=mode,
        )

    def payment_options(self):
        review = self.review()
        return self.ux.confirm_order(session=self.session, state=review.state)

    def test_cart_review_and_service_mode_are_whatsapp_native(self) -> None:
        cart = self.ux.cart_review(self.catalog)
        self.assertEqual(cart.state.stage, W1CheckoutStage.CART_REVIEW)
        self.assertIn("1 Ã— Fixture Daily Item", cart.view.body)
        self.assertEqual([button.reply_id for button in cart.view.buttons], ["checkout", "menu"])

        modes = self.ux.service_mode(self.catalog)
        self.assertEqual(
            [button.reply_id for button in modes.view.buttons],
            ["service:dine_in", "service:takeaway", "service:delivery"],
        )

    def test_dine_in_uses_server_bound_table_context(self) -> None:
        review = self.review(ServiceMode.DINE_IN)
        self.assertEqual(review.state.stage, W1CheckoutStage.ORDER_REVIEW)
        self.assertIn("Total: 3000 XAF", review.view.body)
        no_table = CustomerSessionSnapshot(
            "sess", "conv", "corr", ChannelState.BROWSING,
            owner_identity_ref="identity",
            security_binding_complete=True,
        )
        failed = self.ux.select_service_mode(
            session=no_table,
            catalog_session=self.catalog,
            service_mode=ServiceMode.DINE_IN,
        )
        self.assertEqual(failed.state.stage, W1CheckoutStage.ERROR)
        self.assertEqual(self.boundary.prepare_calls, 1)

    def test_takeaway_and_delivery_present_authoritative_snapshots(self) -> None:
        takeaway = self.review(ServiceMode.TAKEAWAY)
        self.assertIn("Service: Takeaway", takeaway.view.body)
        self.assertIn("Total: 3000 XAF", takeaway.view.body)

        delivery = self.review(ServiceMode.DELIVERY)
        self.assertIn("Service: Delivery", delivery.view.body)
        self.assertIn("Delivery fee: 750 XAF", delivery.view.body)
        self.assertIn("Total: 3750 XAF", delivery.view.body)

    def test_order_confirmation_then_only_current_c3_methods(self) -> None:
        options = self.payment_options()
        self.assertEqual(options.state.stage, W1CheckoutStage.PAYMENT_OPTIONS)
        self.assertIn("Order submitted: order:u1:1001", options.view.body)
        self.assertIn("Amount to pay: 3000 XAF", options.view.body)
        ids = [row.reply_id for row in options.view.list_rows]
        self.assertEqual(
            ids,
            [
                "pay:pay_at_counter",
                "pay:mtn_mobile_money",
                "pay:orange_money",
            ],
        )
        self.assertNotIn("xafpay_wallet", options.view.body)
        self.assertNotIn("card", options.view.body.casefold())

    def test_pay_at_counter_does_not_mark_paid(self) -> None:
        options = self.payment_options()
        selected = self.ux.select_payment_method(
            state=options.state,
            method=PaymentMethodCode.PAY_AT_COUNTER,
        )
        self.assertEqual(selected.state.stage, W1CheckoutStage.METHOD_SELECTED)
        self.assertIn("Pay at counter selected", selected.view.body)
        self.assertIn("does not mean", selected.view.body)
        self.assertNotIn("Payment confirmed", selected.view.body)

    def test_mtn_and_orange_stop_before_execution(self) -> None:
        for method, label in (
            (PaymentMethodCode.MTN_MOBILE_MONEY, "MTN Mobile Money"),
            (PaymentMethodCode.ORANGE_MONEY, "Orange Money"),
        ):
            with self.subTest(method=method):
                options = self.payment_options()
                selected = self.ux.select_payment_method(
                    state=options.state,
                    method=method,
                )
                self.assertIn(f"{label} selected", selected.view.body)
                self.assertIn("has not started yet", selected.view.body)
                self.assertNotIn("push", selected.view.body.casefold())
                self.assertNotIn("pending", selected.view.body.casefold())
                self.assertNotIn("succeeded", selected.view.body.casefold())

    def test_wallet_and_card_are_not_accepted_by_c3_safe_projection(self) -> None:
        for method in (PaymentMethodCode.XAFPAY_WALLET, PaymentMethodCode.CARD):
            with self.subTest(method=method):
                with self.assertRaisesRegex(ValueError, "unsupported_payment_method_projection"):
                    W1CustomerSafePaymentOptions(
                        payment_request_ref="payment-request:bad",
                        order_ref="order:bad",
                        canonical_amount=Decimal("3000"),
                        currency="XAF",
                        permitted_methods=(method,),
                    )

    def test_unsupported_method_fails_closed(self) -> None:
        options = self.payment_options()
        selected = self.ux.select_payment_method(
            state=options.state,
            method=PaymentMethodCode.CARD,
        )
        self.assertEqual(selected.state.stage, W1CheckoutStage.ERROR)
        self.assertIn("not available", selected.view.body)
        self.assertEqual(self.boundary.payment_calls, 1)

    def test_unknown_order_outcome_is_customer_safe(self) -> None:
        self.boundary.submit_error = W1CheckoutFailureCode.UNKNOWN_ORDER_RESULT
        result = self.ux.confirm_order(session=self.session, state=self.review().state)
        self.assertEqual(result.state.stage, W1CheckoutStage.ERROR)
        self.assertIn("Do not submit a second order", result.view.body)
        self.assertNotIn("exception", result.view.body.casefold())

    def test_payment_options_unavailable_is_customer_safe(self) -> None:
        self.boundary.payment_error = W1CheckoutFailureCode.PAYMENT_OPTIONS_UNAVAILABLE
        result = self.ux.confirm_order(session=self.session, state=self.review().state)
        self.assertEqual(result.state.stage, W1CheckoutStage.ERROR)
        self.assertIn("You have not been charged", result.view.body)

    def test_unknown_payment_request_result_does_not_infer_success(self) -> None:
        self.boundary.payment_error = W1CheckoutFailureCode.UNKNOWN_PAYMENT_REQUEST_RESULT
        result = self.ux.confirm_order(session=self.session, state=self.review().state)
        self.assertIn("Do not start a second payment request", result.view.body)
        self.assertNotIn("confirmed", result.view.body.casefold())

    def test_open_means_options_available_not_payment_started(self) -> None:
        options = self.payment_options()
        projection = options.state.payment_options
        assert projection is not None
        self.assertEqual(projection.payment_request_state, "open")
        self.assertEqual(projection.safe_next_action, "present_permitted_methods_only")
        self.assertNotIn("pending", options.view.body.casefold())
        self.assertNotIn("paid", options.view.body.casefold())


if __name__ == "__main__":
    unittest.main()

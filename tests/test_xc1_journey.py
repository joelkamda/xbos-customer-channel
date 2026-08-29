import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xbos_customer_channel.adapters.fake_payment import FakePaymentService
from xbos_customer_channel.adapters.fake_transport import FakeWhatsAppTransport
from xbos_customer_channel.adapters.fake_xbos import FakeXBOSCommerceClient
from xbos_customer_channel.application.journey import CustomerJourneyService
from xbos_customer_channel.models import PaymentState


class XC1JourneyTests(unittest.TestCase):
    def test_successful_fake_journey_uses_authority_ports(self) -> None:
        commerce = FakeXBOSCommerceClient()
        payments = FakePaymentService(PaymentState.SUCCEEDED)
        transport = FakeWhatsAppTransport()
        service = CustomerJourneyService(commerce, payments, transport)

        result = service.run_confirm_and_pay(
            customer_ref="customer:fixture:1",
            entry_ref="entry:fixture:alpha",
            item_refs=["item:fixture:one"],
            correlation_ref="corr:1",
        )

        self.assertEqual(result.order.state, "confirmed_paid")
        self.assertEqual(result.payment_state, PaymentState.SUCCEEDED)
        self.assertEqual(result.receipt.amount, 3000)
        self.assertEqual(len(transport.sent), 1)

    def test_pending_does_not_become_success(self) -> None:
        commerce = FakeXBOSCommerceClient()
        payments = FakePaymentService(PaymentState.PENDING)
        transport = FakeWhatsAppTransport()
        service = CustomerJourneyService(commerce, payments, transport)

        with self.assertRaisesRegex(RuntimeError, "payment_not_succeeded"):
            service.run_confirm_and_pay(
                customer_ref="customer:fixture:1",
                entry_ref="entry:fixture:alpha",
                item_refs=["item:fixture:one"],
                correlation_ref="corr:2",
            )

        self.assertEqual(len(transport.sent), 1)
        pending_message = next(iter(transport.sent.values())).body.lower()
        self.assertIn("pending", pending_message)
        self.assertNotIn("payment confirmed", pending_message)

    def test_idempotent_fake_requests_do_not_multiply_effects(self) -> None:
        commerce = FakeXBOSCommerceClient()
        quote = commerce.resolve_quote(commerce.context, ["item:fixture:one"])
        first = commerce.open_order(quote, "idem:order:1")
        second = commerce.open_order(quote, "idem:order:1")
        self.assertEqual(first, second)

        payments = FakePaymentService()
        p1 = payments.create_payment_request(quote, "idem:payment:1")
        p2 = payments.create_payment_request(quote, "idem:payment:1")
        self.assertEqual(p1, p2)


if __name__ == "__main__":
    unittest.main()

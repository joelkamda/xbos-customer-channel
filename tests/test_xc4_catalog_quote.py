import sys
import unittest
from dataclasses import fields
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xbos_customer_channel.adapters.fake_catalog import FakeXBOSCatalogClient
from xbos_customer_channel.adapters.fake_context import FakeXBOSContextClient
from xbos_customer_channel.application.catalog_service import (
    CatalogQuoteService,
    QuoteUnavailable,
    ReconfirmationRequired,
)
from xbos_customer_channel.entry_context import EntryPurpose, ResolvedEntryContext
from xbos_customer_channel.catalog import AvailabilityState, InteractionCart


def entry_context() -> ResolvedEntryContext:
    projection = FakeXBOSContextClient().resolve_context(
        merchant_ref="merchant:fixture:alpha",
        location_ref="location:fixture:one",
        table_ref="table:fixture:a1",
        purpose=EntryPurpose.DINE_IN,
    )
    return ResolvedEntryContext(
        token_ref="ent_fixture_catalog",
        merchant_ref="merchant:fixture:alpha",
        location_ref="location:fixture:one",
        table_ref="table:fixture:a1",
        dining_area_ref="area:fixture:main",
        purpose=EntryPurpose.DINE_IN,
        projection=projection,
    )


class XC4CatalogQuoteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeXBOSCatalogClient()
        self.service = CatalogQuoteService(self.fake)
        self.entry = entry_context()

    def test_menu_projection_has_required_customer_fields(self) -> None:
        session = self.service.browse(self.entry)
        self.assertEqual(session.projection.currency, "XAF")
        self.assertEqual(len(session.projection.sections), 2)
        item = session.projection.item("item:fixture:one")
        self.assertEqual(item.currency, "XAF")
        self.assertTrue(item.description)
        self.assertTrue(item.media_refs)
        self.assertTrue(item.modifier_group_refs)
        self.assertEqual(item.availability, AvailabilityState.AVAILABLE)
        self.assertEqual(item.display_price, Decimal("3000"))
        self.assertTrue(session.projection.terminology)

    def test_daily_selection_is_catalog_projection_not_channel_menu_store(self) -> None:
        session = self.service.browse(self.entry)
        daily = next(s for s in session.projection.sections if s.section_ref == "section:fixture:daily")
        self.assertEqual(daily.name, "Daily Selection")
        persistence_root = Path(__file__).resolve().parents[1] / "src" / "xbos_customer_channel" / "persistence"
        self.assertFalse(any("catalog" in p.name.lower() or "menu" in p.name.lower() for p in persistence_root.glob("*.py")))

    def test_cart_contains_no_commercial_amount_or_availability_fields(self) -> None:
        names = {f.name for f in fields(InteractionCart)}
        self.assertEqual(names, {"lines"})
        session = self.service.browse(self.entry)
        session = self.service.add_to_cart(session, item_ref="item:fixture:one", quantity=2)
        line_names = {f.name for f in fields(session.cart.lines[0])}
        self.assertEqual(line_names, {"item_ref", "quantity", "option_refs"})
        self.assertFalse({"price", "amount", "total", "currency", "availability", "tax", "fee"} & line_names)

    def test_authoritative_quote_supplies_total(self) -> None:
        session = self.service.add_to_cart(self.service.browse(self.entry), item_ref="item:fixture:one", quantity=2)
        review = self.service.review_for_confirmation(session, now_epoch=1000)
        self.assertEqual(review.quote.total, Decimal("6000"))
        self.assertFalse(review.requires_reconfirmation)
        self.assertTrue(review.can_continue)

    def test_price_change_is_shown_and_requires_explicit_latest_quote_ack(self) -> None:
        session = self.service.add_to_cart(self.service.browse(self.entry), item_ref="item:fixture:one")
        displayed = session.projection.item("item:fixture:one").display_price
        self.fake.set_price("item:fixture:one", Decimal("3500"))
        review = self.service.review_for_confirmation(session, now_epoch=1000)
        self.assertEqual(displayed, Decimal("3000"))
        self.assertEqual(review.quote.total, Decimal("3500"))
        self.assertTrue(review.requires_reconfirmation)
        self.assertEqual(review.changes[0].displayed_value, "3000")
        self.assertEqual(review.changes[0].quoted_value, "3500")
        with self.assertRaisesRegex(ReconfirmationRequired, "latest_quote"):
            self.service.accept_review(review, acknowledged_quote_ref="quote:stale")
        accepted = self.service.accept_review(review, acknowledged_quote_ref=review.quote.quote_ref)
        self.assertEqual(accepted.total, Decimal("3500"))

    def test_availability_change_blocks_continuation(self) -> None:
        session = self.service.add_to_cart(self.service.browse(self.entry), item_ref="item:fixture:one")
        self.fake.set_availability("item:fixture:one", AvailabilityState.UNAVAILABLE)
        review = self.service.review_for_confirmation(session, now_epoch=1000)
        self.assertTrue(review.requires_reconfirmation)
        self.assertFalse(review.can_continue)
        with self.assertRaisesRegex(QuoteUnavailable, "not_available"):
            self.service.accept_review(review, acknowledged_quote_ref=review.quote.quote_ref)

    def test_quote_context_cannot_cross_tenant(self) -> None:
        bad_entry = ResolvedEntryContext(
            token_ref="ent_fixture_other",
            merchant_ref="merchant:fixture:beta",
            location_ref="location:fixture:two",
            table_ref="table:fixture:b1",
            dining_area_ref=None,
            purpose=EntryPurpose.DINE_IN,
            projection=FakeXBOSContextClient().resolve_context(
                merchant_ref="merchant:fixture:beta",
                location_ref="location:fixture:two",
                table_ref="table:fixture:b1",
                purpose=EntryPurpose.DINE_IN,
            ),
        )
        with self.assertRaisesRegex(KeyError, "catalog_context_not_found"):
            self.service.browse(bad_entry)

    def test_xc4_service_has_no_order_or_payment_creation_method(self) -> None:
        public = {name for name in dir(self.service) if not name.startswith("_")}
        self.assertNotIn("open_order", public)
        self.assertNotIn("confirm_order", public)
        self.assertNotIn("create_payment_request", public)
        self.assertNotIn("pay", public)

    def test_empty_cart_quote_is_zero_from_fixture_not_cart_calculation(self) -> None:
        session = self.service.browse(self.entry)
        review = self.service.review_for_confirmation(session, now_epoch=1000)
        self.assertEqual(review.quote.total, Decimal("0"))
        self.assertEqual(review.quote.lines, ())

    def test_option_refs_survive_to_authoritative_quote_request(self) -> None:
        session = self.service.add_to_cart(
            self.service.browse(self.entry),
            item_ref="item:fixture:one",
            option_refs=("option:fixture:side-a",),
        )
        review = self.service.review_for_confirmation(session, now_epoch=1000)
        self.assertEqual(review.quote.lines[0].option_refs, ("option:fixture:side-a",))


if __name__ == "__main__":
    unittest.main()

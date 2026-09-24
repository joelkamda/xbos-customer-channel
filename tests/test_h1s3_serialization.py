from __future__ import annotations

import json
import sys
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xbos_customer_channel.application.catalog_service import CatalogSession
from xbos_customer_channel.application.w1_checkout_ux import (
    W1CheckoutStage,
    W1CheckoutState,
    W1CustomerSafePaymentOptions,
)
from xbos_customer_channel.application.w1_conversation import W1NavigationCursor
from xbos_customer_channel.application.w1_whatsapp_runtime import W1RuntimeConversationState
from xbos_customer_channel.catalog import CatalogProjection, CartLine, InteractionCart
from xbos_customer_channel.entry_context import (
    EntryPurpose,
    MerchantContextProjection,
    ResolvedEntryContext,
)
from xbos_customer_channel.payment_experience import PaymentMethodCode
from xbos_customer_channel.session_state import ChannelState, CustomerSessionSnapshot
from xbos_customer_channel.persistence.postgres.serialization import (
    StateSerializationError,
    deserialize_runtime_state,
    serialize_runtime_state,
)


class H1S3SerializationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.entry_projection = MerchantContextProjection(
            merchant_ref="merchant-1",
            location_ref="location-1",
            service_available=True,
            display_name="Merchant",
            terminology=(),
            currency="XAF",
            allowed_fulfillment_modes=("takeaway",),
        )
        self.catalog_projection = CatalogProjection(
            catalog_ref="catalog-1",
            merchant_ref="merchant-1",
            location_ref="location-1",
            version="v1",
            currency="XAF",
            terminology=(),
            sections=(),
            items=(),
        )
        self.session = CustomerSessionSnapshot(
            session_ref="session-2",
            conversation_ref="conversation-1",
            correlation_ref="correlation-1",
            state=ChannelState.PAYMENT_METHOD,
            entry_token_ref="token-1",
            owner_identity_ref="identity-1",
            tenant_ref="tenant-1",
            merchant_ref="merchant-1",
            location_ref="location-1",
            entry_purpose=EntryPurpose.TAKEAWAY,
            context_binding_ref="context-1",
            created_at_epoch=100,
            expires_at_epoch=86500,
            generation=2,
            predecessor_session_ref="session-1",
            rotated_to_session_ref=None,
            invalidated_at_epoch=None,
            security_binding_complete=True,
            cart_ref="cart-1",
            order_ref="order-1",
            payment_ref="payment-request-1",
            transition_count=7,
        )
        entry = ResolvedEntryContext(
            token_ref="token-1",
            tenant_ref="tenant-1",
            merchant_ref="merchant-1",
            location_ref="location-1",
            table_ref=None,
            dining_area_ref=None,
            purpose=EntryPurpose.TAKEAWAY,
            context_binding_ref="context-1",
            projection=self.entry_projection,
        )
        catalog = CatalogSession(
            entry=entry,
            projection=self.catalog_projection,
            cart=InteractionCart((CartLine("item-1", 2, ("option-1",)),)),
        )
        payment = W1CustomerSafePaymentOptions(
            payment_request_ref="payment-request-1",
            order_ref="order-1",
            canonical_amount=Decimal("2500.00000000"),
            currency="XAF",
            permitted_methods=(
                PaymentMethodCode.MTN_MOBILE_MONEY,
                PaymentMethodCode.ORANGE_MONEY,
            ),
        )
        checkout = W1CheckoutState(
            stage=W1CheckoutStage.PAYMENT_OPTIONS,
            service_mode=None,
            payment_options=payment,
        )
        self.runtime = W1RuntimeConversationState(
            session=self.session,
            catalog_session=catalog,
            navigation=W1NavigationCursor("section-1", "item-1", 2),
            checkout=checkout,
        )

    def test_state_schema_version_one_round_trips_exact_interaction_state(self) -> None:
        document = serialize_runtime_state(self.runtime)
        encoded = json.dumps(document, sort_keys=True)
        self.assertNotIn("security_binding_complete", encoded)
        self.assertNotIn("display_name", encoded)
        restored = deserialize_runtime_state(
            document,
            entry_projection=self.entry_projection,
            catalog_projection=self.catalog_projection,
            security_binding_complete=True,
        )
        self.assertEqual(restored.session, self.session)
        self.assertEqual(restored.navigation, self.runtime.navigation)
        self.assertEqual(
            restored.catalog_session.cart,
            self.runtime.catalog_session.cart,
        )
        self.assertEqual(
            restored.checkout.payment_options.canonical_amount,
            Decimal("2500.00000000"),
        )
        self.assertEqual(
            str(document["checkout"]["payment_options"]["canonical_amount"]),
            "2500.00000000",
        )

    def test_unknown_schema_version_fails_closed(self) -> None:
        document = serialize_runtime_state(self.runtime)
        document["state_schema_version"] = 2
        with self.assertRaisesRegex(
            StateSerializationError,
            "unsupported_runtime_state_schema_version",
        ):
            deserialize_runtime_state(
                document,
                entry_projection=self.entry_projection,
                catalog_projection=self.catalog_projection,
                security_binding_complete=True,
            )

    def test_unknown_document_field_fails_closed(self) -> None:
        document = serialize_runtime_state(self.runtime)
        document["unexpected"] = "value"
        with self.assertRaises(StateSerializationError):
            deserialize_runtime_state(
                document,
                entry_projection=self.entry_projection,
                catalog_projection=self.catalog_projection,
                security_binding_complete=True,
            )
    def test_authoritative_catalog_order_objects_are_not_serialized(self) -> None:
        document = serialize_runtime_state(self.runtime)
        serialized = json.dumps(document, sort_keys=True)
        self.assertNotIn("catalog_ref", serialized)
        self.assertNotIn("display_price", serialized)
        self.assertNotIn("authoritative_snapshot", serialized)
        self.assertIn('"order_ref": "order-1"', serialized)
        self.assertIn('"payment_request_ref": "payment-request-1"', serialized)

    def test_raw_provider_sender_is_not_part_of_serialized_state(self) -> None:
        document = serialize_runtime_state(self.runtime)
        serialized = json.dumps(document, sort_keys=True)
        self.assertNotIn("raw-provider-sender", serialized)
        self.assertNotIn("sender", serialized.casefold())

    def test_rotation_metadata_round_trips(self) -> None:
        rotated = replace(
            self.runtime,
            session=replace(
                self.session,
                rotated_to_session_ref="session-3",
                invalidated_at_epoch=800,
            ),
        )
        document = serialize_runtime_state(rotated)
        restored = deserialize_runtime_state(
            document,
            entry_projection=self.entry_projection,
            catalog_projection=self.catalog_projection,
            security_binding_complete=True,
        )
        self.assertEqual(restored.session.predecessor_session_ref, "session-1")
        self.assertEqual(restored.session.rotated_to_session_ref, "session-3")
        self.assertEqual(restored.session.invalidated_at_epoch, 800)

    def test_pickle_is_not_used_by_serializer_source(self) -> None:
        source = (
            ROOT
            / "src"
            / "xbos_customer_channel"
            / "persistence"
            / "postgres"
            / "serialization.py"
        ).read_text(encoding="utf-8").lower()
        self.assertNotIn("import pickle", source)
        self.assertNotIn("pickle.", source)


if __name__ == "__main__":
    unittest.main()

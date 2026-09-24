from __future__ import annotations

import json
import sys
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xbos_customer_channel.application.catalog_service import CatalogSession
from xbos_customer_channel.application.w1_composition import XBOSW1ContractUnavailable
from xbos_customer_channel.application.w1_conversation import W1NavigationCursor
from xbos_customer_channel.application.w1_whatsapp_runtime import W1RuntimeConversationState
from xbos_customer_channel.catalog import CatalogProjection, InteractionCart
from xbos_customer_channel.entry_context import (
    EntryContextAttestation,
    EntryPurpose,
    MerchantContextProjection,
    ResolvedEntryContext,
)
from xbos_customer_channel.identity import IdentityBindingRecord
from xbos_customer_channel.persistence.postgres.database import PostgresUnavailable
from xbos_customer_channel.persistence.postgres.runtime_state_store import (
    ConversationResolution,
    PersistedRuntimeState,
    StaleRuntimeStateVersion,
)
from xbos_customer_channel.persistence.postgres.serialization import (
    LocatorKeyRing,
    StateSerializationError,
    serialize_runtime_state,
)
from xbos_customer_channel.persistence.postgres.transport_state_stores import (
    IdempotencyConflict,
    IdempotencyResult,
    ProviderMessageReceipt,
)
from xbos_customer_channel.persistence.postgres.w1_runtime_state_port import (
    DuplicateProviderMessage,
    PostgresW1RuntimeStatePort,
    ProviderEndpointMismatch,
    SecureRuntimeSessionRequired,
)
from xbos_customer_channel.session_state import ChannelState, CustomerSessionSnapshot
from xbos_customer_channel.transports.meta_whatsapp import (
    NormalizedInboundMessage,
    UntrustedProviderUserRef,
)


FUTURE = 4_102_444_800


def base_projection() -> MerchantContextProjection:
    return MerchantContextProjection(
        merchant_ref="merchant-1",
        location_ref="location-1",
        service_available=True,
        display_name="Merchant",
        terminology=(),
        currency="XAF",
        allowed_fulfillment_modes=("takeaway",),
    )


def catalog_projection() -> CatalogProjection:
    return CatalogProjection(
        catalog_ref="catalog-1",
        merchant_ref="merchant-1",
        location_ref="location-1",
        version="v1",
        currency="XAF",
        terminology=(),
        sections=(),
        items=(),
    )


def secure_session() -> CustomerSessionSnapshot:
    return CustomerSessionSnapshot(
        session_ref="session-1",
        conversation_ref="conversation-1",
        correlation_ref="correlation-1",
        state=ChannelState.BROWSING,
        entry_token_ref="token-1",
        owner_identity_ref="identity-1",
        tenant_ref="tenant-1",
        merchant_ref="merchant-1",
        location_ref="location-1",
        entry_purpose=EntryPurpose.TAKEAWAY,
        context_binding_ref="context-1",
        created_at_epoch=1_700_000_000,
        expires_at_epoch=FUTURE,
        generation=0,
        security_binding_complete=True,
    )


def runtime_state() -> W1RuntimeConversationState:
    session = secure_session()
    entry = ResolvedEntryContext(
        token_ref="token-1",
        tenant_ref="tenant-1",
        merchant_ref="merchant-1",
        location_ref="location-1",
        table_ref=None,
        dining_area_ref=None,
        purpose=EntryPurpose.TAKEAWAY,
        context_binding_ref="context-1",
        projection=base_projection(),
    )
    return W1RuntimeConversationState(
        session=session,
        catalog_session=CatalogSession(
            entry=entry,
            projection=catalog_projection(),
            cart=InteractionCart(),
        ),
    )


def binding() -> IdentityBindingRecord:
    return IdentityBindingRecord(
        identity_binding_ref="binding-1",
        identity_ref="identity-1",
        canonical_channel_subject_ref="subject-1",
        subject_attestation_ref="subject-attestation-1",
        conversation_ref="conversation-1",
        tenant_ref="tenant-1",
        merchant_ref="merchant-1",
        location_ref="location-1",
        table_ref=None,
        dining_area_ref=None,
        context_binding_ref="context-1",
        issued_at_epoch=1_700_000_000,
        expires_at_epoch=FUTURE,
    )


def inbound(
    ref: str = "wamid.1",
    *,
    text: str = "menu",
    endpoint: str = "phone-id",
    sender: str = "237600000001",
) -> NormalizedInboundMessage:
    return NormalizedInboundMessage(
        provider_message_ref=ref,
        sender=UntrustedProviderUserRef(sender),
        occurred_at_epoch=1_800_000_000,
        text=text,
        interactive_reply=None,
        context_provider_message_ref=None,
        metadata_phone_number_id=endpoint,
    )


class FakeRuntimeStore:
    def __init__(self) -> None:
        state = runtime_state()
        self.conversation = ConversationResolution(
            conversation_ref="conversation-1",
            row_version=1,
            locator_hash_key_version="v2",
            lazy_rehashed=False,
        )
        self.persisted = PersistedRuntimeState(
            session_ref="session-1",
            row_version=1,
            state_schema_version=1,
            interaction_state_json=serialize_runtime_state(state),
            last_provider_message_ref=None,
            last_transition_ref=None,
        )
        self.resolve_error = None
        self.cas_error = None
        self.resolve_calls = 0
        self.cas_calls = 0
        self.last_resolve = None

    def resolve_active_conversation(self, **kwargs):
        self.resolve_calls += 1
        self.last_resolve = kwargs
        if self.resolve_error is not None:
            raise self.resolve_error
        return self.conversation

    def load(self, session_ref):
        if session_ref != self.persisted.session_ref:
            raise KeyError(session_ref)
        return self.persisted

    def compare_and_swap(self, **kwargs):
        self.cas_calls += 1
        if self.cas_error is not None:
            raise self.cas_error
        if kwargs["expected_row_version"] != self.persisted.row_version:
            raise StaleRuntimeStateVersion("runtime_state_compare_and_swap_conflict")
        new_version = self.persisted.row_version + 1
        self.persisted = PersistedRuntimeState(
            session_ref=kwargs["session_ref"],
            row_version=new_version,
            state_schema_version=kwargs["state_schema_version"],
            interaction_state_json=kwargs["interaction_state_json"],
            last_provider_message_ref=kwargs["last_provider_message_ref"],
            last_transition_ref=kwargs["last_transition_ref"],
        )
        return new_version


class FakeSessionStore:
    def __init__(self, session=None) -> None:
        self.session = secure_session() if session is None else session
        self.calls = 0

    def resolve_active_for_conversation(self, conversation_ref, *, now_utc):
        self.calls += 1
        return self.session


class FakeIdentityStore:
    def __init__(self, record=None) -> None:
        self.record = binding() if record is None else record
        self.calls = 0

    def resolve_active_for_session_context(self, session, *, now_utc):
        self.calls += 1
        return self.record


class FakeContext:
    def __init__(self) -> None:
        self.calls = 0
        self.error = None

    def attest_context(self, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        projection = base_projection()
        return EntryContextAttestation(
            tenant_ref="tenant-1",
            merchant_ref="merchant-1",
            location_ref="location-1",
            table_ref=None,
            dining_area_ref=None,
            purpose=EntryPurpose.TAKEAWAY,
            context_binding_ref="context-1",
            projection=projection,
        )


class FakeCatalog:
    def __init__(self) -> None:
        self.calls = 0
        self.error = None

    def get_catalog(self, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return catalog_projection()


class FakeReceiptStore:
    def __init__(self) -> None:
        self.rows = {}
        self.get_calls = []
        self.begin_calls = 0
        self.finish_calls = 0
        self.fail_finish = False

    def get_or_create(self, **kwargs):
        self.get_calls.append(kwargs)
        key = (
            kwargs["provider_code"],
            kwargs["provider_endpoint_ref"],
            kwargs["provider_message_ref"],
            kwargs["event_kind"],
        )
        if key in self.rows:
            return self.rows[key], False
        row = ProviderMessageReceipt(
            provider_message_receipt_ref=UUID(int=len(self.rows) + 1),
            provider_code=kwargs["provider_code"],
            provider_endpoint_ref=kwargs["provider_endpoint_ref"],
            provider_message_ref=kwargs["provider_message_ref"],
            event_kind=kwargs["event_kind"],
            processing_state="received",
            processing_attempt=0,
            row_version=1,
        )
        self.rows[key] = row
        return row, True

    def begin_processing(self, *, receipt_ref, expected_row_version, now_utc):
        self.begin_calls += 1
        row = next(r for r in self.rows.values() if r.provider_message_receipt_ref == receipt_ref)
        updated = replace(
            row,
            processing_state="processing",
            processing_attempt=row.processing_attempt + 1,
            row_version=row.row_version + 1,
        )
        key = (
            row.provider_code,
            row.provider_endpoint_ref,
            row.provider_message_ref,
            row.event_kind,
        )
        self.rows[key] = updated
        return updated

    def finish_processing(self, **kwargs):
        self.finish_calls += 1
        if self.fail_finish:
            raise PostgresUnavailable("customer_channel_postgres_transaction_failed")
        row = next(
            r for r in self.rows.values()
            if r.provider_message_receipt_ref == kwargs["receipt_ref"]
        )
        updated = replace(
            row,
            processing_state=kwargs["processing_state"],
            row_version=row.row_version + 1,
        )
        key = (
            row.provider_code,
            row.provider_endpoint_ref,
            row.provider_message_ref,
            row.event_kind,
        )
        self.rows[key] = updated
        return updated


class FakeIdempotencyStore:
    def __init__(self) -> None:
        self.rows = {}
        self.complete_calls = 0
        self.unknown_calls = 0

    def reserve(self, **kwargs):
        key = (kwargs["scope"], kwargs["idempotency_key"])
        existing = self.rows.get(key)
        if existing is not None:
            if existing.request_fingerprint != kwargs["request_fingerprint"]:
                raise IdempotencyConflict(
                    "idempotency_key_reused_with_different_fingerprint"
                )
            return existing, False
        row = IdempotencyResult(
            idempotency_result_ref=UUID(int=len(self.rows) + 100),
            scope=kwargs["scope"],
            idempotency_key=kwargs["idempotency_key"],
            request_fingerprint=kwargs["request_fingerprint"],
            status="in_progress",
            result_ref=None,
            safe_result_json=None,
            row_version=1,
        )
        self.rows[key] = row
        return row, True

    def complete(self, **kwargs):
        self.complete_calls += 1
        key = next(
            key for key, row in self.rows.items()
            if row.idempotency_result_ref == kwargs["idempotency_result_ref"]
        )
        row = self.rows[key]
        updated = replace(
            row,
            status="completed",
            result_ref=kwargs["result_ref"],
            safe_result_json=kwargs["safe_result_json"],
            row_version=row.row_version + 1,
        )
        self.rows[key] = updated
        return updated

    def mark_unknown(self, **kwargs):
        self.unknown_calls += 1
        key = next(
            key for key, row in self.rows.items()
            if row.idempotency_result_ref == kwargs["idempotency_result_ref"]
        )
        row = self.rows[key]
        updated = replace(row, status="unknown", row_version=row.row_version + 1)
        self.rows[key] = updated
        return updated


def make_port(
    *,
    runtime=None,
    sessions=None,
    identities=None,
    receipts=None,
    idempotency=None,
    context=None,
    catalog=None,
    ring=None,
):
    runtime = FakeRuntimeStore() if runtime is None else runtime
    sessions = FakeSessionStore() if sessions is None else sessions
    identities = FakeIdentityStore() if identities is None else identities
    receipts = FakeReceiptStore() if receipts is None else receipts
    idempotency = FakeIdempotencyStore() if idempotency is None else idempotency
    context = FakeContext() if context is None else context
    catalog = FakeCatalog() if catalog is None else catalog
    ring = LocatorKeyRing("current-key", "v2") if ring is None else ring
    port = PostgresW1RuntimeStatePort(
        runtime_store=runtime,
        session_store=sessions,
        identity_binding_store=identities,
        receipt_store=receipts,
        idempotency_store=idempotency,
        locator_key_ring=ring,
        xbos_context=context,
        xbos_catalog=catalog,
        configured_endpoint_ref="phone-id",
    )
    return port, runtime, sessions, identities, receipts, idempotency, context, catalog


class H1S4DurableRuntimeStatePortTests(unittest.TestCase):
    def test_t01_restart_durability_round_trip(self):
        port, runtime, sessions, identities, receipts, idem, context, catalog = make_port()
        first = inbound("wamid.1")
        state = port.load(first)
        changed = replace(state, navigation=W1NavigationCursor("section-1", None, 1))
        port.save(first, changed)

        restarted, *_ = make_port(
            runtime=runtime,
            sessions=sessions,
            identities=identities,
            receipts=receipts,
            idempotency=idem,
            context=context,
            catalog=catalog,
        )
        restored = restarted.load(inbound("wamid.2"))
        self.assertEqual(restored.navigation.active_section_ref, "section-1")

    def test_t02_multi_instance_row_version_cas(self):
        runtime = FakeRuntimeStore()
        shared = {
            "runtime": runtime,
            "sessions": FakeSessionStore(),
            "identities": FakeIdentityStore(),
            "receipts": FakeReceiptStore(),
            "idempotency": FakeIdempotencyStore(),
            "context": FakeContext(),
            "catalog": FakeCatalog(),
        }
        a, *_ = make_port(**shared)
        b, *_ = make_port(**shared)
        ma, mb = inbound("wamid.a"), inbound("wamid.b")
        sa, sb = a.load(ma), b.load(mb)
        a.save(ma, sa)
        with self.assertRaises(StaleRuntimeStateVersion):
            b.save(mb, sb)

    def test_t03_same_message_deduplicates(self):
        port, runtime, sessions, identities, receipts, idem, context, catalog = make_port()
        message = inbound("wamid.same")
        state = port.load(message)
        port.save(message, state)
        again, *_ = make_port(
            runtime=runtime,
            sessions=sessions,
            identities=identities,
            receipts=receipts,
            idempotency=idem,
            context=context,
            catalog=catalog,
        )
        with self.assertRaises(DuplicateProviderMessage):
            again.load(message)
        self.assertEqual(runtime.cas_calls, 1)

    def test_t04_same_event_key_different_fingerprint_fails_closed(self):
        port, runtime, sessions, identities, receipts, idem, context, catalog = make_port()
        first = inbound("wamid.same", text="menu")
        state = port.load(first)
        port.save(first, state)
        again, *_ = make_port(
            runtime=runtime,
            sessions=sessions,
            identities=identities,
            receipts=receipts,
            idempotency=idem,
            context=context,
            catalog=catalog,
        )
        with self.assertRaises(IdempotencyConflict):
            again.load(inbound("wamid.same", text="changed"))

    def test_t05_concurrent_duplicate_has_one_processing_owner(self):
        runtime = FakeRuntimeStore()
        receipts = FakeReceiptStore()
        idem = FakeIdempotencyStore()
        shared = {
            "runtime": runtime,
            "sessions": FakeSessionStore(),
            "identities": FakeIdentityStore(),
            "receipts": receipts,
            "idempotency": idem,
            "context": FakeContext(),
            "catalog": FakeCatalog(),
        }
        a, *_ = make_port(**shared)
        b, *_ = make_port(**shared)
        message = inbound("wamid.concurrent")
        a.load(message)
        with self.assertRaises(DuplicateProviderMessage):
            b.load(message)
        self.assertEqual(receipts.begin_calls, 1)

    def test_t06_crash_after_cas_never_reapplies_transition(self):
        receipts = FakeReceiptStore()
        receipts.fail_finish = True
        port, runtime, sessions, identities, _, idem, context, catalog = make_port(
            receipts=receipts
        )
        message = inbound("wamid.crash")
        state = port.load(message)
        with self.assertRaises(PostgresUnavailable):
            port.save(message, state)
        self.assertEqual(runtime.cas_calls, 1)

        replay, *_ = make_port(
            runtime=runtime,
            sessions=sessions,
            identities=identities,
            receipts=receipts,
            idempotency=idem,
            context=context,
            catalog=catalog,
        )
        with self.assertRaises(DuplicateProviderMessage):
            replay.load(message)
        self.assertEqual(runtime.cas_calls, 1)

    def test_t07_database_outage_on_load_fails_closed(self):
        runtime = FakeRuntimeStore()
        runtime.resolve_error = PostgresUnavailable("customer_channel_postgres_unavailable")
        port, *_ = make_port(runtime=runtime)
        with self.assertRaises(PostgresUnavailable):
            port.load(inbound())

    def test_t08_database_outage_on_save_is_not_assumed_success(self):
        runtime = FakeRuntimeStore()
        port, _, _, _, _, idem, _, _ = make_port(runtime=runtime)
        message = inbound("wamid.save-outage")
        state = port.load(message)
        runtime.cas_error = PostgresUnavailable("customer_channel_postgres_transaction_failed")
        with self.assertRaises(PostgresUnavailable):
            port.save(message, state)
        self.assertEqual(idem.complete_calls, 0)
    def test_t09_endpoint_mismatch_fails_before_database_lookup(self):
        port, runtime, *_ = make_port()
        with self.assertRaises(ProviderEndpointMismatch):
            port.load(inbound(endpoint="wrong-phone-id"))
        self.assertEqual(runtime.resolve_calls, 0)

    def test_t10_locator_rotation_ring_is_used_for_resolution(self):
        ring = LocatorKeyRing(
            current_key="current-key",
            current_version="v2",
            previous_key="previous-key",
            previous_version="v1",
        )
        port, runtime, *_ = make_port(ring=ring)
        port.load(inbound("wamid.rotation"))
        self.assertIs(runtime.last_resolve["key_ring"], ring)
        self.assertEqual(
            tuple(c.key_version for c in ring.candidates(
                channel_code="whatsapp",
                provider_endpoint_ref="phone-id",
                raw_sender="237600000001",
            )),
            ("v2", "v1"),
        )

    def test_t11_locator_rehash_conflict_fails_closed(self):
        runtime = FakeRuntimeStore()
        runtime.resolve_error = StaleRuntimeStateVersion(
            "conversation_locator_lazy_rehash_conflict"
        )
        port, *_ = make_port(runtime=runtime)
        with self.assertRaises(StaleRuntimeStateVersion):
            port.load(inbound("wamid.rehash-conflict"))

    def test_t12_schema_version_two_fails_closed(self):
        runtime = FakeRuntimeStore()
        runtime.persisted = replace(runtime.persisted, state_schema_version=2)
        port, _, _, _, receipts, *_ = make_port(runtime=runtime)
        with self.assertRaises(StateSerializationError):
            port.load(inbound("wamid.schema"))
        self.assertEqual(len(receipts.get_calls), 0)

    def test_t13_security_binding_must_be_revalidated(self):
        identities = FakeIdentityStore()
        identities.record = None
        port, *_ = make_port(identities=identities)
        with self.assertRaises(SecureRuntimeSessionRequired):
            port.load(inbound("wamid.binding"))

    def test_t14_no_new_conversation_bootstrap_without_xbos_truth(self):
        runtime = FakeRuntimeStore()
        runtime.conversation = None
        context = FakeContext()
        port, *_ = make_port(runtime=runtime, context=context)
        with self.assertRaises(XBOSW1ContractUnavailable):
            port.load(inbound("wamid.new"))
        self.assertEqual(context.calls, 0)

    def test_t15_no_stale_catalog_fallback(self):
        catalog = FakeCatalog()
        catalog.error = RuntimeError("catalog unavailable")
        port, _, _, _, receipts, *_ = make_port(catalog=catalog)
        with self.assertRaisesRegex(RuntimeError, "catalog unavailable"):
            port.load(inbound("wamid.catalog"))
        self.assertEqual(len(receipts.get_calls), 0)

    def test_t16_raw_sender_is_never_persisted(self):
        raw = "237699999999"
        port, runtime, _, _, receipts, idem, *_ = make_port()
        port.load(inbound("wamid.privacy", sender=raw))
        call = receipts.get_calls[0]
        self.assertNotEqual(call["sender_lookup_hash"], raw)
        self.assertEqual(len(call["sender_lookup_hash"]), 64)
        self.assertNotIn(raw, json.dumps(runtime.persisted.interaction_state_json))
        self.assertNotIn(raw, repr(call))
        self.assertNotIn(raw, repr(idem.rows))


if __name__ == "__main__":
    unittest.main()


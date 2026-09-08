import ast
import inspect
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xbos_customer_channel.adapters.fake_context import FakeXBOSContextClient
from xbos_customer_channel.adapters.fake_identity import (
    ConsentIdempotencyConflict,
    FakeCustomerIdentityService,
    SubjectEvidenceError,
)
from xbos_customer_channel.adapters.fake_state_reconciliation import FakeXBOSStateReconciliationClient
from xbos_customer_channel.application.identity_service import ChannelIdentityService
from xbos_customer_channel.application.session_service import CustomerSessionService, SessionSecurityRequired
from xbos_customer_channel.entry_context import EntryPurpose, ResolvedEntryContext
from xbos_customer_channel.identity import ChannelIdentityState, ConsentPurpose
from xbos_customer_channel.persistence.identity_records import InMemoryIdentityBindingStore
from xbos_customer_channel.persistence.session_records import InMemoryCustomerSessionStore


class CR3IdentitySecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.identity = FakeCustomerIdentityService()
        self.bindings = InMemoryIdentityBindingStore()
        self.identity_service = ChannelIdentityService(
            self.identity,
            consent_policy=self.identity,
            binding_store=self.bindings,
            identity_secret="cr3-fixture-secret",
        )
        self.context = FakeXBOSContextClient()
        self.sessions_store = InMemoryCustomerSessionStore()
        self.sessions = CustomerSessionService(
            store=self.sessions_store,
            reconciliation=FakeXBOSStateReconciliationClient(),
            xbos_context=self.context,
            identity_binding_store=self.bindings,
        )
        self._counter = 0

    def entry(self, *, merchant="merchant:fixture:alpha", location="location:fixture:one", table="table:fixture:a1"):
        area = "area:fixture:main" if table in {"table:fixture:a1", "table:fixture:a2"} else None
        att = self.context.attest_context(
            merchant_ref=merchant,
            location_ref=location,
            table_ref=table,
            dining_area_ref=area,
            purpose=EntryPurpose.DINE_IN,
        )
        self._counter += 1
        return ResolvedEntryContext(
            token_ref=f"entry:cr3:{self._counter}",
            merchant_ref=att.merchant_ref,
            location_ref=att.location_ref,
            table_ref=att.table_ref,
            dining_area_ref=att.dining_area_ref,
            purpose=att.purpose,
            projection=att.projection,
            tenant_ref=att.tenant_ref,
            context_binding_ref=att.context_binding_ref,
        )

    def register_subject(self, label: str, *, locator=None, channel="whatsapp", candidates=(), expires=5000):
        locator = locator or f"locator:{label}"
        evidence = f"subject-evidence:{label}"
        canonical = f"canonical-subject:{label}"
        self.identity.register_subject_evidence(
            channel=channel,
            channel_user_ref=locator,
            subject_evidence_ref=evidence,
            canonical_channel_subject_ref=canonical,
            expires_at_epoch=expires,
            candidate_party_refs=tuple(candidates),
        )
        return locator, evidence, canonical

    def resolve(self, label: str, *, conversation=None, candidates=()):
        locator, evidence, canonical = self.register_subject(label, candidates=candidates)
        conversation = conversation or f"conversation:{label}"
        outcome = self.identity_service.resolve(
            channel="whatsapp",
            channel_user_ref=locator,
            subject_evidence_ref=evidence,
            conversation_ref=conversation,
            now_epoch=1000,
        )
        return outcome.identity, locator, evidence, canonical

    def binding(self, label: str, *, entry=None, conversation=None, expires=4000, candidates=()):
        entry = entry or self.entry()
        locator, evidence, _ = self.register_subject(label, candidates=candidates, expires=5000)
        conversation = conversation or f"conversation:{label}"
        binding = self.identity_service.issue_session_identity_binding(
            channel="whatsapp",
            channel_user_ref=locator,
            subject_evidence_ref=evidence,
            conversation_ref=conversation,
            entry_context=entry,
            now_epoch=1000,
            expires_at_epoch=expires,
        )
        return binding, entry, locator, evidence

    def secure_session(self, label="a", *, entry=None, conversation=None):
        conversation = conversation or f"conversation:{label}"
        binding, entry, _, _ = self.binding(label, entry=entry, conversation=conversation)
        session = self.sessions.create_session(
            conversation_ref=conversation,
            correlation_ref=f"correlation:{label}",
            identity_binding_ref=binding.identity_binding_ref,
            entry_context=entry,
            now_epoch=1000,
            expires_at_epoch=3000,
        )
        return session, binding, entry

    # A01-A30
    def test_cr3_a01_caller_asserted_identity_not_verified_identity(self):
        identity, *_ = self.resolve("a01")
        self.assertNotEqual(identity.state, ChannelIdentityState.VERIFIED)

    def test_cr3_a02_server_side_verification_required(self):
        identity, *_ = self.resolve("a02")
        with self.assertRaisesRegex(PermissionError, "not_server_resolved"):
            self.identity_service.verify(identity_ref=identity.identity_ref, verification_evidence_ref="arbitrary", now_epoch=1000)

    def test_cr3_a03_owner_identity_ref_not_caller_authority(self):
        entry = self.entry()
        with self.assertRaisesRegex(SessionSecurityRequired, "caller_owner_identity_ref_not_authority"):
            self.sessions.create_session(
                conversation_ref="conversation:a03",
                correlation_ref="correlation:a03",
                owner_identity_ref="identity:claimed",
                entry_context=entry,
                now_epoch=1000,
                expires_at_epoch=2000,
            )

    def test_cr3_a04_unverified_identity_cannot_gain_verified_privileges(self):
        identity, *_ = self.resolve("a04", candidates=("party:1",))
        with self.assertRaisesRegex(PermissionError, "verification_required"):
            self.identity_service.link(
                identity_ref=identity.identity_ref,
                party_ref="party:1",
                verification_ref="verification:any",
                entry_context=self.entry(),
            )

    def test_cr3_a05_normalization_collision_fails_closed(self):
        a_locator, a_evidence, _ = self.register_subject("a05a", locator="+237600000000")
        b_locator, b_evidence, _ = self.register_subject("a05b", locator="237 600 000 000")
        a = self.identity_service.resolve(channel="whatsapp", channel_user_ref=a_locator, subject_evidence_ref=a_evidence, conversation_ref="c:a", now_epoch=1000).identity
        b = self.identity_service.resolve(channel="whatsapp", channel_user_ref=b_locator, subject_evidence_ref=b_evidence, conversation_ref="c:b", now_epoch=1000).identity
        self.assertNotEqual(a.identity_ref, b.identity_ref)

    def test_cr3_a06_verified_identifier_cannot_bind_two_identities_silently(self):
        a, *_ = self.resolve("a06a")
        b, *_ = self.resolve("a06b")
        self.identity.register_verification_evidence(identity_ref=a.identity_ref, verification_evidence_ref="verify:a06", expires_at_epoch=5000)
        self.identity_service.verify(identity_ref=a.identity_ref, verification_evidence_ref="verify:a06", now_epoch=1000)
        with self.assertRaisesRegex(PermissionError, "identity_mismatch"):
            self.identity_service.verify(identity_ref=b.identity_ref, verification_evidence_ref="verify:a06", now_epoch=1000)

    def test_cr3_a07_duplicate_identity_resolution_retry_is_idempotent(self):
        locator, evidence1, canonical = self.register_subject("a07", locator="same-user")
        self.identity.register_subject_evidence(channel="whatsapp", channel_user_ref=locator, subject_evidence_ref="subject-evidence:a07b", canonical_channel_subject_ref=canonical, expires_at_epoch=5000)
        a = self.identity_service.resolve(channel="whatsapp", channel_user_ref=locator, subject_evidence_ref=evidence1, conversation_ref="conv:a", now_epoch=1000).identity
        b = self.identity_service.resolve(channel="whatsapp", channel_user_ref=locator, subject_evidence_ref="subject-evidence:a07b", conversation_ref="conv:b", now_epoch=1000).identity
        self.assertEqual(a.identity_ref, b.identity_ref)

    def test_cr3_a08_same_idempotency_key_changed_identity_payload_conflicts(self):
        a, *_ = self.resolve("a08a")
        b, *_ = self.resolve("a08b")
        self.identity_service.set_consent(identity_ref=a.identity_ref, purpose=ConsentPurpose.SUPPORT, granted=True, evidence_ref="e:a", idempotency_key="idem:a08")
        with self.assertRaisesRegex(ConsentIdempotencyConflict, "payload_conflict"):
            self.identity_service.set_consent(identity_ref=b.identity_ref, purpose=ConsentPurpose.SUPPORT, granted=True, evidence_ref="e:b", idempotency_key="idem:a08")

    def test_cr3_a09_consent_retry_idempotent(self):
        identity, *_ = self.resolve("a09")
        a = self.identity_service.set_consent(identity_ref=identity.identity_ref, purpose=ConsentPurpose.SUPPORT, granted=True, evidence_ref="e", idempotency_key="idem:a09")
        b = self.identity_service.set_consent(identity_ref=identity.identity_ref, purpose=ConsentPurpose.SUPPORT, granted=True, evidence_ref="e", idempotency_key="idem:a09")
        self.assertEqual(a, b)

    def test_cr3_a10_consent_version_change_requires_new_decision(self):
        identity, *_ = self.resolve("a10")
        self.identity_service.set_consent(identity_ref=identity.identity_ref, purpose=ConsentPurpose.MARKETING, granted=True, evidence_ref="e1", idempotency_key="idem:a10:1")
        self.identity.set_policy_version(ConsentPurpose.MARKETING, "v2")
        self.assertFalse(self.identity_service.purpose_allowed(identity_ref=identity.identity_ref, purpose=ConsentPurpose.MARKETING))
        fresh = self.identity_service.set_consent(identity_ref=identity.identity_ref, purpose=ConsentPurpose.MARKETING, granted=True, evidence_ref="e2", idempotency_key="idem:a10:2")
        self.assertEqual(fresh.consent_version, "v2")

    def test_cr3_a11_consent_withdrawal_not_erased_by_retry(self):
        identity, *_ = self.resolve("a11")
        grant = self.identity_service.set_consent(identity_ref=identity.identity_ref, purpose=ConsentPurpose.ORDER_UPDATES, granted=True, evidence_ref="grant", idempotency_key="idem:a11:g")
        self.identity_service.set_consent(identity_ref=identity.identity_ref, purpose=ConsentPurpose.ORDER_UPDATES, granted=False, evidence_ref="withdraw", idempotency_key="idem:a11:w")
        replay = self.identity_service.set_consent(identity_ref=identity.identity_ref, purpose=ConsentPurpose.ORDER_UPDATES, granted=True, evidence_ref="grant", idempotency_key="idem:a11:g")
        self.assertEqual(replay, grant)
        self.assertFalse(self.identity_service.purpose_allowed(identity_ref=identity.identity_ref, purpose=ConsentPurpose.ORDER_UPDATES))

    def test_cr3_a12_session_rotation_preserves_server_bound_identity(self):
        session, binding, _ = self.secure_session("a12")
        rotated = self.sessions.resume_session(session_ref=session.session_ref, identity_binding_ref=binding.identity_binding_ref, now_epoch=1001)
        self.assertEqual(session.owner_identity_ref, rotated.owner_identity_ref)

    def test_cr3_a13_cross_session_identity_substitution_denied(self):
        session_a, _, entry = self.secure_session("a13a")
        binding_b, _, _, _ = self.binding("a13b", entry=entry, conversation=session_a.conversation_ref)
        with self.assertRaisesRegex(PermissionError, "session_owner_mismatch"):
            self.sessions.resume_session(session_ref=session_a.session_ref, identity_binding_ref=binding_b.identity_binding_ref, now_epoch=1001)

    def test_cr3_a14_cross_tenant_or_context_substitution_denied(self):
        alpha = self.entry()
        beta = self.entry(merchant="merchant:fixture:beta", location="location:fixture:two", table="table:fixture:b1")
        binding, _, _, _ = self.binding("a14", entry=beta, conversation="conversation:a14")
        with self.assertRaisesRegex(SessionSecurityRequired, "context_mismatch"):
            self.sessions.create_session(conversation_ref="conversation:a14", correlation_ref="corr:a14", identity_binding_ref=binding.identity_binding_ref, entry_context=alpha, now_epoch=1000, expires_at_epoch=2000)

    def test_cr3_a15_cross_merchant_identity_substitution_denied(self):
        self.test_cr3_a14_cross_tenant_or_context_substitution_denied()

    def test_cr3_a16_identity_lookup_does_not_create_second_party_master(self):
        public = {n for n in dir(ChannelIdentityService) if not n.startswith("_")}
        self.assertNotIn("create_customer", public)
        self.assertNotIn("create_party", public)

    def test_cr3_a17_no_wallet_identity_authority(self):
        text = Path(inspect.getsourcefile(ChannelIdentityService) or "").read_text().lower()
        self.assertNotIn("wallet", text)

    def test_cr3_a18_no_core_party_authority(self):
        text = Path(inspect.getsourcefile(ChannelIdentityService) or "").read_text().lower()
        self.assertNotIn("xafpay_core", text)
        self.assertNotIn("core_party", text)

    def test_cr3_a19_no_payment_authority(self):
        public = {n for n in dir(ChannelIdentityService) if not n.startswith("_")}
        self.assertNotIn("create_payment_request", public)
        self.assertNotIn("mark_paid", public)

    def test_cr3_a20_cr1_security_non_regression(self):
        session, binding, _ = self.secure_session("a20")
        rotated = self.sessions.resume_session(session_ref=session.session_ref, identity_binding_ref=binding.identity_binding_ref, now_epoch=1001)
        self.assertEqual(rotated.generation, 1)

    def test_cr3_a21_cr2_security_non_regression(self):
        source = Path(__file__).resolve().parents[1] / "src" / "xbos_customer_channel" / "application" / "session_service.py"
        text = source.read_text()
        self.assertIn("caller_supplied_upstream_projection_not_authority", text)
        self.assertIn("server_side_authoritative_evidence_required", text)

    def test_cr3_a22_finding_scope_not_false_final_close(self):
        contract = (Path(__file__).resolve().parents[1] / "contracts" / "CR3_IDENTITY_VERIFICATION_COLLISION_CONSENT_SECURITY_CONTRACT_V1.md").read_text()
        self.assertIn("SOURCE_REMEDIATION_IMPLEMENTED_NOT_CLOSED", contract)
        self.assertNotIn("A0_007=FINAL_CLOSED", contract)

    def test_cr3_a23_hmac_of_caller_controlled_subject_is_not_provenance(self):
        _, evidence, _ = self.register_subject("a23", locator="locator:a")
        with self.assertRaisesRegex(SubjectEvidenceError, "locator_attestation_mismatch"):
            self.identity_service.resolve(channel="whatsapp", channel_user_ref="locator:b", subject_evidence_ref=evidence, conversation_ref="conv:a23", now_epoch=1000)

    def test_cr3_a24_subject_server_established_before_binding(self):
        with self.assertRaisesRegex(SubjectEvidenceError, "unknown_subject_evidence"):
            self.identity_service.issue_session_identity_binding(channel="whatsapp", channel_user_ref="locator:any", subject_evidence_ref="unknown", conversation_ref="conv:a24", entry_context=self.entry(), now_epoch=1000, expires_at_epoch=2000)

    def test_cr3_a25_caller_cannot_bind_session_to_another_subject(self):
        session, _, entry = self.secure_session("a25a")
        other, _, _, _ = self.binding("a25b", entry=entry, conversation=session.conversation_ref)
        with self.assertRaisesRegex(PermissionError, "session_owner_mismatch"):
            self.sessions.resume_session(session_ref=session.session_ref, identity_binding_ref=other.identity_binding_ref, now_epoch=1001)

    def test_cr3_a26_binding_issuer_rejects_unknown_unproven_subject(self):
        self.test_cr3_a24_subject_server_established_before_binding()

    def test_cr3_a27_consent_version_is_server_governed(self):
        sig = inspect.signature(ChannelIdentityService.set_consent)
        self.assertNotIn("consent_version", sig.parameters)
        self.assertIn("current_version", inspect.getsource(ChannelIdentityService.set_consent))

    def test_cr3_a28_caller_cannot_select_consent_policy_version(self):
        self.test_cr3_a27_consent_version_is_server_governed()

    def test_cr3_a29_stale_unknown_consent_version_no_current_permission(self):
        identity, *_ = self.resolve("a29")
        self.identity_service.set_consent(identity_ref=identity.identity_ref, purpose=ConsentPurpose.TRANSACTIONAL, granted=True, evidence_ref="e", idempotency_key="idem:a29")
        self.identity.set_policy_version(ConsentPurpose.TRANSACTIONAL, "v99")
        self.assertFalse(self.identity_service.purpose_allowed(identity_ref=identity.identity_ref, purpose=ConsentPurpose.TRANSACTIONAL))

    def test_cr3_a30_policy_version_change_requires_fresh_decision(self):
        self.test_cr3_a10_consent_version_change_requires_new_decision()

    # C01-C12
    def test_cr3_c01_session_a_plus_binding_b_denied(self):
        self.test_cr3_a13_cross_session_identity_substitution_denied()

    def test_cr3_c02_identity_a_plus_verification_b_denied(self):
        self.test_cr3_a06_verified_identifier_cannot_bind_two_identities_silently()

    def test_cr3_c03_concurrent_same_subject_resolution_one_stable_identity(self):
        locator, evidence, canonical = self.register_subject("c03", locator="same-c03")
        for i in range(1, 8):
            self.identity.register_subject_evidence(channel="whatsapp", channel_user_ref=locator, subject_evidence_ref=f"c03:{i}", canonical_channel_subject_ref=canonical, expires_at_epoch=5000)

        def one(i):
            return self.identity_service.resolve(channel="whatsapp", channel_user_ref=locator, subject_evidence_ref=f"c03:{i}", conversation_ref=f"conv:c03:{i}", now_epoch=1000).identity.identity_ref

        with ThreadPoolExecutor(max_workers=7) as pool:
            refs = list(pool.map(one, range(1, 8)))
        self.assertEqual(len(set(refs)), 1)

    def test_cr3_c04_same_idempotency_key_different_payload_conflict(self):
        identity, *_ = self.resolve("c04")
        self.identity_service.set_consent(identity_ref=identity.identity_ref, purpose=ConsentPurpose.SUPPORT, granted=True, evidence_ref="e1", idempotency_key="idem:c04")
        with self.assertRaisesRegex(ConsentIdempotencyConflict, "payload_conflict"):
            self.identity_service.set_consent(identity_ref=identity.identity_ref, purpose=ConsentPurpose.SUPPORT, granted=False, evidence_ref="e2", idempotency_key="idem:c04")

    def test_cr3_c05_consent_retry_same_version_same_result(self):
        self.test_cr3_a09_consent_retry_idempotent()

    def test_cr3_c06_consent_retry_different_version_new_decision_or_conflict(self):
        identity, *_ = self.resolve("c06")
        self.identity_service.set_consent(identity_ref=identity.identity_ref, purpose=ConsentPurpose.RECEIPT_DELIVERY, granted=True, evidence_ref="e", idempotency_key="idem:c06")
        self.identity.set_policy_version(ConsentPurpose.RECEIPT_DELIVERY, "v2")
        with self.assertRaisesRegex(ConsentIdempotencyConflict, "payload_conflict"):
            self.identity_service.set_consent(identity_ref=identity.identity_ref, purpose=ConsentPurpose.RECEIPT_DELIVERY, granted=True, evidence_ref="e", idempotency_key="idem:c06")

    def test_cr3_c07_rotation_predecessor_or_different_identity_assertion_denied(self):
        session, binding, _ = self.secure_session("c07")
        self.sessions.resume_session(session_ref=session.session_ref, identity_binding_ref=binding.identity_binding_ref, now_epoch=1001)
        with self.assertRaisesRegex(PermissionError, "stale_or_rotated"):
            self.sessions.resume_session(session_ref=session.session_ref, identity_binding_ref=binding.identity_binding_ref, now_epoch=1002)

    def test_cr3_c08_response_loss_recovery_idempotent(self):
        identity, *_ = self.resolve("c08")
        first = self.identity_service.set_consent(identity_ref=identity.identity_ref, purpose=ConsentPurpose.SUPPORT, granted=True, evidence_ref="e", idempotency_key="idem:c08")
        second = self.identity_service.set_consent(identity_ref=identity.identity_ref, purpose=ConsentPurpose.SUPPORT, granted=True, evidence_ref="e", idempotency_key="idem:c08")
        self.assertEqual(first, second)

    def test_cr3_c09_caller_a_plus_subject_b_denied_or_verification_required(self):
        _, evidence, _ = self.register_subject("c09", locator="locator:a")
        with self.assertRaisesRegex(SubjectEvidenceError, "locator_attestation_mismatch"):
            self.identity_service.resolve(channel="whatsapp", channel_user_ref="locator:b", subject_evidence_ref=evidence, conversation_ref="conv:c09", now_epoch=1000)

    def test_cr3_c10_valid_identity_a_plus_binding_request_subject_b_denied(self):
        self.test_cr3_a25_caller_cannot_bind_session_to_another_subject()

    def test_cr3_c11_caller_supplied_stale_consent_version_no_permission(self):
        self.test_cr3_a29_stale_unknown_consent_version_no_current_permission()

    def test_cr3_c12_caller_supplied_unknown_future_version_no_permission(self):
        self.test_cr3_a27_consent_version_is_server_governed()


if __name__ == "__main__":
    unittest.main()

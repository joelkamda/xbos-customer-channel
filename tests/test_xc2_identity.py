import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xbos_customer_channel.adapters.fake_context import FakeXBOSContextClient
from xbos_customer_channel.adapters.fake_identity import FakeCustomerIdentityService
from xbos_customer_channel.application.identity_service import ChannelIdentityService
from xbos_customer_channel.entry_context import EntryPurpose, ResolvedEntryContext
from xbos_customer_channel.identity import ChannelIdentityState, ConsentPurpose
from xbos_customer_channel.persistence.identity_records import InMemoryIdentityBindingStore


class XC2IdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = FakeCustomerIdentityService()
        self.bindings = InMemoryIdentityBindingStore()
        self.service = ChannelIdentityService(
            self.adapter,
            consent_policy=self.adapter,
            binding_store=self.bindings,
            identity_secret="fixture-identity-secret",
        )
        self.context = FakeXBOSContextClient()

    def register_subject(
        self,
        *,
        locator: str,
        evidence: str,
        canonical: str,
        candidates: tuple[str, ...] = (),
        expires: int = 5000,
    ) -> None:
        self.adapter.register_subject_evidence(
            channel="whatsapp",
            channel_user_ref=locator,
            subject_evidence_ref=evidence,
            canonical_channel_subject_ref=canonical,
            expires_at_epoch=expires,
            candidate_party_refs=candidates,
        )

    def resolve(self, *, locator="whatsapp-user-1", evidence="subject-evidence-1", conversation="conversation:1"):
        return self.service.resolve(
            channel="whatsapp",
            channel_user_ref=locator,
            subject_evidence_ref=evidence,
            conversation_ref=conversation,
            now_epoch=1000,
        )

    def entry_context(self) -> ResolvedEntryContext:
        att = self.context.attest_context(
            merchant_ref="merchant:fixture:alpha",
            location_ref="location:fixture:one",
            table_ref="table:fixture:a1",
            dining_area_ref="area:fixture:main",
            purpose=EntryPurpose.DINE_IN,
        )
        return ResolvedEntryContext(
            token_ref="entry:xc2",
            merchant_ref=att.merchant_ref,
            location_ref=att.location_ref,
            table_ref=att.table_ref,
            dining_area_ref=att.dining_area_ref,
            purpose=att.purpose,
            projection=att.projection,
            tenant_ref=att.tenant_ref,
            context_binding_ref=att.context_binding_ref,
        )

    def test_unknown_channel_user_is_anonymous(self) -> None:
        self.register_subject(locator="unknown-user", evidence="subject-unknown", canonical="subject:unknown")
        outcome = self.resolve(locator="unknown-user", evidence="subject-unknown")
        self.assertEqual(outcome.identity.state, ChannelIdentityState.ANONYMOUS)
        self.assertIsNone(outcome.identity.linked_party_ref)

    def test_lookup_hit_is_recognized_not_verified_or_linked(self) -> None:
        self.register_subject(
            locator="whatsapp-user-1",
            evidence="subject-recognized",
            canonical="subject:one",
            candidates=("party:xbos:42",),
        )
        outcome = self.resolve(evidence="subject-recognized", conversation="conversation:2")
        self.assertEqual(outcome.identity.state, ChannelIdentityState.RECOGNIZED)
        self.assertFalse(outcome.may_link)
        self.assertIsNone(outcome.identity.verification_ref)
        self.assertIsNone(outcome.identity.linked_party_ref)

    def test_link_requires_prior_server_verification_and_matching_reference(self) -> None:
        self.register_subject(
            locator="whatsapp-user-1",
            evidence="subject-link",
            canonical="subject:one",
            candidates=("party:xbos:42",),
        )
        identity = self.resolve(evidence="subject-link", conversation="conversation:3").identity
        with self.assertRaisesRegex(PermissionError, "verification_required"):
            self.service.link(
                identity_ref=identity.identity_ref,
                party_ref="party:xbos:42",
                verification_ref="made-up",
                entry_context=self.entry_context(),
            )

        self.adapter.register_verification_evidence(
            identity_ref=identity.identity_ref,
            verification_evidence_ref="challenge:fixture:ok",
            expires_at_epoch=5000,
        )
        verified = self.service.verify(
            identity_ref=identity.identity_ref,
            verification_evidence_ref="challenge:fixture:ok",
            now_epoch=1000,
        )
        self.assertEqual(verified.state, ChannelIdentityState.VERIFIED)
        self.assertIsNone(verified.linked_party_ref)

        linked = self.service.link(
            identity_ref=verified.identity_ref,
            party_ref="party:xbos:42",
            verification_ref=verified.verification_ref or "",
            entry_context=self.entry_context(),
        )
        self.assertEqual(linked.state, ChannelIdentityState.LINKED)
        self.assertEqual(linked.linked_party_ref, "party:xbos:42")
        self.assertEqual(linked.linked_merchant_ref, "merchant:fixture:alpha")

    def test_verification_does_not_create_consent(self) -> None:
        self.register_subject(locator="whatsapp-user-1", evidence="subject-verify", canonical="subject:one")
        identity = self.resolve(evidence="subject-verify", conversation="conversation:4").identity
        self.adapter.register_verification_evidence(
            identity_ref=identity.identity_ref,
            verification_evidence_ref="challenge:fixture:ok",
            expires_at_epoch=5000,
        )
        verified = self.service.verify(
            identity_ref=identity.identity_ref,
            verification_evidence_ref="challenge:fixture:ok",
            now_epoch=1000,
        )
        self.assertEqual(verified.state, ChannelIdentityState.VERIFIED)
        for purpose in ConsentPurpose:
            self.assertFalse(self.service.purpose_allowed(identity_ref=verified.identity_ref, purpose=purpose))

    def test_consent_is_purpose_specific_server_versioned_and_idempotent(self) -> None:
        self.register_subject(locator="whatsapp-user-1", evidence="subject-consent", canonical="subject:one")
        identity = self.resolve(evidence="subject-consent", conversation="conversation:5").identity
        first = self.service.set_consent(
            identity_ref=identity.identity_ref,
            purpose=ConsentPurpose.ORDER_UPDATES,
            granted=True,
            evidence_ref="consent:event:1",
            idempotency_key="idem:consent:1",
        )
        replay = self.service.set_consent(
            identity_ref=identity.identity_ref,
            purpose=ConsentPurpose.ORDER_UPDATES,
            granted=True,
            evidence_ref="consent:event:1",
            idempotency_key="idem:consent:1",
        )
        self.assertEqual(first, replay)
        self.assertEqual(first.consent_version, "v1")
        self.assertTrue(self.service.purpose_allowed(identity_ref=identity.identity_ref, purpose=ConsentPurpose.ORDER_UPDATES))
        self.assertFalse(self.service.purpose_allowed(identity_ref=identity.identity_ref, purpose=ConsentPurpose.MARKETING))

    def test_restricted_identity_cannot_be_verified(self) -> None:
        self.register_subject(locator="whatsapp-user-1", evidence="subject-restricted", canonical="subject:one")
        identity = self.resolve(evidence="subject-restricted", conversation="conversation:6").identity
        restricted = self.adapter.restrict_identity(identity_ref=identity.identity_ref, restriction_ref="restriction:fixture:1")
        self.assertEqual(restricted.state, ChannelIdentityState.BLOCKED_OR_RESTRICTED)
        self.adapter.register_verification_evidence(
            identity_ref=identity.identity_ref,
            verification_evidence_ref="challenge:fixture:ok",
            expires_at_epoch=5000,
        )
        with self.assertRaisesRegex(PermissionError, "identity_restricted"):
            self.service.verify(
                identity_ref=identity.identity_ref,
                verification_evidence_ref="challenge:fixture:ok",
                now_epoch=1000,
            )

    def test_same_server_subject_is_stable_across_conversations(self) -> None:
        self.register_subject(locator="whatsapp-user-1", evidence="subject-one-a", canonical="subject:one")
        self.register_subject(locator="whatsapp-user-1", evidence="subject-one-b", canonical="subject:one")
        first = self.resolve(evidence="subject-one-a", conversation="conversation:a").identity
        second = self.resolve(evidence="subject-one-b", conversation="conversation:b").identity
        self.assertEqual(first.identity_ref, second.identity_ref)

    def test_different_server_subjects_in_same_conversation_do_not_collide(self) -> None:
        self.register_subject(locator="user-a", evidence="subject-a", canonical="subject:a")
        self.register_subject(locator="user-b", evidence="subject-b", canonical="subject:b")
        a = self.resolve(locator="user-a", evidence="subject-a", conversation="conversation:same").identity
        b = self.resolve(locator="user-b", evidence="subject-b", conversation="conversation:same").identity
        self.assertNotEqual(a.identity_ref, b.identity_ref)


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xbos_customer_channel.adapters.fake_identity import FakeCustomerIdentityService
from xbos_customer_channel.application.identity_service import ChannelIdentityService
from xbos_customer_channel.identity import ChannelIdentityState, ConsentPurpose


class XC2IdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = FakeCustomerIdentityService({"whatsapp-user-1": ("party:xbos:42",)})
        self.service = ChannelIdentityService(self.adapter)

    def test_unknown_channel_user_is_anonymous(self) -> None:
        outcome = self.service.resolve(
            channel="whatsapp",
            channel_user_ref="unknown-user",
            conversation_ref="conversation:1",
        )
        self.assertEqual(outcome.identity.state, ChannelIdentityState.ANONYMOUS)
        self.assertIsNone(outcome.identity.linked_party_ref)

    def test_lookup_hit_is_recognized_not_verified_or_linked(self) -> None:
        outcome = self.service.resolve(
            channel="whatsapp",
            channel_user_ref="whatsapp-user-1",
            conversation_ref="conversation:2",
        )
        self.assertEqual(outcome.identity.state, ChannelIdentityState.RECOGNIZED)
        self.assertFalse(outcome.may_link)
        self.assertIsNone(outcome.identity.verification_ref)
        self.assertIsNone(outcome.identity.linked_party_ref)

    def test_link_requires_prior_verification_and_matching_reference(self) -> None:
        identity = self.service.resolve(
            channel="whatsapp",
            channel_user_ref="whatsapp-user-1",
            conversation_ref="conversation:3",
        ).identity
        with self.assertRaisesRegex(PermissionError, "verification_required"):
            self.service.link(
                identity_ref=identity.identity_ref,
                party_ref="party:xbos:42",
                verification_ref="made-up",
            )

        verified = self.service.verify(
            identity_ref=identity.identity_ref,
            verification_evidence_ref="challenge:fixture:ok",
        )
        self.assertEqual(verified.state, ChannelIdentityState.VERIFIED)
        self.assertIsNone(verified.linked_party_ref)

        linked = self.service.link(
            identity_ref=verified.identity_ref,
            party_ref="party:xbos:42",
            verification_ref=verified.verification_ref or "",
        )
        self.assertEqual(linked.state, ChannelIdentityState.LINKED)
        self.assertEqual(linked.linked_party_ref, "party:xbos:42")

    def test_verification_does_not_create_consent(self) -> None:
        identity = self.service.resolve(
            channel="whatsapp",
            channel_user_ref="whatsapp-user-1",
            conversation_ref="conversation:4",
        ).identity
        verified = self.service.verify(
            identity_ref=identity.identity_ref,
            verification_evidence_ref="challenge:fixture:ok",
        )
        self.assertEqual(verified.state, ChannelIdentityState.VERIFIED)
        for purpose in ConsentPurpose:
            self.assertFalse(self.service.purpose_allowed(identity_ref=verified.identity_ref, purpose=purpose))

    def test_consent_is_purpose_specific_and_idempotent(self) -> None:
        identity = self.service.resolve(
            channel="whatsapp",
            channel_user_ref="whatsapp-user-1",
            conversation_ref="conversation:5",
        ).identity
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
        self.assertTrue(self.service.purpose_allowed(identity_ref=identity.identity_ref, purpose=ConsentPurpose.ORDER_UPDATES))
        self.assertFalse(self.service.purpose_allowed(identity_ref=identity.identity_ref, purpose=ConsentPurpose.MARKETING))

    def test_restricted_identity_cannot_be_verified(self) -> None:
        identity = self.service.resolve(
            channel="whatsapp",
            channel_user_ref="whatsapp-user-1",
            conversation_ref="conversation:6",
        ).identity
        restricted = self.adapter.restrict_identity(identity_ref=identity.identity_ref, restriction_ref="restriction:fixture:1")
        self.assertEqual(restricted.state, ChannelIdentityState.BLOCKED_OR_RESTRICTED)
        with self.assertRaisesRegex(PermissionError, "identity_restricted"):
            self.service.verify(identity_ref=identity.identity_ref, verification_evidence_ref="challenge:fixture:ok")


if __name__ == "__main__":
    unittest.main()

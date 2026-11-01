"""Track A: TEST-only signed Twilio MENU → trusted CC/WND menu boundary.

Never activates from an env flag alone. The caller must provide the verified
first provider claim and independent durable receipt/session ports. No order,
catalog fixture, payment, or outbound REST fallback.
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from typing import Protocol

from ..adapters.real_xbos_catalog import RealXBOSCatalogAdapter
from ..application.swes_wnd_menu_orchestration import (
    SWESMenuReply,
    TrustedProcessedReceiptPort,
    prepare_trusted_wnd_menu_reply,
)
from ..application.swes_wnd_verified_menu import (
    SWESWNDMenuNotAdmitted,
    SWESWNDMenuReadGate,
)
from ..channel_contract import ChannelInboundEvent
from ..session_state import ChannelState, CustomerSessionSnapshot
from .twilio_signed_ingress import SandboxIngressConfig, VerifiedSandboxIngress
from .twilio_whatsapp import (
    TwilioChannelContext,
    TwilioInboundMessage,
    TwilioWhatsAppInboundAdapter,
)

TEST_TENANT = "910001"
TEST_MERCHANT = "4b196022-65a1-548c-b122-f1f9e4b78bea"
MAX_MESSAGE_AGE = timedelta(minutes=5)


class TrackAMenuNotAdmitted(PermissionError):
    """Signed Track A message cannot be bound to its authorized TEST session."""


class TrackAServerSessionPort(Protocol):
    def load_for_provider_event(
        self, *, provider_event_digest: str, sender: str, recipient: str,
        now: datetime
    ) -> CustomerSessionSnapshot | None: ...


class TrackAReceiptPort(TrustedProcessedReceiptPort, Protocol):
    """Actual PG-backed processed-first receipt authority."""


class _BoundSessionView:
    """Adapter that only returns the already source-read exact session."""

    def __init__(
        self, event_id: str, correlation_ref: str,
        session: CustomerSessionSnapshot
    ) -> None:
        self.event_id = event_id
        self.correlation_ref = correlation_ref
        self.session = session

    def load_for_verified_event(
        self, *, channel_event_id: str, correlation_ref: str
    ) -> CustomerSessionSnapshot | None:
        if (
            channel_event_id != self.event_id
            or correlation_ref != self.correlation_ref
        ):
            return None
        return self.session


def prepare_track_a_menu(
    *, enabled: bool, admitted_reader: bool, signed: VerifiedSandboxIngress,
    message: TwilioInboundMessage, settings: SandboxIngressConfig,
    sessions: TrackAServerSessionPort, receipts: TrackAReceiptPort,
    catalog: RealXBOSCatalogAdapter, trusted_location_ref: str,
    now: datetime,
) -> SWESMenuReply:
    """Build the real menu reply from source-owned session + bound private catalog.

    This function does not create a session, dispatch commerce or send messages.
    No runtime route is registered until independently qualified hosted
    reader credentials, persistent session and rollback evidence exist.
    """
    if not enabled or not admitted_reader:
        raise TrackAMenuNotAdmitted("track_a_menu_disabled_or_reader_unadmitted")
    if (
        not isinstance(signed, VerifiedSandboxIngress)
        or signed.disposition != "accepted"
        or signed.action != "receipt_only"
        or signed.merchant_dispatch_enabled
        or not isinstance(message, TwilioInboundMessage)
        or message.body.strip().casefold() != "menu"
        or message.account_sid != settings.provider.account_sid
        or not hmac.compare_digest(message.sender, settings.allowed_tester)
        or not hmac.compare_digest(message.recipient, settings.provider.sender)
    ):
        raise TrackAMenuNotAdmitted("signed_first_sandbox_menu_required")
    if (
        now.tzinfo is None or now.utcoffset() is None
        or message.occurred_at > now
        or now - message.occurred_at > MAX_MESSAGE_AGE
        or now.astimezone(UTC) >= settings.tester_expires_utc
    ):
        raise TrackAMenuNotAdmitted("sandbox_or_session_clock_invalid")
    receipt_key = hashlib.sha256(
        (message.provider_message_ref + "\x00" + message.recipient + "\x00"
         + settings.provider.account_sid).encode("utf-8")
    ).hexdigest()
    if (
        not isinstance(signed.receipt_key, str)
        or not hmac.compare_digest(receipt_key, signed.receipt_key)
        or not hmac.compare_digest(receipt_key[:20], signed.event_digest)
    ):
        raise TrackAMenuNotAdmitted("signed_provider_receipt_mismatch")
    try:
        session = sessions.load_for_provider_event(
            provider_event_digest=signed.event_digest,
            sender=message.normalized_sender,
            recipient=message.normalized_recipient, now=now,
        )
    except Exception as exc:
        raise TrackAMenuNotAdmitted("trusted_persistent_session_unavailable") from exc
    if (
        not isinstance(session, CustomerSessionSnapshot)
        or not session.security_binding_complete
        or session.tenant_ref != TEST_TENANT
        or session.merchant_ref != TEST_MERCHANT
        or not trusted_location_ref
        or session.location_ref != trusted_location_ref
        or session.state not in {ChannelState.BROWSING, ChannelState.CART}
        or not session.context_binding_ref
        or not session.owner_identity_ref
        or not session.expires_at_epoch
        or now.timestamp() >= session.expires_at_epoch
        or session.invalidated_at_epoch is not None
        or session.rotated_to_session_ref is not None
    ):
        raise TrackAMenuNotAdmitted("tenant_bound_active_session_unavailable")
    context = TwilioChannelContext(
        conversation_ref=session.conversation_ref,
        session_ref=session.session_ref,
        merchant_ref=session.merchant_ref,
        subject_ref=session.owner_identity_ref,
        correlation_ref=session.correlation_ref,
        trusted_session=True,
    )
    event: ChannelInboundEvent = TwilioWhatsAppInboundAdapter.to_channel_event(
        message=message, context=context
    )
    gate = SWESWNDMenuReadGate(
        enabled=True, merchant_admitted=True, service_reader_admitted=True,
        tenant_ref=TEST_TENANT, merchant_ref=TEST_MERCHANT,
        location_ref=trusted_location_ref,
    )
    try:
        return prepare_trusted_wnd_menu_reply(
            enabled=True, gate=gate, inbound=event, receipts=receipts,
            sessions=_BoundSessionView(
                event.channel_event_id, session.correlation_ref, session
            ),
            catalog=catalog, now=now,
        )
    except SWESWNDMenuNotAdmitted as exc:
        raise TrackAMenuNotAdmitted("authenticated_menu_not_available") from exc

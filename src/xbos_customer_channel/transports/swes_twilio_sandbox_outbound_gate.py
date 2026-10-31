"""Default-off, TEST-only admission for future provider REST status replies.

This file is not composed into the live runtime. Verified payment/order status
must come from an injected authenticated evidence verifier, never an inbound
WhatsApp form, generic flag, or customer-supplied payment claim.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from ..channel_contract import ChannelOutboundIntent
from .twilio_whatsapp import (
    TwilioSendResult, TwilioWhatsAppConfig, TwilioWhatsAppOutboundAdapter,
)


class SWESSandboxOutboundNotAdmitted(PermissionError):
    pass


class TrustedStatusEvidenceVerifier(Protocol):
    """Inject a gateway/WND-owned status authority, not a user-controlled bool."""
    def verify(self, *, intent: ChannelOutboundIntent) -> bool: ...


@dataclass(frozen=True, slots=True)
class SWESSandboxOutboundPolicy:
    enabled: bool
    nonfinancial_only: bool
    tester_consented: bool
    allowed_tester: str
    approved_sender: str
    tester_join_expires_utc: datetime

    def __post_init__(self) -> None:
        if (
            not self.allowed_tester.startswith("whatsapp:+")
            or not self.approved_sender.startswith("whatsapp:+")
            or self.tester_join_expires_utc.tzinfo is None
            or self.tester_join_expires_utc.utcoffset() is None
        ):
            raise SWESSandboxOutboundNotAdmitted("bounded_sandbox_identity_required")


class SWESSandboxStatusSender:
    """No send without exact enrolled tester and independently verified status."""

    def __init__(
        self, *,
        config: TwilioWhatsAppConfig,
        policy: SWESSandboxOutboundPolicy,
        evidence: TrustedStatusEvidenceVerifier,
        transport: TwilioWhatsAppOutboundAdapter,
    ) -> None:
        self._config = config
        self._policy = policy
        self._evidence = evidence
        self._transport = transport

    def authorize(self, *, intent: ChannelOutboundIntent, now: datetime) -> None:
        """Fail closed before reserving an outbound delivery or calling a provider."""
        policy = self._policy
        if not policy.enabled or not policy.nonfinancial_only or not policy.tester_consented:
            raise SWESSandboxOutboundNotAdmitted("sandbox_status_sender_disabled")
        if now.tzinfo is None or now.utcoffset() is None:
            raise SWESSandboxOutboundNotAdmitted("aware_clock_required")
        if now.astimezone(timezone.utc) >= policy.tester_join_expires_utc.astimezone(timezone.utc):
            raise SWESSandboxOutboundNotAdmitted("sandbox_tester_join_expired")
        if (
            self._config.sender != policy.approved_sender
            or intent.channel != "whatsapp"
            or intent.recipient != policy.allowed_tester
            or intent.purpose != "sandbox_status_confirmation"
            or intent.event_type not in {"wnd_order_status", "gateway_payment_status"}
        ):
            raise SWESSandboxOutboundNotAdmitted("outbound_sender_recipient_or_purpose_not_admitted")
        if not self._evidence.verify(intent=intent):
            raise SWESSandboxOutboundNotAdmitted("authoritative_status_evidence_required")

    def send(self, *, intent: ChannelOutboundIntent, now: datetime) -> TwilioSendResult:
        self.authorize(intent=intent, now=now)
        return self._transport.send(intent)

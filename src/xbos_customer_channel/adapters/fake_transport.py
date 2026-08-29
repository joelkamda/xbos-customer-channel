from __future__ import annotations

from ..models import OutboundMessage


class FakeWhatsAppTransport:
    """Transport fixture only. No WhatsApp provider SDK or provider-specific semantics."""

    def __init__(self) -> None:
        self.sent: dict[str, OutboundMessage] = {}
        self.acks: set[str] = set()

    def send_message(self, customer_ref: str, body: str, idempotency_key: str) -> OutboundMessage:
        if idempotency_key not in self.sent:
            self.sent[idempotency_key] = OutboundMessage(
                f"message:fixture:{len(self.sent)+1}", "whatsapp_fake", body
            )
        return self.sent[idempotency_key]

    def receive_event(self, event_ref: str) -> dict[str, str]:
        return {"event_ref": event_ref, "channel": "whatsapp_fake"}

    def acknowledge_event(self, event_ref: str) -> None:
        self.acks.add(event_ref)

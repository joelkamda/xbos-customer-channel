"""Small, injectable Meta WhatsApp Cloud API boundary.

The values received from Meta identify a provider delivery target only.  They
are deliberately represented as untrusted locators and are never converted to
channel, customer, or XCIA identity authority in this module.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class InboundRequestRejected(ValueError):
    """A request rejected before it can become a trusted channel event."""


class MissingWebhookSignature(InboundRequestRejected):
    pass


class InvalidWebhookSignature(InboundRequestRejected):
    pass


class ProviderPayloadRejected(InboundRequestRejected):
    pass


@dataclass(frozen=True, slots=True)
class MetaWhatsAppConfig:
    graph_api_version: str
    phone_number_id: str
    app_secret: str = field(repr=False)
    verification_token: str = field(repr=False)
    access_token: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.graph_api_version.startswith("v"):
            raise ValueError("graph_api_version_required")
        if not all((self.phone_number_id, self.app_secret, self.verification_token, self.access_token)):
            raise ValueError("meta_whatsapp_secret_configuration_required")

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> MetaWhatsAppConfig:
        source = os.environ if environ is None else environ
        try:
            return cls(
                graph_api_version=source["META_WHATSAPP_GRAPH_API_VERSION"],
                phone_number_id=source["META_WHATSAPP_PHONE_NUMBER_ID"],
                app_secret=source["META_WHATSAPP_APP_SECRET"],
                verification_token=source["META_WHATSAPP_VERIFY_TOKEN"],
                access_token=source["META_WHATSAPP_ACCESS_TOKEN"],
            )
        except KeyError as error:
            raise ValueError(f"missing_meta_whatsapp_configuration:{error.args[0]}") from None


@dataclass(frozen=True, slots=True)
class UntrustedProviderUserRef:
    """Raw WhatsApp sender/recipient locator; explicitly not an XCIA subject."""

    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ProviderPayloadRejected("provider_user_ref_missing")


class InteractionKind(StrEnum):
    BUTTON_REPLY = "button_reply"
    LIST_REPLY = "list_reply"


@dataclass(frozen=True, slots=True)
class NormalizedInteractiveReply:
    kind: InteractionKind
    reply_id: str
    title: str


@dataclass(frozen=True, slots=True)
class NormalizedInboundMessage:
    provider_message_ref: str
    sender: UntrustedProviderUserRef
    occurred_at_epoch: int
    text: str | None
    interactive_reply: NormalizedInteractiveReply | None
    context_provider_message_ref: str | None
    metadata_phone_number_id: str


class DeliveryState(StrEnum):
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class NormalizedDeliveryStatus:
    provider_message_ref: str
    state: DeliveryState
    occurred_at_epoch: int
    recipient: UntrustedProviderUserRef | None
    error_code: int | None = None


@dataclass(frozen=True, slots=True)
class NormalizedProviderCallback:
    messages: tuple[NormalizedInboundMessage, ...]
    delivery_statuses: tuple[NormalizedDeliveryStatus, ...]


def verify_webhook_subscription(
    *, config: MetaWhatsAppConfig, mode: str | None, verify_token: str | None, challenge: str | None
) -> str | None:
    """Return the provider challenge only for a constant-time verified subscription request."""
    if mode != "subscribe" or verify_token is None or challenge is None:
        return None
    if not hmac.compare_digest(config.verification_token, verify_token):
        return None
    return challenge


def verify_webhook_signature(*, config: MetaWhatsAppConfig, raw_body: bytes, signature: str | None) -> None:
    """Verify Meta's SHA-256 signature over the exact, undecoded request body."""
    if signature is None:
        raise MissingWebhookSignature("missing_x_hub_signature_256")
    prefix = "sha256="
    if not signature.startswith(prefix):
        raise InvalidWebhookSignature("invalid_x_hub_signature_256")
    supplied = signature[len(prefix) :]
    expected = hmac.new(config.app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, supplied):
        raise InvalidWebhookSignature("invalid_x_hub_signature_256")


class MetaWhatsAppInboundAdapter:
    """Verification-first adapter; payload parsing never runs before signature validation."""

    def __init__(self, config: MetaWhatsAppConfig) -> None:
        self._config = config

    def verify_subscription(self, *, mode: str | None, verify_token: str | None, challenge: str | None) -> str | None:
        return verify_webhook_subscription(
            config=self._config, mode=mode, verify_token=verify_token, challenge=challenge
        )

    def receive(self, *, raw_body: bytes, signature: str | None) -> NormalizedProviderCallback:
        verify_webhook_signature(config=self._config, raw_body=raw_body, signature=signature)
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ProviderPayloadRejected("malformed_provider_payload") from None
        return normalize_provider_callback(payload)


def normalize_provider_callback(payload: object) -> NormalizedProviderCallback:
    """Normalize only documented text, interactive replies, and delivery status callbacks."""
    root = _mapping(payload, "provider_payload_not_object")
    if root.get("object") != "whatsapp_business_account":
        raise ProviderPayloadRejected("unsupported_provider_object")
    entries = _sequence(root.get("entry"), "provider_entry_missing")
    messages: list[NormalizedInboundMessage] = []
    statuses: list[NormalizedDeliveryStatus] = []
    for entry in entries:
        changes = _sequence(_mapping(entry, "provider_entry_invalid").get("changes"), "provider_changes_missing")
        for change in changes:
            change_map = _mapping(change, "provider_change_invalid")
            if change_map.get("field") != "messages":
                raise ProviderPayloadRejected("unsupported_webhook_field")
            value = _mapping(change_map.get("value"), "provider_message_value_missing")
            metadata = _mapping(value.get("metadata"), "provider_metadata_missing")
            phone_number_id = _text(metadata.get("phone_number_id"), "provider_phone_number_id_missing")
            for message in _optional_sequence(value.get("messages"), "provider_messages_invalid"):
                messages.append(_normalize_message(_mapping(message, "provider_message_invalid"), phone_number_id))
            for status in _optional_sequence(value.get("statuses"), "provider_statuses_invalid"):
                statuses.append(_normalize_status(_mapping(status, "provider_status_invalid")))
    if not messages and not statuses:
        raise ProviderPayloadRejected("provider_callback_empty")
    return NormalizedProviderCallback(tuple(messages), tuple(statuses))


def _normalize_message(message: Mapping[str, object], phone_number_id: str) -> NormalizedInboundMessage:
    provider_message_ref = _text(message.get("id"), "provider_message_id_missing")
    sender = UntrustedProviderUserRef(_text(message.get("from"), "provider_sender_missing"))
    occurred_at_epoch = _epoch(message.get("timestamp"), "provider_message_timestamp_invalid")
    message_type = _text(message.get("type"), "provider_message_type_missing")
    context_ref: str | None = None
    if "context" in message:
        context_ref = _text(_mapping(message["context"], "provider_context_invalid").get("id"), "provider_context_id_missing")
    if message_type == "text":
        text = _text(_mapping(message.get("text"), "provider_text_invalid").get("body"), "provider_text_missing")
        return NormalizedInboundMessage(provider_message_ref, sender, occurred_at_epoch, text, None, context_ref, phone_number_id)
    if message_type != "interactive":
        raise ProviderPayloadRejected("unsupported_message_type")
    interactive = _mapping(message.get("interactive"), "provider_interactive_missing")
    kind_value = _text(interactive.get("type"), "provider_interactive_type_missing")
    try:
        kind = InteractionKind(kind_value)
    except ValueError:
        raise ProviderPayloadRejected("unsupported_interactive_type") from None
    reply = _mapping(interactive.get(kind.value), "provider_interactive_reply_missing")
    normalized = NormalizedInteractiveReply(
        kind=kind,
        reply_id=_text(reply.get("id"), "provider_interactive_reply_id_missing"),
        title=_text(reply.get("title"), "provider_interactive_reply_title_missing"),
    )
    return NormalizedInboundMessage(provider_message_ref, sender, occurred_at_epoch, None, normalized, context_ref, phone_number_id)


def _normalize_status(status: Mapping[str, object]) -> NormalizedDeliveryStatus:
    provider_message_ref = _text(status.get("id"), "provider_status_id_missing")
    try:
        state = DeliveryState(_text(status.get("status"), "provider_status_missing"))
    except ValueError:
        raise ProviderPayloadRejected("unsupported_delivery_status") from None
    recipient_raw = status.get("recipient_id")
    recipient = None if recipient_raw is None else UntrustedProviderUserRef(_text(recipient_raw, "provider_recipient_invalid"))
    error_code = None
    errors = status.get("errors")
    if errors is not None:
        first = _sequence(errors, "provider_status_errors_invalid")[0]
        code = _mapping(first, "provider_status_error_invalid").get("code")
        if isinstance(code, int):
            error_code = code
    return NormalizedDeliveryStatus(
        provider_message_ref=provider_message_ref,
        state=state,
        occurred_at_epoch=_epoch(status.get("timestamp"), "provider_status_timestamp_invalid"),
        recipient=recipient,
        error_code=error_code,
    )


def _mapping(value: object, reason: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ProviderPayloadRejected(reason)
    return value


def _sequence(value: object, reason: str) -> Sequence[object]:
    if not isinstance(value, list) or not value:
        raise ProviderPayloadRejected(reason)
    return value


def _optional_sequence(value: object, reason: str) -> Sequence[object]:
    if value is None:
        return ()
    return _sequence(value, reason)


def _text(value: object, reason: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProviderPayloadRejected(reason)
    return value


def _epoch(value: object, reason: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    raise ProviderPayloadRejected(reason)


@dataclass(frozen=True, slots=True)
class MetaHttpResponse:
    status_code: int
    body: bytes


class MetaHttpClient(Protocol):
    def post(self, *, url: str, headers: Mapping[str, str], body: bytes, timeout_seconds: float) -> MetaHttpResponse: ...


class ProviderErrorKind(StrEnum):
    RATE_LIMITED = "rate_limited"
    TRANSIENT = "transient"
    PERMANENT = "permanent"


class MetaProviderError(RuntimeError):
    def __init__(self, *, kind: ProviderErrorKind, status_code: int, provider_code: int | None) -> None:
        self.kind = kind
        self.status_code = status_code
        self.provider_code = provider_code
        super().__init__(f"meta_provider_error:{kind.value}:{status_code}")


class MetaRateLimitError(MetaProviderError):
    pass


@dataclass(frozen=True, slots=True)
class InteractiveButton:
    reply_id: str
    title: str


@dataclass(frozen=True, slots=True)
class InteractiveListRow:
    reply_id: str
    title: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class InteractiveListSection:
    title: str
    rows: tuple[InteractiveListRow, ...]


@dataclass(frozen=True, slots=True)
class ProviderOutboundMessage:
    provider_message_ref: str


class MetaWhatsAppOutboundAdapter:
    """Maps channel-rendered messages to the Cloud API through an injected HTTP client."""

    def __init__(self, *, config: MetaWhatsAppConfig, http_client: MetaHttpClient, timeout_seconds: float = 10.0) -> None:
        self._config = config
        self._http_client = http_client
        self._timeout_seconds = timeout_seconds

    def send_text(self, *, recipient: UntrustedProviderUserRef, body: str) -> ProviderOutboundMessage:
        return self._send(recipient=recipient, payload={"type": "text", "text": {"body": _outbound_text(body)}})

    def send_buttons(
        self, *, recipient: UntrustedProviderUserRef, body: str, buttons: Sequence[InteractiveButton]
    ) -> ProviderOutboundMessage:
        if not 1 <= len(buttons) <= 3:
            raise ValueError("meta_button_count_invalid")
        actions = [
            {"type": "reply", "reply": {"id": _outbound_text(button.reply_id), "title": _outbound_text(button.title)}}
            for button in buttons
        ]
        return self._send(
            recipient=recipient,
            payload={"type": "interactive", "interactive": {"type": "button", "body": {"text": _outbound_text(body)}, "action": {"buttons": actions}}},
        )

    def send_list(
        self, *, recipient: UntrustedProviderUserRef, body: str, button_label: str, sections: Sequence[InteractiveListSection]
    ) -> ProviderOutboundMessage:
        if not sections or any(not section.rows for section in sections):
            raise ValueError("meta_list_sections_invalid")
        rendered_sections = [
            {
                "title": _outbound_text(section.title),
                "rows": [
                    {"id": _outbound_text(row.reply_id), "title": _outbound_text(row.title), **({"description": row.description} if row.description else {})}
                    for row in section.rows
                ],
            }
            for section in sections
        ]
        return self._send(
            recipient=recipient,
            payload={"type": "interactive", "interactive": {"type": "list", "body": {"text": _outbound_text(body)}, "action": {"button": _outbound_text(button_label), "sections": rendered_sections}}},
        )

    def _send(self, *, recipient: UntrustedProviderUserRef, payload: Mapping[str, object]) -> ProviderOutboundMessage:
        body = json.dumps({"messaging_product": "whatsapp", "to": recipient.value, **payload}, separators=(",", ":")).encode("utf-8")
        response = self._http_client.post(
            url=f"https://graph.facebook.com/{self._config.graph_api_version}/{self._config.phone_number_id}/messages",
            headers={"Authorization": f"Bearer {self._config.access_token}", "Content-Type": "application/json"},
            body=body,
            timeout_seconds=self._timeout_seconds,
        )
        parsed = _response_object(response.body)
        if not 200 <= response.status_code < 300:
            raise _provider_error(response.status_code, parsed)
        messages = parsed.get("messages")
        if not isinstance(messages, list) or not messages or not isinstance(messages[0], dict):
            raise MetaProviderError(kind=ProviderErrorKind.TRANSIENT, status_code=response.status_code, provider_code=None)
        provider_message_ref = messages[0].get("id")
        if not isinstance(provider_message_ref, str) or not provider_message_ref:
            raise MetaProviderError(kind=ProviderErrorKind.TRANSIENT, status_code=response.status_code, provider_code=None)
        return ProviderOutboundMessage(provider_message_ref)


def _response_object(body: bytes) -> Mapping[str, object]:
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _provider_error(status_code: int, payload: Mapping[str, object]) -> MetaProviderError:
    error = payload.get("error")
    code = _mapping(error, "provider_error_invalid").get("code") if isinstance(error, dict) else None
    provider_code = code if isinstance(code, int) else None
    if status_code == 429 or provider_code in {4, 80007, 130429}:
        return MetaRateLimitError(kind=ProviderErrorKind.RATE_LIMITED, status_code=status_code, provider_code=provider_code)
    kind = ProviderErrorKind.TRANSIENT if status_code >= 500 else ProviderErrorKind.PERMANENT
    return MetaProviderError(kind=kind, status_code=status_code, provider_code=provider_code)


def _outbound_text(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ValueError("meta_outbound_text_invalid")
    return value

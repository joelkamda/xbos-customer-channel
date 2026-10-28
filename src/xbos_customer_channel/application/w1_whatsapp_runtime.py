"""Provider-neutral W1 runtime state contract.

Provider-specific parsing and sending live under transports/. This module keeps
only the conversation state object and the state-port protocol used by durable
Customer Channel persistence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..adapters.wnd_merchant_http import WNDMerchantBinding
from ..channel_contract import ChannelCommand, ChannelCommandType, ChannelInboundEvent
from ..order import CanonicalOrderProjection
from ..session_state import CustomerSessionSnapshot
from .command_validation import CommandValidator
from .catalog_service import CatalogSession
from .w1_checkout_ux import W1CheckoutState
from .w1_conversation import W1NavigationCursor


@dataclass(frozen=True, slots=True)
class W1RuntimeConversationState:
    session: CustomerSessionSnapshot
    catalog_session: CatalogSession
    navigation: W1NavigationCursor = W1NavigationCursor()
    checkout: W1CheckoutState | None = None


class W1RuntimeStatePort(Protocol):
    """Durable state resolution independent of any provider object contract."""

    def load(self, inbound: object) -> W1RuntimeConversationState: ...

    def save(
        self,
        inbound: object,
        state: W1RuntimeConversationState,
    ) -> None: ...


class WNDMerchantCheckoutNotAdmitted(PermissionError):
    pass


class WNDMerchantConfirmedOrderService(Protocol):
    def submit(
        self, *, session_ref: str, confirmation_handle_ref: str,
        client_submit_ref: str, now_epoch: int,
    ) -> CanonicalOrderProjection: ...


class W1WNDMerchantOrderDispatch:
    """Default-OFF, WND-authenticated checkout service handoff.

    This is deliberately NOT registered in the live WhatsApp runtime. The
    existing OrderDraftConfirmationService independently revalidates a
    server-issued confirmation, session provenance and upstream WND order.
    """

    def __init__(
        self, *,
        enabled: bool = False,
        merchant_admitted: bool = False,
        binding: WNDMerchantBinding,
        expected_tenant_ref: str,
        order_service: WNDMerchantConfirmedOrderService,
    ) -> None:
        self._enabled = enabled
        self._admitted = merchant_admitted
        self._binding = binding
        if not expected_tenant_ref.strip():
            raise WNDMerchantCheckoutNotAdmitted("tenant_attestation_required")
        self._tenant_ref = expected_tenant_ref
        self._order_service = order_service
        self._validator = CommandValidator(real_xbos_write_available=True)

    def submit_confirmed(
        self, *, event: ChannelInboundEvent,
        command: ChannelCommand,
        session: CustomerSessionSnapshot,
        now_epoch: int,
    ) -> CanonicalOrderProjection:
        if not self._enabled:
            raise WNDMerchantCheckoutNotAdmitted("wnd_whatsapp_merchant_feature_disabled")
        if not self._admitted:
            raise WNDMerchantCheckoutNotAdmitted("wnd_merchant_identity_not_verified")
        if event.channel != "whatsapp" or command.command_type is not ChannelCommandType.SUBMIT_ORDER:
            raise WNDMerchantCheckoutNotAdmitted("only_authenticated_whatsapp_order_submit")
        if (
            session.tenant_ref != self._tenant_ref
            or session.merchant_ref != self._binding.merchant_public_id
            or session.location_ref != self._binding.location_public_id
            or session.context_binding_ref != self._binding.context_binding_public_id
            or not session.security_binding_complete
        ):
            raise WNDMerchantCheckoutNotAdmitted("wnd_session_merchant_context_not_admitted")
        verdict = self._validator.validate(
            event=event, command=command, session=session,
            now_epoch=now_epoch,
        )
        if not verdict.may_dispatch:
            raise WNDMerchantCheckoutNotAdmitted("wnd_command_not_dispatchable:" + verdict.reason)
        if set(command.payload) != {"confirmation_ref", "client_submit_ref"}:
            raise WNDMerchantCheckoutNotAdmitted("server_confirmation_submit_payload_required")
        handle = command.payload["confirmation_ref"]
        supplied_key = command.payload["client_submit_ref"]
        # A server-provided confirmation handle must be bound to the same
        # deterministic command. Never use Twilio provider ID as an order ID.
        expected_key = "cc-submit:" + event.channel_event_id
        if (
            not isinstance(handle, str) or not handle.strip()
            or not isinstance(supplied_key, str) or supplied_key != expected_key
        ):
            raise WNDMerchantCheckoutNotAdmitted("server_confirmed_submit_key_mismatch")
        return self._order_service.submit(
            session_ref=session.session_ref,
            confirmation_handle_ref=handle,
            client_submit_ref=expected_key,
            now_epoch=now_epoch,
        )

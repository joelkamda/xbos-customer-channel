from __future__ import annotations

from dataclasses import dataclass

from ..channel_contract import (
    ChannelCommand,
    ChannelCommandType,
    ChannelInboundEvent,
    CommandValidationResult,
    ControlOwner,
    MUTATING_COMMANDS,
)
from ..session_state import ChannelState, CustomerSessionSnapshot


_ALLOWED_STATES: dict[ChannelCommandType, frozenset[ChannelState]] = {
    ChannelCommandType.SHOW_CATALOG: frozenset(
        {
            ChannelState.MERCHANT_CONTEXT,
            ChannelState.BROWSING,
            ChannelState.CART,
            ChannelState.SERVICE_MODE,
            ChannelState.REVIEW,
        }
    ),
    ChannelCommandType.SHOW_ITEM: frozenset(
        {ChannelState.BROWSING, ChannelState.CART}
    ),
    ChannelCommandType.ADD_LINE: frozenset(
        {ChannelState.BROWSING, ChannelState.CART}
    ),
    ChannelCommandType.REMOVE_LINE: frozenset({ChannelState.CART}),
    ChannelCommandType.SET_QUANTITY: frozenset({ChannelState.CART}),
    ChannelCommandType.SET_FULFILLMENT: frozenset(
        {ChannelState.CART, ChannelState.SERVICE_MODE}
    ),
    ChannelCommandType.SHOW_CART: frozenset(
        {
            ChannelState.BROWSING,
            ChannelState.CART,
            ChannelState.SERVICE_MODE,
            ChannelState.REVIEW,
        }
    ),
    ChannelCommandType.SUBMIT_ORDER: frozenset(
        {ChannelState.REVIEW, ChannelState.ORDER_SUBMITTING}
    ),
    ChannelCommandType.GET_ORDER_STATUS: frozenset(
        {
            ChannelState.ORDER_CREATED,
            ChannelState.PAYMENT_METHOD,
            ChannelState.PAYMENT_PENDING,
            ChannelState.PAID,
            ChannelState.FULFILLMENT,
            ChannelState.COMPLETED,
        }
    ),
    ChannelCommandType.CANCEL_DRAFT: frozenset(
        {
            ChannelState.BROWSING,
            ChannelState.CART,
            ChannelState.SERVICE_MODE,
            ChannelState.REVIEW,
        }
    ),
    ChannelCommandType.REQUEST_HUMAN: frozenset(
        state
        for state in ChannelState
        if state not in {ChannelState.COMPLETED, ChannelState.CANCELED}
    ),
}


def control_owner_for_session(session: CustomerSessionSnapshot) -> ControlOwner:
    if session.state is ChannelState.HUMAN_HANDOFF:
        return ControlOwner.HUMAN
    return ControlOwner.AUTOMATION


@dataclass(frozen=True, slots=True)
class CommandValidator:
    """Deterministic authorization boundary between interpretation and domain dispatch."""

    real_xbos_write_available: bool = False

    def validate(
        self,
        *,
        event: ChannelInboundEvent,
        command: ChannelCommand,
        session: CustomerSessionSnapshot,
        now_epoch: int,
        control_owner: ControlOwner | None = None,
    ) -> CommandValidationResult:
        owner = control_owner or control_owner_for_session(session)

        failure = self._schema_and_binding_failure(
            event=event,
            command=command,
            session=session,
            now_epoch=now_epoch,
        )
        if failure is not None:
            return CommandValidationResult(
                command=command,
                valid=False,
                dispatchable=False,
                reason=failure,
                control_owner=owner,
            )

        allowed = _ALLOWED_STATES[command.command_type]
        if session.state not in allowed:
            return CommandValidationResult(
                command=command,
                valid=False,
                dispatchable=False,
                reason=f"command_not_allowed_in_state:{session.state.value}",
                control_owner=owner,
            )

        if owner is ControlOwner.HUMAN and command.command_type in MUTATING_COMMANDS:
            return CommandValidationResult(
                command=command,
                valid=False,
                dispatchable=False,
                reason="human_control_blocks_automated_mutation",
                control_owner=owner,
            )

        if command.mutating:
            expected = f"channel-command:{event.channel_event_id}"
            if command.idempotency_key != expected:
                return CommandValidationResult(
                    command=command,
                    valid=False,
                    dispatchable=False,
                    reason="mutating_command_idempotency_identity_invalid",
                    control_owner=owner,
                )

        if command.requires_real_xbos_write and not self.real_xbos_write_available:
            return CommandValidationResult(
                command=command,
                valid=True,
                dispatchable=False,
                reason="real_xbos_write_adapter_unavailable",
                control_owner=owner,
            )

        return CommandValidationResult(
            command=command,
            valid=True,
            dispatchable=True,
            reason="validated",
            control_owner=owner,
        )

    @staticmethod
    def _schema_and_binding_failure(
        *,
        event: ChannelInboundEvent,
        command: ChannelCommand,
        session: CustomerSessionSnapshot,
        now_epoch: int,
    ) -> str | None:
        if command.channel_event_id != event.channel_event_id:
            return "channel_event_identity_mismatch"
        if command.conversation_id != event.conversation_ref:
            return "event_command_conversation_mismatch"
        if command.session_ref != event.session_ref:
            return "event_command_session_mismatch"
        if command.merchant_ref != event.merchant_ref:
            return "event_command_merchant_mismatch"
        if command.subject_ref != event.subject_ref:
            return "event_command_subject_mismatch"
        if command.correlation_ref != event.correlation_ref:
            return "event_command_correlation_mismatch"

        if session.session_ref != command.session_ref:
            return "active_session_reference_mismatch"
        if session.conversation_ref != command.conversation_id:
            return "active_session_conversation_mismatch"
        if not session.security_binding_complete:
            return "secure_session_binding_required"
        if session.invalidated_at_epoch is not None or session.rotated_to_session_ref is not None:
            return "session_stale_or_rotated"
        if session.expires_at_epoch is not None and now_epoch >= session.expires_at_epoch:
            return "session_expired"

        if session.owner_identity_ref != command.subject_ref:
            return "channel_subject_mismatch"
        if session.merchant_ref != command.merchant_ref:
            return "merchant_binding_mismatch"
        if not session.location_ref:
            return "location_binding_required"
        if not session.context_binding_ref:
            return "context_binding_required"

        return None

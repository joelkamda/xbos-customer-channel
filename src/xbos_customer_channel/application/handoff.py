from __future__ import annotations

from ..channel_contract import (
    ControlOwner,
    HandoffControlState,
    HandoffPhase,
)


def request_handoff(handoff_ref: str) -> HandoffControlState:
    """Request handoff and immediately stop automated commerce mutation."""
    return HandoffControlState(
        phase=HandoffPhase.HANDOFF_REQUESTED,
        control_owner=ControlOwner.HUMAN,
        handoff_ref=handoff_ref,
    )


def activate_human(state: HandoffControlState) -> HandoffControlState:
    if state.phase is not HandoffPhase.HANDOFF_REQUESTED:
        raise ValueError("handoff_activation_requires_requested")
    return HandoffControlState(
        phase=HandoffPhase.HUMAN_ACTIVE,
        control_owner=ControlOwner.HUMAN,
        handoff_ref=state.handoff_ref,
    )


def resume_automation(state: HandoffControlState) -> HandoffControlState:
    if state.phase not in {
        HandoffPhase.HANDOFF_REQUESTED,
        HandoffPhase.HUMAN_ACTIVE,
    }:
        raise ValueError("handoff_resume_requires_human_control")
    return HandoffControlState(
        phase=HandoffPhase.AUTOMATED_RESUME,
        control_owner=ControlOwner.AUTOMATION,
        handoff_ref=state.handoff_ref,
    )

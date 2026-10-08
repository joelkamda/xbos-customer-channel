"""Provider-neutral W1 runtime state contract.

Provider-specific parsing and sending live under transports/. This module keeps
only the conversation state object and the state-port protocol used by durable
Customer Channel persistence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..session_state import CustomerSessionSnapshot
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

from __future__ import annotations

from ..session_state import CustomerSessionSnapshot, UpstreamStateProjection


class FakeXBOSStateReconciliationClient:
    """Deterministic typed XC5 fixture. It does not represent a live XBOS adapter."""

    def __init__(self) -> None:
        self._by_correlation: dict[str, UpstreamStateProjection] = {}

    def register(self, projection: UpstreamStateProjection) -> None:
        self._by_correlation[projection.correlation_ref] = projection

    def reconcile_session(self, session: CustomerSessionSnapshot) -> UpstreamStateProjection | None:
        return self._by_correlation.get(session.correlation_ref)

from __future__ import annotations

from ..provenance import binding_from_session
from ..session_state import CustomerSessionSnapshot, UpstreamStateProjection


class FakeXBOSStateReconciliationClient:
    """Deterministic typed fixture with CR2 session/context binding parity."""

    def __init__(self) -> None:
        self._by_binding: dict[tuple[object, ...], UpstreamStateProjection] = {}

    @staticmethod
    def _key(session: CustomerSessionSnapshot) -> tuple[object, ...]:
        binding = binding_from_session(session)
        return (
            binding.session_ref,
            binding.session_generation,
            binding.owner_identity_ref,
            binding.entry_token_ref,
            binding.tenant_ref,
            binding.merchant_ref,
            binding.location_ref,
            binding.table_ref,
            binding.dining_area_ref,
            binding.context_binding_ref,
            binding.correlation_ref,
        )

    def register(self, session: CustomerSessionSnapshot, projection: UpstreamStateProjection) -> None:
        if projection.correlation_ref != session.correlation_ref:
            raise ValueError("upstream_correlation_mismatch")
        self._by_binding[self._key(session)] = projection

    def reconcile_session(self, session: CustomerSessionSnapshot) -> UpstreamStateProjection | None:
        projection = self._by_binding.get(self._key(session))
        if projection is None:
            return None
        if projection.correlation_ref != session.correlation_ref:
            return None
        return projection

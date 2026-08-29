from __future__ import annotations

from ..models import CustomerIdentity


class FakeCustomerIdentityService:
    def resolve_or_bind_customer(self, channel: str, channel_user_ref: str) -> CustomerIdentity:
        return CustomerIdentity(f"customer:fixture:{channel_user_ref}")

    def record_consent(self, customer_ref: str, purpose: str, idempotency_key: str) -> CustomerIdentity:
        return CustomerIdentity(customer_ref, f"consent:fixture:{purpose}")

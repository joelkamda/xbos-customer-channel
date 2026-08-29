from __future__ import annotations

from ..entry_context import EntryPurpose, MerchantContextProjection


class FakeXBOSContextClient:
    """Typed contract fixture; never durable merchant/location/table authority."""

    def __init__(self) -> None:
        self._contexts: dict[tuple[str, str], MerchantContextProjection] = {
            ("merchant:fixture:alpha", "location:fixture:one"): MerchantContextProjection(
                merchant_ref="merchant:fixture:alpha",
                location_ref="location:fixture:one",
                service_available=True,
                display_name="Fixture Merchant Alpha",
                terminology=(("table", "Table"), ("order", "Order")),
                currency="XAF",
                allowed_fulfillment_modes=("dine_in", "takeaway", "delivery"),
            ),
            ("merchant:fixture:beta", "location:fixture:two"): MerchantContextProjection(
                merchant_ref="merchant:fixture:beta",
                location_ref="location:fixture:two",
                service_available=True,
                display_name="Fixture Merchant Beta",
                terminology=(("table", "Table"), ("order", "Order")),
                currency="XAF",
                allowed_fulfillment_modes=("dine_in", "takeaway"),
            ),
        }

    def resolve_context(
        self,
        *,
        merchant_ref: str,
        location_ref: str,
        table_ref: str | None,
        purpose: EntryPurpose,
    ) -> MerchantContextProjection:
        try:
            projection = self._contexts[(merchant_ref, location_ref)]
        except KeyError:
            raise KeyError("xbos_context_not_found") from None
        if purpose.value not in projection.allowed_fulfillment_modes and purpose is not EntryPurpose.MERCHANT_DISCOVERY:
            raise PermissionError("entry_purpose_not_available")
        if purpose is EntryPurpose.DINE_IN and table_ref is None:
            raise ValueError("table_required_for_dine_in")
        return projection

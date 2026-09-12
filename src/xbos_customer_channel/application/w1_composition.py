"""The real W1 XBOS composition stays unavailable until the ratified API contract exists."""

from __future__ import annotations

from ..ports import XBOSCatalogPort, XBOSContextPort

CUSTOMER_SAFE_MERCHANT_LOCATION_MENU_PRICE_AVAILABILITY_PROJECTION = (
    "CUSTOMER_SAFE_MERCHANT_LOCATION_MENU_PRICE_AVAILABILITY_PROJECTION"
)
REAL_XBOS_ADAPTER_STATE = "WAITING_FOR_XBOS_CONTRACT"


class XBOSW1ContractUnavailable(RuntimeError):
    pass


def require_real_xbos_w1_contract(*, context: XBOSContextPort, catalog: XBOSCatalogPort) -> None:
    """Typed boundary only; do not compose a fake or infer an XBOS HTTP API shape."""
    del context, catalog
    raise XBOSW1ContractUnavailable(REAL_XBOS_ADAPTER_STATE)

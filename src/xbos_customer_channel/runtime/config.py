from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from ..adapters.xbos_private_http import (
    CATALOG_SERVICE_PRINCIPAL,
    CATALOG_SERVICE_SCOPE,
)


XBOS_PRIVATE_BASE_URL_ENV = "XAFPAY_CUSTOMER_CHANNEL_XBOS_PRIVATE_BASE_URL"
XBOS_CATALOG_ADAPTER_ENV = "XAFPAY_CUSTOMER_CHANNEL_XBOS_CATALOG_ADAPTER"
XBOS_CATALOG_READ_TOKEN_ENV = "XAFPAY_CUSTOMER_CHANNEL_XBOS_CATALOG_READ_TOKEN"
XBOS_CATALOG_SERVICE_PRINCIPAL_ENV = "XAFPAY_CUSTOMER_CHANNEL_XBOS_CATALOG_SERVICE_PRINCIPAL"
XBOS_CATALOG_SERVICE_SCOPE_ENV = "XAFPAY_CUSTOMER_CHANNEL_XBOS_CATALOG_SERVICE_SCOPE"
XBOS_CATALOG_BEARER_CREDENTIAL_REFERENCE = f"env:{XBOS_CATALOG_READ_TOKEN_ENV}"


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Provider-independent runtime configuration; secret values never belong in source."""

    xbos_private_base_url: str
    xbos_catalog_adapter: str
    xbos_catalog_service_principal: str
    xbos_catalog_service_scope: str
    xbos_catalog_bearer_credential_reference: str
    xbos_catalog_read_token: str | None = field(default=None, repr=False)

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "RuntimeConfig":
        source = os.environ if environ is None else environ
        try:
            xbos_base_url = source[XBOS_PRIVATE_BASE_URL_ENV].strip()
        except KeyError:
            raise ValueError(
                f"missing_customer_channel_configuration:{XBOS_PRIVATE_BASE_URL_ENV}"
            ) from None

        if not xbos_base_url or not xbos_base_url.startswith(("http://", "https://")):
            raise ValueError("invalid_customer_channel_xbos_private_base_url")

        catalog_adapter = source.get(
            XBOS_CATALOG_ADAPTER_ENV,
            "fake",
        ).strip().lower()
        if catalog_adapter not in {"fake", "real"}:
            raise ValueError("invalid_customer_channel_xbos_catalog_adapter")

        raw_token = source.get(XBOS_CATALOG_READ_TOKEN_ENV)
        catalog_read_token = None if raw_token is None else raw_token.strip() or None

        principal = source.get(
            XBOS_CATALOG_SERVICE_PRINCIPAL_ENV,
            CATALOG_SERVICE_PRINCIPAL,
        ).strip()
        if principal != CATALOG_SERVICE_PRINCIPAL:
            raise ValueError("invalid_customer_channel_xbos_catalog_service_principal")

        scope = source.get(
            XBOS_CATALOG_SERVICE_SCOPE_ENV,
            CATALOG_SERVICE_SCOPE,
        ).strip()
        if scope != CATALOG_SERVICE_SCOPE or len(scope.split()) != 1:
            raise ValueError("invalid_customer_channel_xbos_catalog_service_scope")

        return cls(
            xbos_private_base_url=xbos_base_url.rstrip("/"),
            xbos_catalog_adapter=catalog_adapter,
            xbos_catalog_service_principal=principal,
            xbos_catalog_service_scope=scope,
            xbos_catalog_bearer_credential_reference=XBOS_CATALOG_BEARER_CREDENTIAL_REFERENCE,
            xbos_catalog_read_token=catalog_read_token,
        )

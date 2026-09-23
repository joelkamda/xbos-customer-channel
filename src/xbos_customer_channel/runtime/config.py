from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from ..transports.meta_whatsapp import MetaWhatsAppConfig


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Environment-only runtime configuration; secret values never belong in source."""

    meta: MetaWhatsAppConfig
    xbos_private_base_url: str

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "RuntimeConfig":
        source = os.environ if environ is None else environ
        meta = MetaWhatsAppConfig.from_environment(source)
        try:
            xbos_base_url = source["XAFPAY_CUSTOMER_CHANNEL_XBOS_PRIVATE_BASE_URL"].strip()
        except KeyError:
            raise ValueError(
                "missing_customer_channel_configuration:XAFPAY_CUSTOMER_CHANNEL_XBOS_PRIVATE_BASE_URL"
            ) from None

        if not xbos_base_url or not xbos_base_url.startswith(("http://", "https://")):
            raise ValueError("invalid_customer_channel_xbos_private_base_url")

        return cls(
            meta=meta,
            xbos_private_base_url=xbos_base_url.rstrip("/"),
        )

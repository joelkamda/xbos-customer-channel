from __future__ import annotations

from collections.abc import Mapping

import httpx

from ..transports.meta_whatsapp import MetaHttpResponse


class HttpxMetaHttpClient:
    """Concrete HTTPX transport for the accepted Meta outbound adapter contract."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def post(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> MetaHttpResponse:
        if timeout_seconds <= 0:
            raise ValueError("meta_http_timeout_required")

        if self._client is not None:
            response = self._client.post(
                url,
                headers=dict(headers),
                content=body,
                timeout=timeout_seconds,
            )
        else:
            with httpx.Client() as client:
                response = client.post(
                    url,
                    headers=dict(headers),
                    content=body,
                    timeout=timeout_seconds,
                )

        return MetaHttpResponse(
            status_code=response.status_code,
            body=response.content,
        )

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx

from xbos_customer_channel.runtime.meta_http import HttpxMetaHttpClient
from xbos_customer_channel.transports.meta_whatsapp import (
    MetaWhatsAppConfig,
    MetaWhatsAppOutboundAdapter,
    UntrustedProviderUserRef,
)


class XC9MetaHttpClientTests(unittest.TestCase):
    def test_meta_http_client_builds_accepted_provider_request(self) -> None:
        observed: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            observed["method"] = request.method
            observed["url"] = str(request.url)
            observed["authorization"] = request.headers.get("Authorization")
            observed["content_type"] = request.headers.get("Content-Type")
            observed["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={"messages": [{"id": "wamid.out.runtime"}]},
            )

        transport = httpx.MockTransport(handler)
        with httpx.Client(transport=transport) as client:
            concrete = HttpxMetaHttpClient(client)
            config = MetaWhatsAppConfig(
                "v99.0",
                "phone-id",
                "app-secret",
                "verify-token",
                "access-token",
            )
            adapter = MetaWhatsAppOutboundAdapter(
                config=config,
                http_client=concrete,
                timeout_seconds=4.0,
            )
            result = adapter.send_text(
                recipient=UntrustedProviderUserRef("237600000000"),
                body="hello",
            )

        self.assertEqual(result.provider_message_ref, "wamid.out.runtime")
        self.assertEqual(observed["method"], "POST")
        self.assertEqual(
            observed["url"],
            "https://graph.facebook.com/v99.0/phone-id/messages",
        )
        self.assertEqual(observed["authorization"], "Bearer access-token")
        self.assertEqual(observed["content_type"], "application/json")
        self.assertEqual(
            observed["body"],
            {
                "messaging_product": "whatsapp",
                "to": "237600000000",
                "type": "text",
                "text": {"body": "hello"},
            },
        )

    def test_meta_http_client_normalizes_status_and_body(self) -> None:
        expected = b'{"messages":[{"id":"wamid.normalized"}]}'

        def handler(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(
                202,
                content=expected,
                headers={"Content-Type": "application/json"},
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            concrete = HttpxMetaHttpClient(client)
            response = concrete.post(
                url="https://graph.facebook.com/v99.0/phone-id/messages",
                headers={
                    "Authorization": "Bearer access-token",
                    "Content-Type": "application/json",
                },
                body=b"{}",
                timeout_seconds=3.0,
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.body, expected)

    def test_meta_http_client_requires_positive_timeout(self) -> None:
        concrete = HttpxMetaHttpClient()
        with self.assertRaisesRegex(ValueError, "meta_http_timeout_required"):
            concrete.post(
                url="https://example.invalid",
                headers={},
                body=b"{}",
                timeout_seconds=0,
            )


if __name__ == "__main__":
    unittest.main()

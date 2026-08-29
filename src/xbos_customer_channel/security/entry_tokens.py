from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VerifiedTokenEnvelope:
    version: int
    token_ref: str
    expires_at_epoch: int
    nonce: str


class InvalidEntryToken(ValueError):
    pass


class HmacEntryTokenCodec:
    """Signs only opaque entry references. The signing key is injected and never stored in source."""

    def __init__(self, signing_key: bytes) -> None:
        if len(signing_key) < 32:
            raise ValueError("entry_signing_key_too_short")
        self._key = signing_key

    @staticmethod
    def _message(version: int, token_ref: str, expires_at_epoch: int, nonce: str) -> bytes:
        return f"{version}|{token_ref}|{expires_at_epoch}|{nonce}".encode("utf-8")

    def issue(self, *, version: int, token_ref: str, expires_at_epoch: int, nonce: str) -> str:
        message = self._message(version, token_ref, expires_at_epoch, nonce)
        digest = hmac.new(self._key, message, hashlib.sha256).digest()
        signature = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        return f"xc3v{version}.{token_ref}.{expires_at_epoch}.{nonce}.{signature}"

    def verify(self, token: str, *, now_epoch: int) -> VerifiedTokenEnvelope:
        try:
            prefix, token_ref, expiry_text, nonce, signature = token.split(".", 4)
            if not prefix.startswith("xc3v"):
                raise ValueError
            version = int(prefix[4:])
            expires_at_epoch = int(expiry_text)
        except (ValueError, TypeError):
            raise InvalidEntryToken("invalid_entry_token") from None

        expected = self.issue(
            version=version,
            token_ref=token_ref,
            expires_at_epoch=expires_at_epoch,
            nonce=nonce,
        ).rsplit(".", 1)[1]
        if not hmac.compare_digest(signature, expected):
            raise InvalidEntryToken("invalid_entry_token")
        if now_epoch >= expires_at_epoch:
            raise InvalidEntryToken("expired_entry_token")
        return VerifiedTokenEnvelope(version, token_ref, expires_at_epoch, nonce)

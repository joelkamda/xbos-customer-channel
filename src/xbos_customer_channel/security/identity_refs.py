from __future__ import annotations

import hashlib
import hmac


def hash_channel_user_ref(*, channel: str, channel_user_ref: str, secret: str) -> str:
    """Create a non-reversible durable lookup token without persisting the raw address/phone."""
    if not secret:
        raise ValueError("hash_secret_required")
    material = f"{channel}\x1f{channel_user_ref}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), material, hashlib.sha256).hexdigest()

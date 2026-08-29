from __future__ import annotations

from urllib.parse import urlencode, urlparse

from ..entry_context import EntryTarget


class EntryLinkBuilder:
    """Builds only preconfigured trusted channel entry URLs; callers cannot supply redirects."""

    def __init__(self, trusted_bases: dict[EntryTarget, str]) -> None:
        required = {EntryTarget.WHATSAPP, EntryTarget.CUSTOMER_WEB}
        if set(trusted_bases) != required:
            raise ValueError("both_channel_entry_targets_required")
        self._bases: dict[EntryTarget, str] = {}
        for target, base in trusted_bases.items():
            parsed = urlparse(base)
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValueError("untrusted_entry_base")
            if parsed.query or parsed.fragment:
                raise ValueError("entry_base_must_not_contain_query_or_fragment")
            self._bases[target] = base.rstrip("?")

    def build(self, target: EntryTarget, public_token: str) -> str:
        base = self._bases[target]
        separator = "&" if "?" in base else "?"
        return f"{base}{separator}{urlencode({'entry_token': public_token})}"

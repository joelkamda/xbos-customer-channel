from __future__ import annotations

import secrets
import time
from dataclasses import replace
from urllib.parse import parse_qs, urlparse

from ..entry_context import (
    EntryPurpose,
    EntryTarget,
    EntryTokenRecord,
    ReplayPolicy,
    ResolvedEntryContext,
)
from ..ports import EntryTokenStorePort, XBOSContextPort
from ..security.entry_tokens import HmacEntryTokenCodec, InvalidEntryToken
from ..transports.entry_links import EntryLinkBuilder


class EntryContextRejected(PermissionError):
    pass


class EntryContextService:
    """Channel entry orchestration. Canonical merchant/location semantics remain XBOS-owned."""

    TOKEN_VERSION = 1

    def __init__(
        self,
        *,
        xbos_context: XBOSContextPort,
        token_store: EntryTokenStorePort,
        token_codec: HmacEntryTokenCodec,
        link_builder: EntryLinkBuilder,
    ) -> None:
        self._xbos = xbos_context
        self._store = token_store
        self._codec = token_codec
        self._links = link_builder

    def issue_entry(
        self,
        *,
        merchant_ref: str,
        location_ref: str,
        table_ref: str | None,
        dining_area_ref: str | None,
        purpose: EntryPurpose,
        expires_at_epoch: int,
        replay_policy: ReplayPolicy,
        now_epoch: int | None = None,
    ) -> tuple[EntryTokenRecord, str]:
        current = int(time.time()) if now_epoch is None else now_epoch
        if expires_at_epoch <= current:
            raise ValueError("entry_expiry_must_be_future")
        if purpose is EntryPurpose.DINE_IN and table_ref is None:
            raise ValueError("table_required_for_dine_in")

        # Validate future XBOS authority before issuing a channel entry reference.
        self._xbos.resolve_context(
            merchant_ref=merchant_ref,
            location_ref=location_ref,
            table_ref=table_ref,
            purpose=purpose,
        )
        token_ref = "ent_" + secrets.token_urlsafe(24)
        nonce = secrets.token_urlsafe(18)
        record = EntryTokenRecord(
            token_ref=token_ref,
            merchant_ref=merchant_ref,
            location_ref=location_ref,
            table_ref=table_ref,
            dining_area_ref=dining_area_ref,
            purpose=purpose,
            expires_at_epoch=expires_at_epoch,
            version=self.TOKEN_VERSION,
            replay_policy=replay_policy,
        )
        self._store.put(record)
        token = self._codec.issue(
            version=record.version,
            token_ref=record.token_ref,
            expires_at_epoch=record.expires_at_epoch,
            nonce=nonce,
        )
        return record, token

    def resolve(
        self,
        public_token: str,
        *,
        now_epoch: int | None = None,
        expected_merchant_ref: str | None = None,
    ) -> ResolvedEntryContext:
        current = int(time.time()) if now_epoch is None else now_epoch
        try:
            envelope = self._codec.verify(public_token, now_epoch=current)
        except InvalidEntryToken as exc:
            raise EntryContextRejected(str(exc)) from None

        record = self._store.get(envelope.token_ref)
        # Unknown token, mismatched expiry or mismatched version all fail closed.
        if (
            record is None
            or record.version != envelope.version
            or record.expires_at_epoch != envelope.expires_at_epoch
        ):
            raise EntryContextRejected("invalid_or_unknown_entry")

        if expected_merchant_ref is not None and record.merchant_ref != expected_merchant_ref:
            raise EntryContextRejected("cross_tenant_context_rejected")

        if (
            record.replay_policy is ReplayPolicy.SINGLE_USE
            and record.consumed_at_epoch is not None
        ):
            raise EntryContextRejected("unsafe_replay_rejected")

        try:
            projection = self._xbos.resolve_context(
                merchant_ref=record.merchant_ref,
                location_ref=record.location_ref,
                table_ref=record.table_ref,
                purpose=record.purpose,
            )
        except (KeyError, PermissionError, ValueError):
            raise EntryContextRejected("entry_context_unavailable") from None

        if record.replay_policy is ReplayPolicy.SINGLE_USE:
            self._store.mark_consumed(record.token_ref, current)

        return ResolvedEntryContext(
            token_ref=record.token_ref,
            merchant_ref=record.merchant_ref,
            location_ref=record.location_ref,
            table_ref=record.table_ref,
            dining_area_ref=record.dining_area_ref,
            purpose=record.purpose,
            projection=projection,
        )

    def launch_links(self, public_token: str) -> dict[EntryTarget, str]:
        return {
            EntryTarget.WHATSAPP: self._links.build(EntryTarget.WHATSAPP, public_token),
            EntryTarget.CUSTOMER_WEB: self._links.build(EntryTarget.CUSTOMER_WEB, public_token),
        }

    @staticmethod
    def token_from_link(url: str) -> str:
        values = parse_qs(urlparse(url).query).get("entry_token", [])
        if len(values) != 1 or not values[0]:
            raise EntryContextRejected("missing_entry_token")
        return values[0]

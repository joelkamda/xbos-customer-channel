from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from ..entry_context import (
    EntryContextAttestation,
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
    """Channel entry orchestration. Canonical merchant/location/table semantics remain XBOS-owned."""

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

    @staticmethod
    def _assert_attestation_matches_request(
        attestation: EntryContextAttestation,
        *,
        merchant_ref: str,
        location_ref: str,
        table_ref: str | None,
        dining_area_ref: str | None,
        purpose: EntryPurpose,
    ) -> None:
        if attestation.merchant_ref != merchant_ref or attestation.location_ref != location_ref:
            raise EntryContextRejected("entry_context_chain_mismatch")
        if attestation.table_ref != table_ref or attestation.purpose is not purpose:
            raise EntryContextRejected("entry_context_chain_mismatch")
        if dining_area_ref is not None and attestation.dining_area_ref != dining_area_ref:
            raise EntryContextRejected("entry_context_chain_mismatch")
        if not attestation.context_binding_ref:
            raise EntryContextRejected("entry_context_binding_missing")

    @staticmethod
    def _record_matches_attestation(record: EntryTokenRecord, attestation: EntryContextAttestation) -> bool:
        return (
            record.tenant_ref == attestation.tenant_ref
            and record.merchant_ref == attestation.merchant_ref
            and record.location_ref == attestation.location_ref
            and record.table_ref == attestation.table_ref
            and record.dining_area_ref == attestation.dining_area_ref
            and record.purpose is attestation.purpose
            and record.context_binding_ref == attestation.context_binding_ref
        )

    def _attest(
        self,
        *,
        merchant_ref: str,
        location_ref: str,
        table_ref: str | None,
        dining_area_ref: str | None,
        purpose: EntryPurpose,
        context_binding_ref: str | None,
        correlation_ref: str | None,
        effective_at_epoch: int,
    ) -> EntryContextAttestation:
        bound = getattr(self._xbos, "attest_bound_context", None)
        if callable(bound):
            if not context_binding_ref:
                raise EntryContextRejected("entry_context_binding_missing")
            if not correlation_ref:
                raise EntryContextRejected("entry_context_correlation_missing")
            return bound(
                context_binding_ref=context_binding_ref,
                merchant_ref=merchant_ref,
                location_ref=location_ref,
                table_ref=table_ref,
                purpose=purpose,
                dining_area_ref=dining_area_ref,
                effective_at=datetime.fromtimestamp(
                    effective_at_epoch,
                    tz=timezone.utc,
                ),
                correlation_ref=correlation_ref,
            )
        return self._xbos.attest_context(
            merchant_ref=merchant_ref,
            location_ref=location_ref,
            table_ref=table_ref,
            purpose=purpose,
            dining_area_ref=dining_area_ref,
        )

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
        context_binding_ref: str | None = None,
        correlation_ref: str | None = None,
    ) -> tuple[EntryTokenRecord, str]:
        current = int(time.time()) if now_epoch is None else now_epoch
        if expires_at_epoch <= current:
            raise ValueError("entry_expiry_must_be_future")
        if purpose is EntryPurpose.DINE_IN and table_ref is None:
            raise ValueError("table_required_for_dine_in")

        try:
            attestation = self._attest(
                merchant_ref=merchant_ref,
                location_ref=location_ref,
                table_ref=table_ref,
                purpose=purpose,
                dining_area_ref=dining_area_ref,
                context_binding_ref=context_binding_ref,
                correlation_ref=correlation_ref,
                effective_at_epoch=current,
            )
        except (KeyError, PermissionError, ValueError):
            raise EntryContextRejected("entry_context_unavailable") from None
        self._assert_attestation_matches_request(
            attestation,
            merchant_ref=merchant_ref,
            location_ref=location_ref,
            table_ref=table_ref,
            dining_area_ref=dining_area_ref,
            purpose=purpose,
        )

        token_ref = "ent_" + secrets.token_urlsafe(24)
        nonce = secrets.token_urlsafe(18)
        record = EntryTokenRecord(
            token_ref=token_ref,
            tenant_ref=attestation.tenant_ref,
            merchant_ref=attestation.merchant_ref,
            location_ref=attestation.location_ref,
            table_ref=attestation.table_ref,
            dining_area_ref=attestation.dining_area_ref,
            purpose=attestation.purpose,
            context_binding_ref=attestation.context_binding_ref,
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
        correlation_ref: str | None = None,
    ) -> ResolvedEntryContext:
        current = int(time.time()) if now_epoch is None else now_epoch
        try:
            envelope = self._codec.verify(public_token, now_epoch=current)
        except InvalidEntryToken as exc:
            raise EntryContextRejected(str(exc)) from None

        record = self._store.get(envelope.token_ref)
        if (
            record is None
            or record.version != envelope.version
            or record.expires_at_epoch != envelope.expires_at_epoch
        ):
            raise EntryContextRejected("invalid_or_unknown_entry")

        if expected_merchant_ref is not None and record.merchant_ref != expected_merchant_ref:
            raise EntryContextRejected("cross_tenant_context_rejected")

        if record.replay_policy is ReplayPolicy.SINGLE_USE and record.consumed_at_epoch is not None:
            raise EntryContextRejected("unsafe_replay_rejected")

        try:
            attestation = self._attest(
                merchant_ref=record.merchant_ref,
                location_ref=record.location_ref,
                table_ref=record.table_ref,
                purpose=record.purpose,
                dining_area_ref=record.dining_area_ref,
                context_binding_ref=record.context_binding_ref,
                correlation_ref=correlation_ref,
                effective_at_epoch=current,
            )
        except (KeyError, PermissionError, ValueError):
            raise EntryContextRejected("entry_context_unavailable") from None

        if not self._record_matches_attestation(record, attestation):
            raise EntryContextRejected("stale_entry_context_rejected")

        if record.replay_policy is ReplayPolicy.SINGLE_USE:
            claimed = self._store.consume_if_unconsumed(record.token_ref, current)
            if claimed is None:
                raise EntryContextRejected("unsafe_replay_rejected")

        return ResolvedEntryContext(
            token_ref=record.token_ref,
            tenant_ref=attestation.tenant_ref,
            merchant_ref=attestation.merchant_ref,
            location_ref=attestation.location_ref,
            table_ref=attestation.table_ref,
            dining_area_ref=attestation.dining_area_ref,
            purpose=attestation.purpose,
            context_binding_ref=attestation.context_binding_ref,
            projection=attestation.projection,
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

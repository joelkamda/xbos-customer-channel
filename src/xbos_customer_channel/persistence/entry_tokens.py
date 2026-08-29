from __future__ import annotations

from dataclasses import replace

from ..entry_context import EntryTokenRecord


class InMemoryEntryTokenStore:
    """Channel entry/session infrastructure only; not canonical merchant/location/table truth."""

    def __init__(self) -> None:
        self._records: dict[str, EntryTokenRecord] = {}

    def put(self, record: EntryTokenRecord) -> None:
        if record.token_ref in self._records:
            raise ValueError("duplicate_entry_token_ref")
        self._records[record.token_ref] = record

    def get(self, token_ref: str) -> EntryTokenRecord | None:
        return self._records.get(token_ref)

    def mark_consumed(self, token_ref: str, consumed_at_epoch: int) -> EntryTokenRecord:
        record = self._records.get(token_ref)
        if record is None:
            raise KeyError("entry_token_not_found")
        updated = replace(record, consumed_at_epoch=consumed_at_epoch)
        self._records[token_ref] = updated
        return updated

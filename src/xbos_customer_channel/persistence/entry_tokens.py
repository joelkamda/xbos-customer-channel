from __future__ import annotations

from dataclasses import replace
from threading import Lock

from ..entry_context import EntryTokenRecord


class InMemoryEntryTokenStore:
    """Single-process fixture with atomic compare-and-set for single-use token claims."""

    def __init__(self) -> None:
        self._records: dict[str, EntryTokenRecord] = {}
        self._lock = Lock()

    def put(self, record: EntryTokenRecord) -> None:
        with self._lock:
            if record.token_ref in self._records:
                raise ValueError("duplicate_entry_token_ref")
            self._records[record.token_ref] = record

    def get(self, token_ref: str) -> EntryTokenRecord | None:
        with self._lock:
            return self._records.get(token_ref)

    def consume_if_unconsumed(self, token_ref: str, consumed_at_epoch: int) -> EntryTokenRecord | None:
        with self._lock:
            record = self._records.get(token_ref)
            if record is None:
                raise KeyError("entry_token_not_found")
            if record.consumed_at_epoch is not None:
                return None
            updated = replace(record, consumed_at_epoch=consumed_at_epoch)
            self._records[token_ref] = updated
            return updated

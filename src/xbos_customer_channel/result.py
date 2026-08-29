from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, TypeVar

T = TypeVar("T")


class ErrorCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    DEPENDENCY_PENDING = "dependency_pending"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    UNAUTHORIZED = "unauthorized"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class Failure:
    code: ErrorCode
    public_message: str
    correlation_ref: str


@dataclass(frozen=True, slots=True)
class Result(Generic[T]):
    value: T | None = None
    failure: Failure | None = None

    @property
    def ok(self) -> bool:
        return self.failure is None

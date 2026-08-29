from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SafeEvent:
    event_name: str
    correlation_ref: str
    session_ref: str | None = None
    dependency: str | None = None
    outcome: str | None = None


def event_dict(event: SafeEvent) -> dict[str, str]:
    """Intentionally excludes phone numbers, names, message bodies and payment credentials."""
    return {key: value for key, value in {
        "event_name": event.event_name,
        "correlation_ref": event.correlation_ref,
        "session_ref": event.session_ref,
        "dependency": event.dependency,
        "outcome": event.outcome,
    }.items() if value is not None}

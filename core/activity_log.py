"""Structured in-memory activity history for one application session."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class EventCategory(str, Enum):
    SIMULATION = "SIMULATION"
    AIRCRAFT = "AIRCRAFT"
    ATC = "ATC"
    RUNWAY = "RUNWAY"
    EMERGENCY = "EMERGENCY"


class EventSeverity(str, Enum):
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class ActivityEvent:
    event_id: int
    timestamp: datetime
    pnr_id: str | None
    category: EventCategory
    message: str
    severity: EventSeverity = EventSeverity.INFO

    @property
    def display_time(self) -> str:
        return self.timestamp.strftime("%H:%M:%S")


class ActivityLog:
    """Append-only event IDs with resettable in-memory session history."""

    def __init__(self) -> None:
        self._events: list[ActivityEvent] = []
        self._next_event_id = 1
        self.revision = 0

    @property
    def events(self) -> tuple[ActivityEvent, ...]:
        return tuple(self._events)

    def record(
        self,
        category: EventCategory,
        message: str,
        pnr_id: str | None = None,
        severity: EventSeverity = EventSeverity.INFO,
    ) -> ActivityEvent:
        if not message.strip():
            raise ValueError("Activity event messages cannot be empty")
        event = ActivityEvent(
            event_id=self._next_event_id,
            timestamp=datetime.now(),
            pnr_id=pnr_id,
            category=category,
            message=message,
            severity=severity,
        )
        self._next_event_id += 1
        self._events.append(event)
        self.revision += 1
        return event

    def clear(self) -> None:
        self._events.clear()
        self.revision += 1

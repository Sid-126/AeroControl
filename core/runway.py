"""Runway model used by the airport operations dashboard."""

from dataclasses import dataclass, field
from enum import Enum
from math import isfinite

Position = tuple[float, float]


class RunwayStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    RESERVED = "RESERVED"
    OCCUPIED = "OCCUPIED"
    CLEARING = "CLEARING"
    UNAVAILABLE = "UNAVAILABLE"


class RunwayOperationalStatus(str, Enum):
    OPEN = "OPEN"
    CLOSURE_PENDING = "CLOSURE PENDING"
    CLOSED = "CLOSED"
    UNAVAILABLE = "UNAVAILABLE"


class RunwayOperation(str, Enum):
    LANDING = "LANDING"
    TAKEOFF = "TAKEOFF"


@dataclass
class Runway:
    """Runway state and normalized geometry shared by all dashboard views."""

    runway_id: str
    available: bool
    reserved_by: str | None = None
    operation_type: RunwayOperation | None = None
    centerline: tuple[Position, ...] = ((0.12, 0.0), (0.50, 0.0))
    departure_end_point: Position = (0.88, 0.0)
    landing_vacate_point: Position = (0.55, 0.12)
    status: RunwayStatus = field(init=False)
    operational_status: RunwayOperationalStatus = field(init=False)
    closure_reason: str | None = field(default=None, init=False)
    remaining_clearing_seconds: float = field(default=0.0, init=False)
    _initial_available: bool = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if len(self.centerline) < 2:
            raise ValueError("A runway centerline must have at least two points")
        if self.reserved_by is not None or self.operation_type is not None:
            raise ValueError(
                "Initial runway reservations are unsupported; runways must start unassigned"
            )
        self._initial_available = self.available
        self.status = (
            RunwayStatus.AVAILABLE if self.available else RunwayStatus.UNAVAILABLE
        )
        self.operational_status = (
            RunwayOperationalStatus.OPEN
            if self.available
            else RunwayOperationalStatus.UNAVAILABLE
        )

    @property
    def assigned_aircraft_id(self) -> str | None:
        return self.reserved_by

    @property
    def departure_clearance_point(self) -> Position:
        """Return the runway-end waypoint that marks physical departure clearance."""
        return self.centerline[-1]

    @property
    def eligible_for_operation(self) -> bool:
        return (
            self.operational_status is RunwayOperationalStatus.OPEN
            and self.available
            and self.status is RunwayStatus.AVAILABLE
            and self.reserved_by is None
            and self.operation_type is None
            and self.remaining_clearing_seconds == 0.0
        )

    @property
    def can_request_closure(self) -> bool:
        if self.operational_status is not RunwayOperationalStatus.OPEN:
            return False
        if self.eligible_for_operation:
            return True
        has_active_owner = (
            not self.available
            and bool(self.reserved_by)
            and isinstance(self.operation_type, RunwayOperation)
        )
        if self.status in (RunwayStatus.RESERVED, RunwayStatus.OCCUPIED):
            return has_active_owner and self.remaining_clearing_seconds == 0.0
        return (
            self.status is RunwayStatus.CLEARING
            and has_active_owner
            and isfinite(self.remaining_clearing_seconds)
            and self.remaining_clearing_seconds >= 0.0
        )

    def reserve(
        self,
        pnr_id: str,
        operation_type: RunwayOperation = RunwayOperation.LANDING,
    ) -> bool:
        if not pnr_id.strip():
            raise ValueError("A runway reservation requires a PNR")
        if not isinstance(operation_type, RunwayOperation):
            raise ValueError("A runway reservation requires a valid operation type")
        if not self.eligible_for_operation:
            return False
        self.available = False
        self.reserved_by = pnr_id
        self.operation_type = operation_type
        self.status = RunwayStatus.RESERVED
        self.remaining_clearing_seconds = 0.0
        return True

    def occupy(
        self,
        pnr_id: str,
        operation_type: RunwayOperation = RunwayOperation.TAKEOFF,
    ) -> bool:
        if not isinstance(operation_type, RunwayOperation):
            raise ValueError("Runway occupancy requires a valid operation type")
        if (
            self.status is not RunwayStatus.RESERVED
            or self.available
            or not self.reserved_by
            or self.reserved_by != pnr_id
            or self.operation_type is not operation_type
            or self.remaining_clearing_seconds != 0.0
        ):
            return False
        self.status = RunwayStatus.OCCUPIED
        return True

    def cancel_reservation(self, pnr_id: str) -> bool:
        """Cancel only the matching reservation, never an occupied runway."""
        if (
            self.status is not RunwayStatus.RESERVED
            or self.available
            or self.reserved_by != pnr_id
            or not isinstance(self.operation_type, RunwayOperation)
            or self.remaining_clearing_seconds != 0.0
        ):
            return False
        self.available = True
        self.reserved_by = None
        self.operation_type = None
        self.status = RunwayStatus.AVAILABLE
        if self.operational_status is RunwayOperationalStatus.CLOSURE_PENDING:
            self.complete_pending_closure()
        return True

    def begin_clearing(self, pnr_id: str, delay_seconds: float = 5.0) -> bool:
        if not isfinite(delay_seconds) or delay_seconds < 0:
            raise ValueError("Runway clearing delay must be finite and non-negative")
        if (
            self.status is not RunwayStatus.OCCUPIED
            or self.available
            or not self.reserved_by
            or self.reserved_by != pnr_id
            or not isinstance(self.operation_type, RunwayOperation)
            or self.remaining_clearing_seconds != 0.0
        ):
            return False
        self.status = RunwayStatus.CLEARING
        self.remaining_clearing_seconds = delay_seconds
        return True

    def advance_clearing(self, elapsed_seconds: float) -> bool:
        """Advance the shared-loop clearing countdown; return true on release."""
        if self.status is not RunwayStatus.CLEARING:
            return False
        if (
            self.available
            or not self.reserved_by
            or not isinstance(self.operation_type, RunwayOperation)
            or not isfinite(self.remaining_clearing_seconds)
            or self.remaining_clearing_seconds < 0
        ):
            raise RuntimeError("Runway clearing state is inconsistent")
        if not isfinite(elapsed_seconds) or elapsed_seconds < 0:
            raise ValueError("Runway clearing elapsed time must be finite and non-negative")
        if elapsed_seconds == 0:
            return False
        self.remaining_clearing_seconds = max(
            0.0, self.remaining_clearing_seconds - elapsed_seconds
        )
        if self.remaining_clearing_seconds > 1e-9:
            return False
        self.available = True
        self.reserved_by = None
        self.operation_type = None
        self.status = RunwayStatus.AVAILABLE
        self.remaining_clearing_seconds = 0.0
        if self.operational_status is RunwayOperationalStatus.CLOSURE_PENDING:
            self.complete_pending_closure()
        return True

    @property
    def reopen_permitted(self) -> bool:
        return self._initial_available

    def close(self, reason: str) -> bool:
        """Close a physically clear, open runway without changing occupancy."""
        self._validate_closure_reason(reason)
        if (
            self.operational_status is not RunwayOperationalStatus.OPEN
            or not self.eligible_for_operation
        ):
            return False
        self.operational_status = RunwayOperationalStatus.CLOSED
        self.closure_reason = reason.strip()
        return True

    def defer_closure(self, reason: str) -> bool:
        """Block new reservations while an existing operation finishes."""
        self._validate_closure_reason(reason)
        if (
            not self.can_request_closure
            or self.status is RunwayStatus.AVAILABLE
        ):
            return False
        self.operational_status = RunwayOperationalStatus.CLOSURE_PENDING
        self.closure_reason = reason.strip()
        return True

    def complete_pending_closure(self) -> bool:
        """Finish a deferred closure only after physical runway release."""
        if (
            self.operational_status is not RunwayOperationalStatus.CLOSURE_PENDING
            or not self.available
            or self.status is not RunwayStatus.AVAILABLE
            or self.reserved_by is not None
            or self.operation_type is not None
            or self.remaining_clearing_seconds != 0.0
        ):
            return False
        self.operational_status = RunwayOperationalStatus.CLOSED
        return True

    def reopen(self) -> bool:
        """Reopen only a temporarily closed runway at its configured baseline."""
        if (
            self.operational_status is not RunwayOperationalStatus.CLOSED
            or not self.reopen_permitted
            or not self.available
            or self.status is not RunwayStatus.AVAILABLE
            or self.reserved_by is not None
            or self.operation_type is not None
            or self.remaining_clearing_seconds != 0.0
        ):
            return False
        self.operational_status = RunwayOperationalStatus.OPEN
        self.closure_reason = None
        return True

    def cancel_pending_closure(self) -> bool:
        """Cancel an explicit deferred closure without changing occupancy."""
        if self.operational_status is not RunwayOperationalStatus.CLOSURE_PENDING:
            return False
        self.operational_status = RunwayOperationalStatus.OPEN
        self.closure_reason = None
        return True

    @staticmethod
    def _validate_closure_reason(reason: str) -> None:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("A runway closure requires a non-empty reason")

    def reset(self) -> None:
        self.available = self._initial_available
        self.reserved_by = None
        self.operation_type = None
        self.remaining_clearing_seconds = 0.0
        self.status = (
            RunwayStatus.AVAILABLE if self._initial_available else RunwayStatus.UNAVAILABLE
        )
        self.operational_status = (
            RunwayOperationalStatus.OPEN
            if self._initial_available
            else RunwayOperationalStatus.UNAVAILABLE
        )
        self.closure_reason = None

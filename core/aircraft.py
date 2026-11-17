"""Mutable aircraft state used by the static and simulated radar views."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from math import hypot, isfinite

Position = tuple[float, float]
_EPSILON = 1e-9

AWAITING_TAKEOFF_CLEARANCE = "AWAITING TAKEOFF CLEARANCE"
CLEARED_FOR_TAKEOFF = "CLEARED FOR TAKEOFF"
TAKING_OFF = "TAKING OFF"
DEPARTED = "DEPARTED"
AWAITING_LANDING_CLEARANCE = "Awaiting landing clearance"
CLEARED_TO_LAND = "CLEARED TO LAND"
LANDING = "LANDING"
LANDED = "LANDED"
ENTERING_HOLD = "ENTERING HOLD"
HOLDING = "HOLDING"
EXITING_HOLD = "EXITING HOLD"
GOING_AROUND = "GOING AROUND"


class FlightPhase(str, Enum):
    GROUND = "GROUND"
    APPROACH = "APPROACH"
    DESCENT = "DESCENT"
    LEVEL = "LEVEL"
    FINAL_APPROACH = "FINAL APPROACH"
    ENTERING_HOLD = "ENTERING HOLD"
    HOLDING = "HOLDING"
    EXITING_HOLD = "EXITING HOLD"
    GOING_AROUND = "GOING AROUND"
    TAKEOFF_ROLL = "TAKEOFF ROLL"
    CLIMB = "CLIMB"
    LANDED = "LANDED"
    DEPARTED = "DEPARTED"


class RouteMode(str, Enum):
    APPROACH = "APPROACH"
    HOLD_ENTRY = "HOLD ENTRY"
    HOLDING = "HOLDING"
    HOLD_EXIT = "HOLD EXIT"
    FINAL_APPROACH = "FINAL APPROACH"
    GO_AROUND = "GO-AROUND"
    DEPARTURE = "DEPARTURE"


@dataclass
class Aircraft:
    """Flight information and normalized route progress for one aircraft."""

    flight_id: str
    altitude_ft: float
    speed_kts: int
    flight_type: str
    status: str
    initial_position: Position = (0.0, 0.0)
    route_waypoints: tuple[Position, ...] = ()
    movement_speed: float = 0.08
    current_position: Position | None = None
    route_segment: int = 0
    segment_progress: float = 0.0
    assigned_runway: str | None = None
    route_complete: bool = field(default=False, init=False)
    initial_altitude_ft: float = field(default=0.0, init=False)
    target_altitude_ft: float = field(default=0.0, init=False)
    altitude_segment_start_ft: float = field(default=0.0, init=False)
    altitude_segment_end_ft: float = field(default=0.0, init=False)
    flight_phase: FlightPhase = field(default=FlightPhase.GROUND, init=False)
    route_mode: RouteMode = field(default=RouteMode.APPROACH, init=False)
    approach_checkpoint: Position = field(default=(0.0, 0.0), init=False)
    holding_altitude_ft: float = field(default=0.0, init=False)
    holding_circuit_count: int = field(default=0, init=False)
    go_around_count: int = field(default=0, init=False)
    holding_altitude_transition_pending: bool = field(default=False, init=False)
    runway_release_pending: bool = field(default=False, init=False)
    _initial_status: str = field(default="", init=False, repr=False)
    _initial_route_waypoints: tuple[Position, ...] = field(
        default=(), init=False, repr=False
    )
    _initial_flight_phase: FlightPhase = field(
        default=FlightPhase.GROUND, init=False, repr=False
    )
    _initial_route_mode: RouteMode = field(
        default=RouteMode.APPROACH, init=False, repr=False
    )
    _altitude_profile: tuple[tuple[int, float], ...] = field(
        default=(), init=False, repr=False
    )
    runway_clearance_waypoint_index: int | None = field(default=None, init=False)
    liftoff_waypoint_index: int | None = field(default=None, init=False)
    touchdown_waypoint_index: int | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self._initial_status = self.status
        self._initial_route_waypoints = self.route_waypoints
        if not isfinite(self.altitude_ft) or self.altitude_ft < 0:
            raise ValueError("altitude_ft must be finite and non-negative")
        self.initial_altitude_ft = float(self.altitude_ft)
        self.altitude_ft = self.initial_altitude_ft
        if self.assigned_runway is not None:
            raise ValueError(
                "Initial runway assignments are managed by SimulationController"
            )
        if self.flight_type == "Departure" and self.initial_altitude_ft != 0:
            raise ValueError("Departure aircraft must start at runway elevation (0 ft)")
        if self.current_position is None:
            self.current_position = self.initial_position
        self.approach_checkpoint = (
            self.route_waypoints[-1] if self.route_waypoints else self.initial_position
        )
        if self.route_waypoints and self.route_waypoints[0] != self.initial_position:
            raise ValueError("The first route waypoint must match initial_position")
        if len(self.route_waypoints) >= 2:
            self._validate_movement_speed()
        self.route_complete = len(self.route_waypoints) < 2
        self.target_altitude_ft = self.altitude_ft
        self.altitude_segment_start_ft = self.altitude_ft
        self.altitude_segment_end_ft = self.altitude_ft
        self.flight_phase = (
            FlightPhase.APPROACH
            if self.flight_type == "Arrival"
            else FlightPhase.GROUND
        )
        self._initial_flight_phase = self.flight_phase
        self.route_mode = (
            RouteMode.APPROACH
            if self.flight_type == "Arrival"
            else RouteMode.DEPARTURE
        )
        self._initial_route_mode = self.route_mode
        self.holding_altitude_ft = self.altitude_ft
        self._altitude_profile = self._constant_altitude_profile()

    def advance(self, elapsed_seconds: float) -> None:
        """Move toward route waypoints by distance traveled in normalized space."""
        if self.route_complete or elapsed_seconds <= 0:
            return

        self._validate_movement_speed()
        remaining_distance = self.movement_speed * elapsed_seconds
        while remaining_distance > _EPSILON and not self.route_complete:
            start = self.route_waypoints[self.route_segment]
            end = self.route_waypoints[self.route_segment + 1]
            segment_length = hypot(end[0] - start[0], end[1] - start[1])

            if segment_length <= _EPSILON:
                self.current_position = end
                self.route_segment += 1
                self.segment_progress = 0.0
                self._complete_if_at_route_end()
                continue

            distance_left_on_segment = segment_length - self.segment_progress
            distance_to_move = min(remaining_distance, distance_left_on_segment)
            self.segment_progress += distance_to_move
            remaining_distance -= distance_to_move
            progress = min(self.segment_progress / segment_length, 1.0)
            self.current_position = (
                start[0] + (end[0] - start[0]) * progress,
                start[1] + (end[1] - start[1]) * progress,
            )

            if self.segment_progress >= segment_length - _EPSILON:
                self.current_position = end
                self.route_segment += 1
                self.segment_progress = 0.0
                self._complete_if_at_route_end()
        self._update_altitude_for_position()

    def reset(self) -> None:
        """Restore the initial position, route progress, and displayed status."""
        self.current_position = self.initial_position
        self.route_waypoints = self._initial_route_waypoints
        self.route_segment = 0
        self.segment_progress = 0.0
        self.route_complete = len(self.route_waypoints) < 2
        self.status = self._initial_status
        self.altitude_ft = self.initial_altitude_ft
        self.target_altitude_ft = self.initial_altitude_ft
        self.altitude_segment_start_ft = self.initial_altitude_ft
        self.altitude_segment_end_ft = self.initial_altitude_ft
        self.flight_phase = self._initial_flight_phase
        self.route_mode = self._initial_route_mode
        self.holding_altitude_ft = self.initial_altitude_ft
        self.holding_circuit_count = 0
        self.go_around_count = 0
        self.holding_altitude_transition_pending = False
        self.runway_release_pending = False
        self._altitude_profile = self._constant_altitude_profile()
        self.assigned_runway = None
        self.runway_clearance_waypoint_index = None
        self.liftoff_waypoint_index = None
        self.touchdown_waypoint_index = None

    def configure_route(
        self,
        waypoints: tuple[Position, ...],
        runway_clearance_waypoint_index: int | None = None,
        liftoff_waypoint_index: int | None = None,
        touchdown_waypoint_index: int | None = None,
    ) -> None:
        """Replace the remaining route without changing the aircraft position."""
        if not waypoints or waypoints[0] != self.current_position:
            raise ValueError("A replacement route must begin at the current position")
        if len(waypoints) >= 2:
            self._validate_movement_speed()
        if (
            runway_clearance_waypoint_index is not None
            and not 1 <= runway_clearance_waypoint_index < len(waypoints)
        ):
            raise ValueError("Runway clearance waypoint is outside the route")
        if (
            liftoff_waypoint_index is not None
            and not 1 <= liftoff_waypoint_index < len(waypoints) - 1
        ):
            raise ValueError("Liftoff waypoint must precede the route endpoint")
        if (
            touchdown_waypoint_index is not None
            and not 1 <= touchdown_waypoint_index < len(waypoints) - 1
        ):
            raise ValueError("Touchdown waypoint must precede the runway exit")
        self.route_waypoints = waypoints
        self.route_segment = 0
        self.segment_progress = 0.0
        self.route_complete = len(waypoints) < 2
        self.runway_clearance_waypoint_index = runway_clearance_waypoint_index
        self.liftoff_waypoint_index = liftoff_waypoint_index
        self.touchdown_waypoint_index = touchdown_waypoint_index
        self._altitude_profile = self._constant_altitude_profile()
        self.target_altitude_ft = self.altitude_ft
        self._update_altitude_for_position()

    def configure_altitude_profile(
        self,
        waypoint_altitudes: Sequence[tuple[int, float]],
    ) -> None:
        """Set altitude anchors at route waypoints for distance-based interpolation."""
        profile = tuple(waypoint_altitudes)
        if len(self.route_waypoints) < 2 or len(profile) < 2:
            raise ValueError("A moving route needs at least two altitude anchors")
        if profile[0][0] != 0 or profile[-1][0] != len(self.route_waypoints) - 1:
            raise ValueError("Altitude profile must cover the complete route")

        previous_index = -1
        for waypoint_index, altitude_ft in profile:
            if not previous_index < waypoint_index < len(self.route_waypoints):
                raise ValueError("Altitude profile waypoint indices must increase")
            if not isfinite(altitude_ft) or altitude_ft < 0:
                raise ValueError("Profile altitudes must be finite and non-negative")
            previous_index = waypoint_index

        if abs(profile[0][1] - self.altitude_ft) > _EPSILON:
            raise ValueError(
                "Altitude profile must begin at the aircraft's current altitude"
            )

        cumulative_distances = self._route_cumulative_distances()
        if cumulative_distances[-1] <= _EPSILON and any(
            abs(altitude - profile[0][1]) > _EPSILON
            for _waypoint_index, altitude in profile[1:]
        ):
            raise ValueError("Altitude cannot change along a zero-length route")
        self._altitude_profile = profile
        self.target_altitude_ft = profile[-1][1]
        self._update_altitude_for_position()

    def _validate_movement_speed(self) -> None:
        if not isfinite(self.movement_speed) or self.movement_speed <= 0:
            raise ValueError(
                "movement_speed must be finite and greater than zero for a moving route"
            )

    def _constant_altitude_profile(self) -> tuple[tuple[int, float], ...]:
        if len(self.route_waypoints) < 2:
            return ((0, self.altitude_ft),)
        return (
            (0, self.altitude_ft),
            (len(self.route_waypoints) - 1, self.altitude_ft),
        )

    def _route_cumulative_distances(self) -> tuple[float, ...]:
        distances = [0.0]
        for start, end in zip(self.route_waypoints, self.route_waypoints[1:]):
            distances.append(
                distances[-1] + hypot(end[0] - start[0], end[1] - start[1])
            )
        return tuple(distances)

    def _altitude_at_distance(
        self, distance: float, cumulative_distances: tuple[float, ...]
    ) -> float:
        if not self._altitude_profile:
            return self.initial_altitude_ft
        if distance <= 0:
            return self._altitude_profile[0][1]

        for (start_index, start_altitude), (end_index, end_altitude) in zip(
            self._altitude_profile, self._altitude_profile[1:]
        ):
            start_distance = cumulative_distances[start_index]
            end_distance = cumulative_distances[end_index]
            if distance <= end_distance + _EPSILON:
                segment_distance = end_distance - start_distance
                if segment_distance <= _EPSILON:
                    return start_altitude
                fraction = min(
                    max((distance - start_distance) / segment_distance, 0.0),
                    1.0,
                )
                return start_altitude + (end_altitude - start_altitude) * fraction
        return self._altitude_profile[-1][1]

    def _update_altitude_for_position(self) -> None:
        if not self.route_waypoints or not self._altitude_profile:
            return
        cumulative_distances = self._route_cumulative_distances()
        if self.route_complete:
            distance = cumulative_distances[-1]
        else:
            segment_index = min(
                max(self.route_segment, 0), len(self.route_waypoints) - 2
            )
            segment_length = (
                cumulative_distances[segment_index + 1]
                - cumulative_distances[segment_index]
            )
            distance = cumulative_distances[segment_index] + min(
                max(self.segment_progress, 0.0), segment_length
            )
        self.altitude_ft = self._altitude_at_distance(distance, cumulative_distances)

        if self.route_complete:
            self.altitude_segment_start_ft = self.target_altitude_ft
            self.altitude_segment_end_ft = self.target_altitude_ft
            return
        segment_index = min(
            max(self.route_segment, 0), len(self.route_waypoints) - 2
        )
        self.altitude_segment_start_ft = self._altitude_at_distance(
            cumulative_distances[segment_index], cumulative_distances
        )
        self.altitude_segment_end_ft = self._altitude_at_distance(
            cumulative_distances[segment_index + 1], cumulative_distances
        )

    def has_reached_waypoint(self, waypoint_index: int) -> bool:
        if not 0 <= waypoint_index < len(self.route_waypoints):
            raise ValueError("Waypoint index is outside the route")
        return self.route_complete or self.route_segment >= waypoint_index

    @property
    def route_progress(self) -> float:
        """Return the completed fraction of total waypoint distance."""
        if self.route_complete:
            return 1.0
        if len(self.route_waypoints) < 2:
            return 0.0

        segment_count = len(self.route_waypoints) - 1
        segment_index = max(0, min(self.route_segment, segment_count))
        segment_lengths = [
            hypot(end[0] - start[0], end[1] - start[1])
            for start, end in zip(self.route_waypoints, self.route_waypoints[1:])
        ]
        total_distance = sum(segment_lengths)
        if total_distance <= _EPSILON:
            return 0.0

        travelled_distance = sum(segment_lengths[:segment_index])
        if segment_index < segment_count:
            travelled_distance += min(
                max(self.segment_progress, 0.0),
                segment_lengths[segment_index],
            )
        return min(max(travelled_distance / total_distance, 0.0), 1.0)

    def _complete_if_at_route_end(self) -> None:
        if self.route_segment >= len(self.route_waypoints) - 1:
            self.route_complete = True
            self.status = "Route complete"

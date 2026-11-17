"""Single-loop controller for the educational aircraft movement simulation."""

from collections.abc import Callable, Sequence
from enum import Enum
from math import cos, isfinite, pi, sin

from core.activity_log import (
    ActivityLog,
    EventCategory,
    EventSeverity,
)
from core.aircraft import (
    AWAITING_TAKEOFF_CLEARANCE,
    AWAITING_LANDING_CLEARANCE,
    CLEARED_FOR_TAKEOFF,
    CLEARED_TO_LAND,
    DEPARTED,
    ENTERING_HOLD,
    EXITING_HOLD,
    FlightPhase,
    GOING_AROUND,
    HOLDING,
    LANDED,
    LANDING,
    TAKING_OFF,
    Aircraft,
    Position,
    RouteMode,
)
from core.runway import (
    Runway,
    RunwayOperation,
    RunwayOperationalStatus,
    RunwayStatus,
)

AfterCallback = Callable[[], None]
ScheduleCallback = Callable[[int, AfterCallback], str]
CancelCallback = Callable[[str], None]
StateChangeCallback = Callable[["SimulationState"], None]
SimulationListener = Callable[["SimulationController"], None]


class SimulationState(Enum):
    READY = "Ready"
    RUNNING = "Running"
    PAUSED = "Paused"
    COMPLETED = "Completed"


class SimulationController:
    """Advances shared aircraft state on one injected Tk-compatible after loop."""

    def __init__(
        self,
        aircraft: Sequence[Aircraft],
        schedule_after: ScheduleCallback,
        cancel_after: CancelCallback,
        on_change: StateChangeCallback | None = None,
        interval_ms: int = 75,
        runways: Sequence[Runway] = (),
        runway_clearing_delay_seconds: float = 5.0,
        arrival_checkpoint_altitude_ft: float = 3000.0,
        departure_climb_target_altitude_ft: float = 12000.0,
        departure_liftoff_fraction: float = 0.5,
        holding_altitude_ft: float | None = None,
        go_around_climb_target_altitude_ft: float = 16000.0,
    ) -> None:
        if interval_ms <= 0:
            raise ValueError("interval_ms must be greater than zero")
        if runway_clearing_delay_seconds < 0:
            raise ValueError("runway_clearing_delay_seconds cannot be negative")
        if (
            not isfinite(arrival_checkpoint_altitude_ft)
            or arrival_checkpoint_altitude_ft <= 0
        ):
            raise ValueError("arrival_checkpoint_altitude_ft must be finite and positive")
        if (
            not isfinite(departure_climb_target_altitude_ft)
            or departure_climb_target_altitude_ft <= 0
        ):
            raise ValueError(
                "departure_climb_target_altitude_ft must be finite and positive"
            )
        if (
            not isfinite(departure_liftoff_fraction)
            or not 0 < departure_liftoff_fraction < 1
        ):
            raise ValueError("departure_liftoff_fraction must be between zero and one")
        if holding_altitude_ft is not None and (
            not isfinite(holding_altitude_ft) or holding_altitude_ft <= 0
        ):
            raise ValueError("holding_altitude_ft must be finite and positive")
        if (
            not isfinite(go_around_climb_target_altitude_ft)
            or go_around_climb_target_altitude_ft <= 0
        ):
            raise ValueError(
                "go_around_climb_target_altitude_ft must be finite and positive"
            )
        self.aircraft = aircraft
        self.runways = runways
        self.runway_clearing_delay_seconds = runway_clearing_delay_seconds
        self.arrival_checkpoint_altitude_ft = arrival_checkpoint_altitude_ft
        self.departure_climb_target_altitude_ft = departure_climb_target_altitude_ft
        self.departure_liftoff_fraction = departure_liftoff_fraction
        self.holding_altitude_ft = (
            arrival_checkpoint_altitude_ft
            if holding_altitude_ft is None
            else holding_altitude_ft
        )
        self.go_around_climb_target_altitude_ft = (
            go_around_climb_target_altitude_ft
        )
        self.activity_log = ActivityLog()
        self.interval_ms = interval_ms
        self.state = SimulationState.READY
        self._selected_aircraft_id: str | None = None
        self._schedule_after = schedule_after
        self._cancel_after = cancel_after
        self._on_change = on_change
        self._listeners: set[SimulationListener] = set()
        self._reported_clearance_rejections: set[tuple[str, str]] = set()
        self._reported_takeoff_rejections: set[tuple[str, str]] = set()
        self._reported_holding_rejections: set[tuple[str, str]] = set()
        self._reported_go_around_rejections: set[tuple[str, str]] = set()
        self._after_id: str | None = None
        self._shutdown = False
        self._configure_initial_altitude_profiles()

    def subscribe(self, listener: SimulationListener) -> Callable[[], None]:
        """Subscribe to shared-state updates and return an idempotent unsubscribe."""
        self._listeners.add(listener)

        def unsubscribe() -> None:
            self._listeners.discard(listener)

        return unsubscribe

    @property
    def selected_aircraft_id(self) -> str | None:
        return self._selected_aircraft_id

    @property
    def selected_aircraft(self) -> Aircraft | None:
        """Resolve the selected identifier to its existing shared aircraft."""
        if self.selected_aircraft_id is None:
            return None
        return next(
            (
                aircraft
                for aircraft in self.aircraft
                if aircraft.flight_id == self.selected_aircraft_id
            ),
            None,
        )

    def select_aircraft(self, flight_id: str | None) -> None:
        """Select an existing aircraft by identifier, or clear the selection."""
        if self._shutdown:
            return
        if flight_id is not None and not any(
            aircraft.flight_id == flight_id for aircraft in self.aircraft
        ):
            raise ValueError(f"Unknown aircraft: {flight_id}")
        if flight_id == self.selected_aircraft_id:
            return
        self._selected_aircraft_id = flight_id
        self._notify()

    def start(self) -> None:
        """Start from READY or continue a paused run without duplicating a loop."""
        if self._shutdown:
            return
        if self.state is SimulationState.PAUSED:
            self.resume()
            return
        if self.state is not SimulationState.READY:
            return
        self.state = SimulationState.RUNNING
        self._record_event(
            EventCategory.SIMULATION,
            "Simulation started.",
            severity=EventSeverity.SUCCESS,
        )
        self._notify()
        self._schedule_tick()

    def pause(self) -> None:
        if self._shutdown or self.state is not SimulationState.RUNNING:
            return
        self._cancel_pending_tick()
        self.state = SimulationState.PAUSED
        self._record_event(
            EventCategory.SIMULATION,
            "Simulation paused.",
            severity=EventSeverity.WARNING,
        )
        self._notify()

    def resume(self) -> None:
        if self._shutdown or self.state is not SimulationState.PAUSED:
            return
        self.state = SimulationState.RUNNING
        self._record_event(
            EventCategory.SIMULATION,
            "Simulation resumed.",
            severity=EventSeverity.INFO,
        )
        self._notify()
        self._schedule_tick()

    def reset(self) -> None:
        if self._shutdown:
            return
        self._cancel_pending_tick()
        for aircraft in self.aircraft:
            aircraft.reset()
        self._configure_initial_altitude_profiles()
        for runway in self.runways:
            runway.reset()
        if self.selected_aircraft is None:
            self._selected_aircraft_id = None
        self.state = SimulationState.READY
        self._reported_clearance_rejections.clear()
        self._reported_takeoff_rejections.clear()
        self._reported_holding_rejections.clear()
        self._reported_go_around_rejections.clear()
        self.activity_log.clear()
        self._record_event(
            EventCategory.SIMULATION,
            "Simulation reset.",
            severity=EventSeverity.INFO,
        )
        self._notify()

    def can_close_runway(self, runway_id: str | None) -> bool:
        runway = self._find_runway(runway_id)
        return (
            not self._shutdown
            and runway is not None
            and runway.can_request_closure
        )

    def close_runway(self, runway_id: str, reason: str) -> bool:
        """Close a clear runway or defer closure until its active operation ends."""
        runway = self._require_runway(runway_id)
        Runway._validate_closure_reason(reason)
        if not self.can_close_runway(runway_id):
            return False

        pnr_id = runway.assigned_aircraft_id
        if runway.status is RunwayStatus.AVAILABLE:
            if not runway.close(reason):
                return False
            completion_message = f"Runway {runway_id} closed: {reason.strip()}."
            completion_severity = EventSeverity.SUCCESS
        elif not runway.defer_closure(reason):
            return False
        else:
            completion_message = (
                f"Runway {runway_id} closure deferred until its current operation "
                f"clears: {reason.strip()}."
            )
            completion_severity = EventSeverity.WARNING

        self._record_event(
            EventCategory.RUNWAY,
            f"Runway {runway_id} closure requested: {reason.strip()}.",
            pnr_id=pnr_id,
            severity=EventSeverity.INFO,
        )
        self._record_event(
            EventCategory.RUNWAY,
            completion_message,
            pnr_id=pnr_id,
            severity=completion_severity,
        )
        self._notify()
        return True

    def can_reopen_runway(self, runway_id: str | None) -> bool:
        runway = self._find_runway(runway_id)
        return (
            not self._shutdown
            and runway is not None
            and runway.operational_status is RunwayOperationalStatus.CLOSED
            and runway.reopen_permitted
            and runway.status is RunwayStatus.AVAILABLE
            and runway.available
            and runway.assigned_aircraft_id is None
            and runway.operation_type is None
        )

    def reopen_runway(self, runway_id: str) -> bool:
        runway = self._require_runway(runway_id)
        if not self.can_reopen_runway(runway_id) or not runway.reopen():
            return False
        self._record_event(
            EventCategory.RUNWAY,
            f"Runway {runway_id} reopened.",
            severity=EventSeverity.SUCCESS,
        )
        self._notify()
        return True

    def can_cancel_pending_runway_closure(self, runway_id: str | None) -> bool:
        runway = self._find_runway(runway_id)
        return (
            not self._shutdown
            and runway is not None
            and runway.operational_status
            is RunwayOperationalStatus.CLOSURE_PENDING
        )

    def cancel_pending_runway_closure(self, runway_id: str) -> bool:
        runway = self._require_runway(runway_id)
        if not self.can_cancel_pending_runway_closure(runway_id):
            return False
        pnr_id = runway.assigned_aircraft_id
        if not runway.cancel_pending_closure():
            return False
        self._record_event(
            EventCategory.RUNWAY,
            f"Pending closure for runway {runway_id} cancelled.",
            pnr_id=pnr_id,
            severity=EventSeverity.INFO,
        )
        self._notify()
        return True

    def can_grant_landing_clearance(self, flight_id: str | None) -> bool:
        aircraft = next(
            (flight for flight in self.aircraft if flight.flight_id == flight_id),
            None,
        )
        return (
            not self._shutdown
            and aircraft is not None
            and aircraft.flight_type == "Arrival"
            and (
                (
                    aircraft.route_mode is RouteMode.APPROACH
                    and aircraft.route_complete
                    and aircraft.status == AWAITING_LANDING_CLEARANCE
                )
                or (
                    aircraft.route_mode is RouteMode.HOLDING
                    and aircraft.status == HOLDING
                )
            )
            and aircraft.assigned_runway is None
            and self._available_runway_for(aircraft) is not None
        )

    def grant_landing_clearance(self, flight_id: str) -> bool:
        """Reserve a runway and route an arrival through landing and runway vacation."""
        if self._shutdown:
            return False
        aircraft = next(
            (flight for flight in self.aircraft if flight.flight_id == flight_id),
            None,
        )
        if aircraft is None:
            raise ValueError(f"Unknown aircraft: {flight_id}")
        if aircraft.assigned_runway is not None or aircraft.status == CLEARED_TO_LAND:
            return False

        if aircraft.flight_type != "Arrival":
            return self._reject_landing_clearance(
                aircraft,
                "Only arriving aircraft can receive landing clearance.",
            )
        at_checkpoint = (
            aircraft.route_mode is RouteMode.APPROACH
            and aircraft.route_complete
            and aircraft.status == AWAITING_LANDING_CLEARANCE
        )
        holding = (
            aircraft.route_mode is RouteMode.HOLDING
            and aircraft.status == HOLDING
        )
        if not (at_checkpoint or holding):
            return self._reject_landing_clearance(
                aircraft,
                "Aircraft is not at a landing-clearance holding point.",
            )

        runway = self._available_runway_for(aircraft)
        if runway is None:
            return self._reject_landing_clearance(
                aircraft,
                "No operational runway is available; landing clearance denied.",
            )
        route = (
            self._route_from_current_position(
                aircraft,
                (aircraft.approach_checkpoint,),
            )
            if holding
            else None
        )
        landing_route = self._landing_route(
            aircraft,
            runway,
            aircraft.approach_checkpoint if holding else None,
        )
        if not runway.reserve(aircraft.flight_id, RunwayOperation.LANDING):
            return self._reject_landing_clearance(
                aircraft,
                "No operational runway is available; landing clearance denied.",
            )

        if holding:
            if route is None:
                raise RuntimeError("Holding exit route was not prepared")
            aircraft.configure_route(route)
            self._configure_constant_altitude(aircraft, aircraft.holding_altitude_ft)
            aircraft.status = EXITING_HOLD
            aircraft.flight_phase = FlightPhase.EXITING_HOLD
            aircraft.route_mode = RouteMode.HOLD_EXIT
            message = (
                f"Landing clearance granted while holding for runway "
                f"{runway.runway_id}."
            )
        else:
            self._configure_final_approach(aircraft, landing_route)
            aircraft.status = CLEARED_TO_LAND
            message = f"Landing clearance granted for runway {runway.runway_id}."
        aircraft.assigned_runway = runway.runway_id
        self._reported_clearance_rejections.discard(
            (
                aircraft.flight_id,
                "No operational runway is available; landing clearance denied.",
            )
        )
        self._record_event(
            EventCategory.RUNWAY,
            f"Runway {runway.runway_id} reserved for landing.",
            pnr_id=aircraft.flight_id,
            severity=EventSeverity.SUCCESS,
        )
        self._record_event(
            EventCategory.ATC,
            message,
            pnr_id=aircraft.flight_id,
            severity=EventSeverity.SUCCESS,
        )
        self._notify()
        return True

    def can_enter_holding(self, flight_id: str | None) -> bool:
        aircraft = next(
            (flight for flight in self.aircraft if flight.flight_id == flight_id),
            None,
        )
        return (
            not self._shutdown
            and aircraft is not None
            and aircraft.flight_type == "Arrival"
            and aircraft.route_mode is RouteMode.APPROACH
            and aircraft.status
            not in (CLEARED_TO_LAND, LANDING, LANDED, ENTERING_HOLD, HOLDING)
            and aircraft.flight_phase
            in (FlightPhase.APPROACH, FlightPhase.DESCENT, FlightPhase.LEVEL)
        )

    def enter_holding(self, flight_id: str) -> bool:
        if self._shutdown:
            return False
        aircraft = next(
            (flight for flight in self.aircraft if flight.flight_id == flight_id),
            None,
        )
        if aircraft is None:
            raise ValueError(f"Unknown aircraft: {flight_id}")
        if not self.can_enter_holding(flight_id):
            return self._reject_holding_operation(
                aircraft,
                "Holding entry rejected: aircraft is not eligible.",
            )
        self._begin_holding(aircraft)
        self._notify()
        return True

    def can_go_around(self, flight_id: str | None) -> bool:
        aircraft = next(
            (flight for flight in self.aircraft if flight.flight_id == flight_id),
            None,
        )
        if (
            self._shutdown
            or aircraft is None
            or aircraft.flight_type != "Arrival"
            or aircraft.status == LANDED
        ):
            return False
        if (
            aircraft.route_mode is RouteMode.APPROACH
            and aircraft.status not in (AWAITING_LANDING_CLEARANCE, LANDED)
            and not aircraft.route_complete
        ):
            return True
        if aircraft.status in (CLEARED_TO_LAND, EXITING_HOLD):
            runway = self._runway_assigned_to(aircraft)
            return runway is not None and runway.status is RunwayStatus.RESERVED
        if aircraft.status == LANDING and aircraft.flight_phase is FlightPhase.FINAL_APPROACH:
            runway = self._runway_assigned_to(aircraft)
            return (
                runway is not None
                and runway.status is RunwayStatus.OCCUPIED
                and aircraft.touchdown_waypoint_index is not None
                and not aircraft.has_reached_waypoint(aircraft.touchdown_waypoint_index)
            )
        return False

    def go_around(self, flight_id: str) -> bool:
        if self._shutdown:
            return False
        aircraft = next(
            (flight for flight in self.aircraft if flight.flight_id == flight_id),
            None,
        )
        if aircraft is None:
            raise ValueError(f"Unknown aircraft: {flight_id}")
        if not self.can_go_around(flight_id):
            return self._reject_go_around(
                aircraft,
                "Go-around rejected: aircraft is beyond the supported approach phase.",
            )

        runway = self._runway_assigned_to(aircraft)
        occupied_runway = (
            runway
            if runway is not None and runway.status is RunwayStatus.OCCUPIED
            else None
        )
        route, escape_waypoint_index, clearance_waypoint_index = (
            self._go_around_route(aircraft, occupied_runway)
        )
        if runway is not None and runway.status is RunwayStatus.RESERVED:
            closure_pending = (
                runway.operational_status
                is RunwayOperationalStatus.CLOSURE_PENDING
            )
            closure_reason = runway.closure_reason
            if not runway.cancel_reservation(aircraft.flight_id):
                return self._reject_go_around(
                    aircraft,
                    "Go-around rejected: landing runway reservation could not be cancelled.",
                )
            aircraft.assigned_runway = None
            self._record_event(
                EventCategory.RUNWAY,
                f"Landing reservation on runway {runway.runway_id} cancelled.",
                pnr_id=aircraft.flight_id,
                severity=EventSeverity.INFO,
            )
            if closure_pending:
                self._record_event(
                    EventCategory.RUNWAY,
                    f"Runway {runway.runway_id} closed after its reservation was cancelled: "
                    f"{closure_reason}.",
                    pnr_id=aircraft.flight_id,
                    severity=EventSeverity.SUCCESS,
                )

        aircraft.configure_route(
            route,
            runway_clearance_waypoint_index=clearance_waypoint_index,
        )
        target_altitude = max(
            self.go_around_climb_target_altitude_ft,
            aircraft.altitude_ft + 1000.0,
        )
        aircraft.configure_altitude_profile(
            (
                (0, aircraft.altitude_ft),
                (escape_waypoint_index, target_altitude),
                (len(route) - 1, target_altitude),
            )
        )
        aircraft.status = GOING_AROUND
        aircraft.flight_phase = FlightPhase.GOING_AROUND
        aircraft.route_mode = RouteMode.GO_AROUND
        aircraft.go_around_count += 1
        aircraft.runway_release_pending = occupied_runway is not None
        self._reported_go_around_rejections = {
            rejection
            for rejection in self._reported_go_around_rejections
            if rejection[0] != aircraft.flight_id
        }
        self._record_event(
            EventCategory.ATC,
            "Go-around initiated.",
            pnr_id=aircraft.flight_id,
            severity=EventSeverity.WARNING,
        )
        self._notify()
        return True

    def can_grant_takeoff_clearance(self, flight_id: str | None) -> bool:
        """Return whether a departure can be cleared using a currently free runway."""
        aircraft = next(
            (flight for flight in self.aircraft if flight.flight_id == flight_id),
            None,
        )
        return (
            not self._shutdown
            and aircraft is not None
            and aircraft.flight_type == "Departure"
            and aircraft.status == AWAITING_TAKEOFF_CLEARANCE
            and aircraft.assigned_runway is None
            and not aircraft.route_complete
            and self._available_runway_for(aircraft) is not None
        )

    def grant_takeoff_clearance(self, flight_id: str) -> bool:
        """Reserve one available runway and clear an eligible departure."""
        if self._shutdown:
            return False
        aircraft = next(
            (flight for flight in self.aircraft if flight.flight_id == flight_id),
            None,
        )
        if aircraft is None:
            raise ValueError(f"Unknown aircraft: {flight_id}")
        if aircraft.flight_type != "Departure":
            return self._reject_takeoff_clearance(
                aircraft,
                "Takeoff clearance rejected: aircraft is not a departure.",
            )
        if aircraft.status == DEPARTED or aircraft.route_complete:
            return self._reject_takeoff_clearance(
                aircraft,
                "Takeoff clearance rejected: aircraft has already departed.",
            )
        if (
            aircraft.status != AWAITING_TAKEOFF_CLEARANCE
            or aircraft.assigned_runway is not None
        ):
            return self._reject_takeoff_clearance(
                aircraft,
                "Takeoff clearance rejected: departure is already cleared or in progress.",
            )

        runway = self._available_runway_for(aircraft)
        if runway is None:
            return self._reject_takeoff_clearance(
                aircraft,
                "Takeoff clearance rejected: no operational runway is available.",
            )
        route, clearance_waypoint_index, liftoff_waypoint_index = (
            self._departure_route(aircraft, runway)
        )
        if not runway.reserve(aircraft.flight_id, RunwayOperation.TAKEOFF):
            return self._reject_takeoff_clearance(
                aircraft,
                "Takeoff clearance rejected: no suitable runway is available.",
            )

        aircraft.configure_route(
            route,
            clearance_waypoint_index,
            liftoff_waypoint_index,
        )
        aircraft.configure_altitude_profile(
            (
                (0, 0.0),
                (liftoff_waypoint_index, 0.0),
                (len(route) - 1, self.departure_climb_target_altitude_ft),
            )
        )
        aircraft.assigned_runway = runway.runway_id
        aircraft.status = CLEARED_FOR_TAKEOFF
        self._reported_takeoff_rejections = {
            rejection
            for rejection in self._reported_takeoff_rejections
            if rejection[0] != aircraft.flight_id
        }
        self._record_event(
            EventCategory.RUNWAY,
            f"Runway {runway.runway_id} reserved for takeoff.",
            pnr_id=aircraft.flight_id,
            severity=EventSeverity.SUCCESS,
        )
        self._record_event(
            EventCategory.ATC,
            f"Takeoff clearance granted for runway {runway.runway_id}.",
            pnr_id=aircraft.flight_id,
            severity=EventSeverity.SUCCESS,
        )
        self._notify()
        return True

    def _available_runway_for(self, aircraft: Aircraft) -> Runway | None:
        if aircraft.assigned_runway is not None or any(
            runway.assigned_aircraft_id == aircraft.flight_id
            for runway in self.runways
        ):
            return None
        return next(
            (runway for runway in self.runways if runway.eligible_for_operation),
            None,
        )

    @staticmethod
    def _route_from_current_position(
        aircraft: Aircraft,
        waypoints: Sequence[Position],
    ) -> tuple[Position, ...]:
        if aircraft.current_position is None:
            raise RuntimeError(f"Aircraft {aircraft.flight_id} has no current position")
        return SimulationController._route_from_position(
            aircraft.current_position, waypoints
        )

    @staticmethod
    def _route_from_position(
        current_position: Position,
        waypoints: Sequence[Position],
    ) -> tuple[Position, ...]:
        route = [current_position]
        for waypoint in waypoints:
            if waypoint != route[-1]:
                route.append(waypoint)
        if len(route) < 2:
            raise ValueError("A runway route must contain at least one movement segment")
        return tuple(route)

    def _departure_route(
        self, aircraft: Aircraft, runway: Runway
    ) -> tuple[tuple[Position, ...], int, int]:
        clearance_point = runway.departure_clearance_point
        departure_end = runway.departure_end_point
        liftoff_point = (
            clearance_point[0]
            + (departure_end[0] - clearance_point[0])
            * self.departure_liftoff_fraction,
            clearance_point[1]
            + (departure_end[1] - clearance_point[1])
            * self.departure_liftoff_fraction,
        )
        route = self._route_from_current_position(
            aircraft,
            (*runway.centerline, liftoff_point, departure_end),
        )
        clearance_index = route.index(clearance_point)
        liftoff_index = route.index(liftoff_point)
        if clearance_index == 0:
            raise ValueError("Runway clearance point must follow the route start")
        if not clearance_index < liftoff_index < len(route) - 1:
            raise ValueError("The runway route must provide a liftoff segment")
        return route, clearance_index, liftoff_index

    def _landing_route(
        self,
        aircraft: Aircraft,
        runway: Runway,
        start_position: Position | None = None,
    ) -> tuple[tuple[Position, ...], int]:
        if start_position is None:
            route = self._route_from_current_position(
                aircraft,
                (*runway.centerline, runway.landing_vacate_point),
            )
        else:
            route = self._route_from_position(
                start_position,
                (*runway.centerline, runway.landing_vacate_point),
            )
        touchdown_point = runway.centerline[0]
        if touchdown_point == route[0]:
            touchdown_point = runway.centerline[-1]
        touchdown_index = route.index(touchdown_point)
        if not 1 <= touchdown_index < len(route) - 1:
            raise ValueError(
                f"Runway {runway.runway_id} route must include touchdown before exit"
            )
        return route, touchdown_index

    def _configure_final_approach(
        self,
        aircraft: Aircraft,
        landing_route: tuple[tuple[Position, ...], int],
    ) -> None:
        route, touchdown_index = landing_route
        aircraft.configure_route(
            route,
            touchdown_waypoint_index=touchdown_index,
        )
        aircraft.configure_altitude_profile(
            (
                (0, aircraft.altitude_ft),
                (touchdown_index, 0.0),
                (len(route) - 1, 0.0),
            )
        )
        aircraft.route_mode = RouteMode.FINAL_APPROACH

    @staticmethod
    def _configure_constant_altitude(
        aircraft: Aircraft, altitude_ft: float
    ) -> None:
        aircraft.configure_altitude_profile(
            (
                (0, aircraft.altitude_ft),
                (len(aircraft.route_waypoints) - 1, altitude_ft),
            )
        )

    def _holding_pattern_for(self, aircraft: Aircraft) -> tuple[Position, ...]:
        arrivals = [
            flight for flight in self.aircraft if flight.flight_type == "Arrival"
        ]
        index = arrivals.index(aircraft)
        lane = index // 2
        side = -1.0 if index % 2 == 0 else 1.0
        checkpoint_x, checkpoint_y = aircraft.approach_checkpoint
        center_x = max(
            -0.50,
            min(0.50, checkpoint_x + side * (0.30 + lane * 0.16)),
        )
        center_y = max(-0.50, min(0.34, checkpoint_y - 0.38 - lane * 0.18))
        radius_x = 0.11
        radius_y = 0.075
        points = [
            (
                center_x + radius_x * cos(2 * pi * step / 12),
                center_y + radius_y * sin(2 * pi * step / 12),
            )
            for step in range(12)
        ]
        points.append(points[0])
        return tuple(points)

    def _begin_holding(self, aircraft: Aircraft) -> None:
        pattern = self._holding_pattern_for(aircraft)
        if aircraft.current_position == pattern[0]:
            self._activate_holding_loop(aircraft, log_entry=True)
            return
        route = self._route_from_current_position(aircraft, (pattern[0],))
        aircraft.configure_route(route)
        aircraft.configure_altitude_profile(
            (
                (0, aircraft.altitude_ft),
                (len(route) - 1, aircraft.holding_altitude_ft),
            )
        )
        aircraft.status = ENTERING_HOLD
        aircraft.flight_phase = FlightPhase.ENTERING_HOLD
        aircraft.route_mode = RouteMode.HOLD_ENTRY
        aircraft.holding_altitude_transition_pending = False
        self._record_event(
            EventCategory.AIRCRAFT,
            "Aircraft entering holding pattern.",
            pnr_id=aircraft.flight_id,
            severity=EventSeverity.INFO,
        )

    def _activate_holding_loop(
        self, aircraft: Aircraft, log_entry: bool = False
    ) -> None:
        pattern = self._holding_pattern_for(aircraft)
        route = self._route_from_current_position(aircraft, pattern[1:])
        aircraft.configure_route(route)
        aircraft.configure_altitude_profile(
            (
                (0, aircraft.altitude_ft),
                (
                    len(route) - 1,
                    aircraft.holding_altitude_ft,
                ),
            )
        )
        aircraft.holding_altitude_transition_pending = (
            abs(aircraft.altitude_ft - aircraft.holding_altitude_ft) > 1e-9
        )
        aircraft.status = HOLDING
        aircraft.flight_phase = FlightPhase.HOLDING
        aircraft.route_mode = RouteMode.HOLDING
        if log_entry:
            self._record_event(
                EventCategory.AIRCRAFT,
                "Aircraft entered holding pattern.",
                pnr_id=aircraft.flight_id,
                severity=EventSeverity.INFO,
            )

    def _go_around_route(
        self,
        aircraft: Aircraft,
        occupied_runway: Runway | None,
    ) -> tuple[tuple[Position, ...], int, int | None]:
        pattern = self._holding_pattern_for(aircraft)
        checkpoint_x, checkpoint_y = aircraft.approach_checkpoint
        escape_point = (
            max(-0.75, min(0.75, checkpoint_x - 0.18)),
            max(-0.75, min(0.75, checkpoint_y - 0.22)),
        )
        if escape_point == pattern[0]:
            escape_point = (
                max(-0.75, min(0.75, checkpoint_x - 0.26)),
                max(-0.75, min(0.75, checkpoint_y - 0.18)),
            )
        waypoints: list[Position] = []
        if occupied_runway is not None:
            waypoints.append(occupied_runway.landing_vacate_point)
        waypoints.extend((escape_point, pattern[0]))
        route = self._route_from_current_position(aircraft, waypoints)
        escape_index = route.index(escape_point)
        if not 1 <= escape_index < len(route) - 1:
            raise ValueError("Go-around route must include an escape climb waypoint")
        clearance_index = (
            route.index(occupied_runway.landing_vacate_point)
            if occupied_runway is not None
            else None
        )
        if clearance_index is not None and clearance_index == 0:
            raise ValueError("Go-around must reach the runway vacate point")
        return route, escape_index, clearance_index

    def shutdown(self) -> None:
        """Cancel pending UI work before the owning Tk root is destroyed."""
        self._shutdown = True
        self._cancel_pending_tick()

    def _schedule_tick(self) -> None:
        if (
            not self._shutdown
            and self.state is SimulationState.RUNNING
            and self._after_id is None
        ):
            self._after_id = self._schedule_after(self.interval_ms, self._update)

    def _cancel_pending_tick(self) -> None:
        if self._after_id is not None:
            after_id = self._after_id
            self._after_id = None
            self._cancel_after(after_id)

    def _update(self) -> None:
        self._after_id = None
        if self._shutdown or self.state is not SimulationState.RUNNING:
            return

        elapsed_seconds = self.interval_ms / 1000
        self._advance_runway_clearings(elapsed_seconds)
        for aircraft in self.aircraft:
            if aircraft.flight_type == "Departure":
                if aircraft.status == CLEARED_FOR_TAKEOFF:
                    self._start_takeoff(aircraft)
                if aircraft.status != TAKING_OFF:
                    continue
            elif aircraft.flight_type == "Arrival":
                if aircraft.status == CLEARED_TO_LAND:
                    self._start_landing(aircraft)
                elif aircraft.status in (AWAITING_LANDING_CLEARANCE, LANDED):
                    continue
                elif aircraft.flight_phase is FlightPhase.APPROACH:
                    self._set_flight_phase(
                        aircraft,
                        FlightPhase.DESCENT,
                        "Aircraft began descent.",
                    )

            was_complete = aircraft.route_complete
            was_taking_off = aircraft.status == TAKING_OFF
            was_landing = aircraft.status == LANDING
            was_approaching = (
                aircraft.flight_type == "Arrival"
                and aircraft.route_mode is RouteMode.APPROACH
            )
            was_entering_hold = aircraft.status == ENTERING_HOLD
            was_holding = aircraft.status == HOLDING
            was_exiting_hold = aircraft.status == EXITING_HOLD
            was_going_around = aircraft.status == GOING_AROUND
            aircraft.advance(elapsed_seconds)
            if (
                was_landing
                and aircraft.flight_phase is FlightPhase.FINAL_APPROACH
                and aircraft.touchdown_waypoint_index is not None
                and aircraft.has_reached_waypoint(aircraft.touchdown_waypoint_index)
            ):
                self._set_flight_phase(
                    aircraft,
                    FlightPhase.GROUND,
                    "Aircraft touched down.",
                )
            if was_taking_off:
                clearance_index = aircraft.runway_clearance_waypoint_index
                if clearance_index is None:
                    raise RuntimeError(
                        f"Takeoff route has no runway-clearance point for {aircraft.flight_id}"
                    )
                if (
                    aircraft.has_reached_waypoint(clearance_index)
                    and (runway := self._runway_assigned_to(aircraft)) is not None
                    and runway.status is RunwayStatus.OCCUPIED
                ):
                    self._begin_runway_clearing(runway, aircraft)
                liftoff_index = aircraft.liftoff_waypoint_index
                if liftoff_index is None:
                    raise RuntimeError(
                        f"Takeoff route has no liftoff point for {aircraft.flight_id}"
                    )
                if (
                    aircraft.flight_phase is FlightPhase.TAKEOFF_ROLL
                    and aircraft.has_reached_waypoint(liftoff_index)
                ):
                    self._set_flight_phase(
                        aircraft,
                        FlightPhase.CLIMB,
                        "Aircraft lifted off and began climb.",
                    )
                if not was_complete and aircraft.route_complete:
                    aircraft.status = DEPARTED
                    aircraft.flight_phase = FlightPhase.DEPARTED
                    self._record_event(
                        EventCategory.AIRCRAFT,
                        "Aircraft departed.",
                        pnr_id=aircraft.flight_id,
                        severity=EventSeverity.SUCCESS,
                    )
            elif was_landing and not was_complete and aircraft.route_complete:
                runway = self._runway_assigned_to(aircraft)
                if runway is None:
                    raise RuntimeError(
                        f"Landing runway reservation lost for {aircraft.flight_id}"
                    )
                aircraft.status = LANDED
                aircraft.altitude_ft = 0.0
                aircraft.flight_phase = FlightPhase.LANDED
                self._record_event(
                    EventCategory.AIRCRAFT,
                    "Landing completed.",
                    pnr_id=aircraft.flight_id,
                    severity=EventSeverity.SUCCESS,
                )
                self._begin_runway_clearing(runway, aircraft)
            elif was_entering_hold and aircraft.route_complete:
                self._activate_holding_loop(aircraft, log_entry=True)
            elif was_holding and aircraft.route_complete:
                aircraft.holding_circuit_count += 1
                aircraft.holding_altitude_transition_pending = False
                self._record_event(
                    EventCategory.AIRCRAFT,
                    f"Holding circuit {aircraft.holding_circuit_count} completed.",
                    pnr_id=aircraft.flight_id,
                    severity=EventSeverity.INFO,
                )
                self._activate_holding_loop(aircraft)
            elif was_exiting_hold and aircraft.route_complete:
                runway = self._runway_assigned_to(aircraft)
                if runway is None or runway.status is not RunwayStatus.RESERVED:
                    raise RuntimeError(
                        f"Landing runway reservation lost for {aircraft.flight_id}"
                    )
                landing_route = self._landing_route(aircraft, runway)
                self._configure_final_approach(aircraft, landing_route)
                aircraft.status = CLEARED_TO_LAND
                aircraft.flight_phase = FlightPhase.DESCENT
                aircraft.route_mode = RouteMode.FINAL_APPROACH
                self._record_event(
                    EventCategory.AIRCRAFT,
                    "Aircraft exited holding pattern.",
                    pnr_id=aircraft.flight_id,
                    severity=EventSeverity.INFO,
                )
                self._record_event(
                    EventCategory.AIRCRAFT,
                    "Aircraft rejoined approach.",
                    pnr_id=aircraft.flight_id,
                    severity=EventSeverity.INFO,
                )
            elif was_going_around:
                clearance_index = aircraft.runway_clearance_waypoint_index
                if (
                    clearance_index is not None
                    and aircraft.has_reached_waypoint(clearance_index)
                    and (runway := self._runway_assigned_to(aircraft)) is not None
                    and runway.status is RunwayStatus.OCCUPIED
                ):
                    self._begin_runway_clearing(runway, aircraft)
                if aircraft.route_complete:
                    self._record_event(
                        EventCategory.AIRCRAFT,
                        "Aircraft rejoined holding pattern.",
                        pnr_id=aircraft.flight_id,
                        severity=EventSeverity.INFO,
                    )
                    self._activate_holding_loop(aircraft)
            elif (
                was_approaching
                and not was_complete
                and aircraft.route_complete
            ):
                self._record_event(
                    EventCategory.ATC,
                    "Reached approach checkpoint; entering holding without clearance.",
                    pnr_id=aircraft.flight_id,
                    severity=EventSeverity.INFO,
                )
                self._begin_holding(aircraft)

        if self._all_operations_complete():
            self.state = SimulationState.COMPLETED
            self._record_event(
                EventCategory.SIMULATION,
                "All aircraft operations and runway clearances completed.",
                severity=EventSeverity.SUCCESS,
            )
            self._notify()
            return

        self._notify()
        self._schedule_tick()

    def _start_takeoff(self, aircraft: Aircraft) -> None:
        runway = self._runway_assigned_to(aircraft)
        if runway is None or not runway.occupy(
            aircraft.flight_id, RunwayOperation.TAKEOFF
        ):
            raise RuntimeError(
                f"Takeoff runway reservation lost for {aircraft.flight_id}"
            )
        aircraft.status = TAKING_OFF
        aircraft.flight_phase = FlightPhase.TAKEOFF_ROLL
        self._record_event(
            EventCategory.RUNWAY,
            f"Runway {runway.runway_id} occupied for takeoff.",
            pnr_id=aircraft.flight_id,
            severity=EventSeverity.INFO,
        )
        self._record_event(
            EventCategory.ATC,
            f"Takeoff initiated on runway {runway.runway_id}.",
            pnr_id=aircraft.flight_id,
            severity=EventSeverity.SUCCESS,
        )

    def _start_landing(self, aircraft: Aircraft) -> None:
        runway = self._runway_assigned_to(aircraft)
        if runway is None or not runway.occupy(
            aircraft.flight_id, RunwayOperation.LANDING
        ):
            raise RuntimeError(
                f"Landing runway reservation lost for {aircraft.flight_id}"
            )
        aircraft.status = LANDING
        aircraft.flight_phase = FlightPhase.FINAL_APPROACH
        self._record_event(
            EventCategory.RUNWAY,
            f"Runway {runway.runway_id} occupied for landing.",
            pnr_id=aircraft.flight_id,
            severity=EventSeverity.INFO,
        )
        self._record_event(
            EventCategory.ATC,
            f"Landing sequence started on runway {runway.runway_id}.",
            pnr_id=aircraft.flight_id,
            severity=EventSeverity.SUCCESS,
        )
        self._record_event(
            EventCategory.AIRCRAFT,
            "Aircraft began final approach.",
            pnr_id=aircraft.flight_id,
            severity=EventSeverity.INFO,
        )

    def _configure_initial_altitude_profiles(self) -> None:
        for aircraft in self.aircraft:
            if aircraft.flight_type != "Arrival":
                continue
            aircraft.holding_altitude_ft = self.holding_altitude_ft
            if aircraft.status == AWAITING_LANDING_CLEARANCE:
                aircraft.flight_phase = FlightPhase.LEVEL
                continue
            if len(aircraft.route_waypoints) < 2:
                continue
            if aircraft.initial_altitude_ft <= self.arrival_checkpoint_altitude_ft:
                raise ValueError(
                    f"Arrival {aircraft.flight_id} must start above its approach checkpoint"
                )
            aircraft.configure_altitude_profile(
                (
                    (0, aircraft.initial_altitude_ft),
                    (
                        len(aircraft.route_waypoints) - 1,
                        self.arrival_checkpoint_altitude_ft,
                    ),
                )
            )
            aircraft.flight_phase = FlightPhase.APPROACH

    def _set_flight_phase(
        self,
        aircraft: Aircraft,
        phase: FlightPhase,
        event_message: str | None = None,
    ) -> None:
        if aircraft.flight_phase is phase:
            return
        aircraft.flight_phase = phase
        if event_message is not None:
            self._record_event(
                EventCategory.AIRCRAFT,
                event_message,
                pnr_id=aircraft.flight_id,
                severity=EventSeverity.INFO,
            )

    def _begin_runway_clearing(
        self,
        runway: Runway,
        aircraft: Aircraft,
    ) -> None:
        if not runway.begin_clearing(
            aircraft.flight_id, self.runway_clearing_delay_seconds
        ):
            raise RuntimeError(
                f"Runway {runway.runway_id} could not enter clearing for {aircraft.flight_id}"
            )
        self._record_event(
            EventCategory.AIRCRAFT,
            "Aircraft cleared runway.",
            pnr_id=aircraft.flight_id,
            severity=EventSeverity.SUCCESS,
        )
        self._record_event(
            EventCategory.RUNWAY,
            f"Runway {runway.runway_id} entering clearing phase.",
            pnr_id=aircraft.flight_id,
            severity=EventSeverity.INFO,
        )

    def _advance_runway_clearings(self, elapsed_seconds: float) -> None:
        for runway in self.runways:
            pnr_id = runway.assigned_aircraft_id
            closure_pending = (
                runway.operational_status
                is RunwayOperationalStatus.CLOSURE_PENDING
            )
            closure_reason = runway.closure_reason
            if runway.advance_clearing(elapsed_seconds):
                aircraft = next(
                    (
                        flight
                        for flight in self.aircraft
                        if flight.flight_id == pnr_id
                    ),
                    None,
                )
                if aircraft is not None and aircraft.runway_release_pending:
                    aircraft.assigned_runway = None
                    aircraft.runway_release_pending = False
                self._record_event(
                    EventCategory.RUNWAY,
                    f"Runway {runway.runway_id} released.",
                    pnr_id=pnr_id,
                    severity=EventSeverity.SUCCESS,
                )
                if closure_pending:
                    self._record_event(
                        EventCategory.RUNWAY,
                        f"Runway {runway.runway_id} closed after clearing: "
                        f"{closure_reason}.",
                        pnr_id=pnr_id,
                        severity=EventSeverity.SUCCESS,
                    )

    def _all_operations_complete(self) -> bool:
        aircraft_complete = True
        for aircraft in self.aircraft:
            if not aircraft.route_complete:
                aircraft_complete = False
                break
            if aircraft.flight_type == "Departure" and aircraft.status != DEPARTED:
                aircraft_complete = False
                break
            if aircraft.flight_type == "Arrival" and aircraft.status != LANDED:
                aircraft_complete = False
                break
        runways_clear = all(
            runway.status not in (
                RunwayStatus.RESERVED,
                RunwayStatus.OCCUPIED,
                RunwayStatus.CLEARING,
            )
            for runway in self.runways
        )
        return aircraft_complete and runways_clear

    def _runway_assigned_to(self, aircraft: Aircraft) -> Runway | None:
        return next(
            (
                runway
                for runway in self.runways
                if runway.runway_id == aircraft.assigned_runway
                and runway.assigned_aircraft_id == aircraft.flight_id
            ),
            None,
        )

    def _find_runway(self, runway_id: str | None) -> Runway | None:
        return next(
            (runway for runway in self.runways if runway.runway_id == runway_id),
            None,
        )

    def _require_runway(self, runway_id: str) -> Runway:
        runway = self._find_runway(runway_id)
        if runway is None:
            raise ValueError(f"Unknown runway: {runway_id}")
        return runway

    def _notify(self) -> None:
        if self._on_change is not None:
            self._on_change(self.state)
        for listener in tuple(self._listeners):
            if listener in self._listeners:
                listener(self)

    def _record_event(
        self,
        category: EventCategory,
        message: str,
        pnr_id: str | None = None,
        severity: EventSeverity = EventSeverity.INFO,
    ) -> None:
        self.activity_log.record(category, message, pnr_id, severity)

    def _reject_holding_operation(self, aircraft: Aircraft, reason: str) -> bool:
        key = (aircraft.flight_id, reason)
        if key not in self._reported_holding_rejections:
            self._reported_holding_rejections.add(key)
            self._record_event(
                EventCategory.ATC,
                reason,
                pnr_id=aircraft.flight_id,
                severity=EventSeverity.WARNING,
            )
            self._notify()
        return False

    def _reject_go_around(self, aircraft: Aircraft, reason: str) -> bool:
        key = (aircraft.flight_id, reason)
        if key not in self._reported_go_around_rejections:
            self._reported_go_around_rejections.add(key)
            self._record_event(
                EventCategory.ATC,
                reason,
                pnr_id=aircraft.flight_id,
                severity=EventSeverity.WARNING,
            )
            self._notify()
        return False

    def _reject_landing_clearance(self, aircraft: Aircraft, reason: str) -> bool:
        key = (aircraft.flight_id, reason)
        if key not in self._reported_clearance_rejections:
            self._reported_clearance_rejections.add(key)
            self._record_event(
                EventCategory.ATC,
                reason,
                pnr_id=aircraft.flight_id,
                severity=EventSeverity.WARNING,
            )
            self._notify()
        return False

    def _reject_takeoff_clearance(self, aircraft: Aircraft, reason: str) -> bool:
        key = (aircraft.flight_id, reason)
        if key not in self._reported_takeoff_rejections:
            self._reported_takeoff_rejections.add(key)
            self._record_event(
                EventCategory.ATC,
                reason,
                pnr_id=aircraft.flight_id,
                severity=EventSeverity.WARNING,
            )
            self._notify()
        return False

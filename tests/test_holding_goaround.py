"""Holding-pattern and go-around behavior tests."""

from collections.abc import Callable
from copy import deepcopy
from math import hypot
import unittest

from core.aircraft import (
    CLEARED_TO_LAND,
    ENTERING_HOLD,
    EXITING_HOLD,
    GOING_AROUND,
    HOLDING,
    LANDED,
    LANDING,
    Aircraft,
    FlightPhase,
    RouteMode,
)
from core.runway import RunwayOperation, RunwayStatus
from core.simulation import SimulationController, SimulationState
from data.sample_flights import SAMPLE_FLIGHTS, SAMPLE_RUNWAYS


class HoldingGoAroundTests(unittest.TestCase):
    def setUp(self) -> None:
        self.aircraft = deepcopy(SAMPLE_FLIGHTS)
        self.runways = deepcopy(SAMPLE_RUNWAYS)
        self.pending_ticks: dict[str, Callable[[], None]] = {}
        self._next_tick_id = 0
        self.controller = SimulationController(
            self.aircraft,
            self._schedule,
            self._cancel,
            interval_ms=500,
            runways=self.runways,
            runway_clearing_delay_seconds=1.0,
        )

    def _schedule(
        self, _delay_ms: int, callback: Callable[[], None]
    ) -> str:
        self._next_tick_id += 1
        token = str(self._next_tick_id)
        self.pending_ticks[token] = callback
        return token

    def _cancel(self, token: str) -> None:
        self.pending_ticks.pop(token, None)

    def _tick(self) -> None:
        self.assertTrue(self.pending_ticks, "Expected a scheduled simulation tick")
        token = next(iter(self.pending_ticks))
        callback = self.pending_ticks.pop(token)
        callback()

    def _run_until(self, predicate, limit: int = 600) -> None:
        for _ in range(limit):
            if predicate():
                return
            self._tick()
        self.fail("Simulation did not reach the expected holding or approach state")

    def _flight(self, flight_id: str) -> Aircraft:
        return next(flight for flight in self.aircraft if flight.flight_id == flight_id)

    def _reach_holding(self, flight_id: str = "AAL204") -> Aircraft:
        aircraft = self._flight(flight_id)
        if aircraft.status != HOLDING:
            self.controller.start()
            self._run_until(lambda: aircraft.status == HOLDING)
        return aircraft

    def test_arrival_enters_closed_hold_and_circuits_without_teleporting(self) -> None:
        aircraft = self._reach_holding()
        self.assertEqual(aircraft.route_mode, RouteMode.HOLDING)
        self.assertEqual(aircraft.flight_phase, FlightPhase.HOLDING)
        self.assertEqual(aircraft.altitude_ft, self.controller.holding_altitude_ft)
        self.assertEqual(aircraft.route_waypoints[0], aircraft.route_waypoints[-1])
        self.assertTrue(
            all(
                abs(x) <= 1 and abs(y) <= 1 and hypot(x, y) <= 1
                for x, y in aircraft.route_waypoints
            )
        )

        self.controller.pause()
        paused_position = aircraft.current_position
        paused_altitude = aircraft.altitude_ft
        self.assertFalse(self.pending_ticks)
        self.controller.resume()
        self._tick()
        self.assertNotEqual(aircraft.current_position, paused_position)
        self.assertEqual(aircraft.altitude_ft, paused_altitude)

        self._run_until(lambda: aircraft.holding_circuit_count == 1)
        self.assertEqual(aircraft.status, HOLDING)
        self.assertEqual(aircraft.current_position, aircraft.route_waypoints[0])
        self.assertEqual(aircraft.route_waypoints[0], aircraft.route_waypoints[-1])
        self.assertEqual(aircraft.altitude_ft, paused_altitude)
        self.assertEqual(
            sum(
                event.message == "Aircraft entered holding pattern."
                and event.pnr_id == aircraft.flight_id
                for event in self.controller.activity_log.events
            ),
            1,
        )

    def test_manual_holding_is_validated_and_joins_without_position_jump(self) -> None:
        self.controller.start()
        arrival = self._flight("AAL204")
        departure = self._flight("DAL731")
        self._tick()
        position_before_entry = arrival.current_position
        self.assertTrue(self.controller.can_enter_holding(arrival.flight_id))
        self.assertTrue(self.controller.enter_holding(arrival.flight_id))
        self.assertEqual(arrival.status, ENTERING_HOLD)
        self.assertEqual(arrival.route_waypoints[0], position_before_entry)
        self.assertFalse(self.controller.enter_holding(arrival.flight_id))
        self.assertFalse(self.controller.enter_holding(departure.flight_id))

        self._run_until(lambda: arrival.status == HOLDING)
        self.assertEqual(arrival.route_mode, RouteMode.HOLDING)
        self.assertEqual(arrival.altitude_ft, self.controller.holding_altitude_ft)

    def test_holding_aircraft_waits_when_no_runway_is_available(self) -> None:
        aircraft = self._reach_holding()
        for runway in self.runways:
            self.assertTrue(
                runway.reserve(f"BLOCK-{runway.runway_id}", RunwayOperation.LANDING)
            )
        held_route = aircraft.route_waypoints
        held_position = aircraft.current_position

        self.assertFalse(self.controller.can_grant_landing_clearance(aircraft.flight_id))
        self.assertFalse(self.controller.grant_landing_clearance(aircraft.flight_id))
        self.assertEqual(aircraft.status, HOLDING)
        self.assertEqual(aircraft.route_waypoints, held_route)
        self.assertEqual(aircraft.current_position, held_position)

    def test_holding_clearance_exits_smoothly_and_resumes_descent(self) -> None:
        aircraft = self._reach_holding()
        self.assertTrue(self.controller.grant_landing_clearance(aircraft.flight_id))
        self.assertEqual(aircraft.status, EXITING_HOLD)
        self.assertEqual(self.runways[0].status, RunwayStatus.RESERVED)
        self.assertEqual(aircraft.route_mode, RouteMode.HOLD_EXIT)
        self.assertEqual(aircraft.route_waypoints[0], aircraft.current_position)

        self._run_until(lambda: aircraft.status == CLEARED_TO_LAND)
        self.assertEqual(aircraft.route_mode, RouteMode.FINAL_APPROACH)
        self.assertEqual(aircraft.altitude_ft, self.controller.holding_altitude_ft)
        self._tick()
        self.assertEqual(aircraft.status, LANDING)
        self.assertLess(aircraft.altitude_ft, self.controller.holding_altitude_ft)
        self._run_until(lambda: aircraft.status == LANDED)
        self.assertEqual(aircraft.altitude_ft, 0.0)

    def test_two_arrivals_hold_independently_while_runway_is_reused(self) -> None:
        self.controller.start()
        aal = self._flight("AAL204")
        ual = self._flight("UAL518")
        self._run_until(lambda: aal.status == HOLDING and ual.status == HOLDING)
        self.assertNotEqual(aal.route_waypoints, ual.route_waypoints)

        self.assertTrue(self.controller.grant_landing_clearance(aal.flight_id))
        self._run_until(lambda: aal.status == LANDED)
        self.assertEqual(ual.status, HOLDING)
        self._run_until(lambda: self.runways[0].status is RunwayStatus.AVAILABLE)
        self.assertTrue(self.controller.grant_landing_clearance(ual.flight_id))
        self._run_until(lambda: ual.status == LANDED)
        self.assertEqual(self.runways[0].status, RunwayStatus.CLEARING)
        self._run_until(lambda: self.runways[0].status is RunwayStatus.AVAILABLE)

        messages = [event.message for event in self.controller.activity_log.events]
        self.assertIn("Landing clearance granted while holding for runway 27L.", messages)
        self.assertIn("Aircraft exited holding pattern.", messages)
        self.assertIn("Aircraft rejoined approach.", messages)

    def test_go_around_cancels_reserved_landing_and_rejoins_holding(self) -> None:
        aircraft = self._reach_holding()
        self.assertTrue(self.controller.grant_landing_clearance(aircraft.flight_id))
        altitude_at_go_around = aircraft.altitude_ft
        self.assertEqual(self.runways[0].status, RunwayStatus.RESERVED)

        self.assertTrue(self.controller.go_around(aircraft.flight_id))
        self.assertEqual(self.runways[0].status, RunwayStatus.AVAILABLE)
        self.assertIsNone(aircraft.assigned_runway)
        self.assertEqual(aircraft.status, GOING_AROUND)
        self.assertEqual(aircraft.route_mode, RouteMode.GO_AROUND)
        self.assertEqual(aircraft.altitude_ft, altitude_at_go_around)
        self.assertGreater(aircraft.target_altitude_ft, altitude_at_go_around)
        self.assertEqual(aircraft.route_waypoints[0], aircraft.current_position)
        self._run_until(lambda: aircraft.status == HOLDING)
        self.assertEqual(aircraft.go_around_count, 1)
        self.assertGreaterEqual(aircraft.altitude_ft, self.controller.holding_altitude_ft)

        self.assertTrue(self.controller.grant_landing_clearance(aircraft.flight_id))
        self._run_until(lambda: aircraft.status == CLEARED_TO_LAND)
        self._tick()
        self._run_until(lambda: aircraft.status == LANDED)
        self._run_until(lambda: self.runways[0].status is RunwayStatus.AVAILABLE)

        messages = [event.message for event in self.controller.activity_log.events]
        self.assertEqual(messages.count("Go-around initiated."), 1)
        self.assertEqual(messages.count("Aircraft rejoined holding pattern."), 1)
        self.assertEqual(
            messages.count("Landing reservation on runway 27L cancelled."),
            1,
        )

    def test_go_around_keeps_occupied_runway_until_aircraft_vacates(self) -> None:
        aircraft = self._reach_holding()
        self.assertTrue(self.controller.grant_landing_clearance(aircraft.flight_id))
        self._run_until(lambda: aircraft.status == CLEARED_TO_LAND)
        self._tick()
        self.assertEqual(aircraft.status, LANDING)
        self.assertEqual(self.runways[0].status, RunwayStatus.OCCUPIED)

        self.assertTrue(self.controller.go_around(aircraft.flight_id))
        self.assertEqual(aircraft.status, GOING_AROUND)
        self.assertEqual(self.runways[0].status, RunwayStatus.OCCUPIED)
        self.assertEqual(self.runways[0].assigned_aircraft_id, aircraft.flight_id)
        self.assertEqual(aircraft.route_waypoints[0], aircraft.current_position)

        self._run_until(lambda: self.runways[0].status is RunwayStatus.CLEARING)
        self.assertEqual(self.runways[0].assigned_aircraft_id, aircraft.flight_id)
        self._run_until(lambda: aircraft.status == HOLDING)
        self._run_until(lambda: self.runways[0].status is RunwayStatus.AVAILABLE)
        self.assertIsNone(aircraft.assigned_runway)
        self.assertEqual(aircraft.status, HOLDING)

    def test_go_around_after_touchdown_is_rejected_without_releasing_runway(self) -> None:
        aircraft = self._reach_holding()
        self.assertTrue(self.controller.grant_landing_clearance(aircraft.flight_id))
        self._run_until(lambda: aircraft.status == CLEARED_TO_LAND)
        self._tick()
        self._run_until(lambda: aircraft.flight_phase is FlightPhase.GROUND)
        self.assertEqual(self.runways[0].status, RunwayStatus.OCCUPIED)

        self.assertFalse(self.controller.can_go_around(aircraft.flight_id))
        self.assertFalse(self.controller.go_around(aircraft.flight_id))
        self.assertEqual(self.runways[0].status, RunwayStatus.OCCUPIED)
        self.assertEqual(aircraft.status, LANDING)

    def test_reset_restores_initial_routes_and_clears_holding_goaround_state(self) -> None:
        aircraft = self._reach_holding()
        self.assertTrue(self.controller.grant_landing_clearance(aircraft.flight_id))
        self.assertTrue(self.controller.go_around(aircraft.flight_id))
        self._run_until(lambda: aircraft.status == HOLDING)
        self.controller.reset()

        initial = next(flight for flight in SAMPLE_FLIGHTS if flight.flight_id == aircraft.flight_id)
        self.assertEqual(aircraft.route_waypoints, initial.route_waypoints)
        self.assertEqual(aircraft.current_position, aircraft.initial_position)
        self.assertEqual(aircraft.altitude_ft, aircraft.initial_altitude_ft)
        self.assertEqual(aircraft.route_mode, RouteMode.APPROACH)
        self.assertEqual(aircraft.holding_circuit_count, 0)
        self.assertEqual(aircraft.go_around_count, 0)
        self.assertEqual(aircraft.status, initial.status)
        self.assertTrue(all(runway.available for runway in self.runways))
        self.assertFalse(self.pending_ticks)
        self.assertEqual(self.controller.state, SimulationState.READY)

    def test_reset_during_runway_clearing_restores_all_initial_state(self) -> None:
        aircraft = self._reach_holding()
        self.assertTrue(self.controller.grant_landing_clearance(aircraft.flight_id))
        self._run_until(lambda: aircraft.status == CLEARED_TO_LAND)
        self._tick()
        self._run_until(lambda: aircraft.status == LANDED)
        self.assertEqual(self.runways[0].status, RunwayStatus.CLEARING)
        self._tick()
        self.assertEqual(self.runways[0].status, RunwayStatus.CLEARING)

        self.controller.reset()
        initial = next(flight for flight in SAMPLE_FLIGHTS if flight.flight_id == aircraft.flight_id)
        self.assertTrue(all(runway.available for runway in self.runways))
        self.assertEqual(aircraft.status, initial.status)
        self.assertEqual(aircraft.current_position, aircraft.initial_position)
        self.assertFalse(self.pending_ticks)


if __name__ == "__main__":
    unittest.main()

"""Tests for runway routing, clearing countdowns, and automatic release."""

from copy import deepcopy
import unittest

from core.aircraft import (
    CLEARED_TO_LAND,
    DEPARTED,
    HOLDING,
    LANDED,
    LANDING,
    TAKING_OFF,
    RouteMode,
)
from core.runway import RunwayStatus
from core.simulation import SimulationController, SimulationState
from data.sample_flights import SAMPLE_FLIGHTS, SAMPLE_RUNWAYS


class RunwayCoordinationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.aircraft = deepcopy(SAMPLE_FLIGHTS)
        self.runways = deepcopy(SAMPLE_RUNWAYS)
        self.pending_ticks = {}
        self._next_tick_id = 0
        self.controller = SimulationController(
            self.aircraft,
            self._schedule,
            self._cancel,
            interval_ms=500,
            runways=self.runways,
            runway_clearing_delay_seconds=1.0,
        )

    def _schedule(self, _delay_ms, callback):
        self._next_tick_id += 1
        token = str(self._next_tick_id)
        self.pending_ticks[token] = callback
        return token

    def _cancel(self, token):
        self.pending_ticks.pop(token, None)

    def _tick(self) -> None:
        self.assertTrue(self.pending_ticks, "Expected a scheduled simulation tick")
        token = next(iter(self.pending_ticks))
        callback = self.pending_ticks.pop(token)
        callback()

    def _flight(self, flight_id):
        return next(flight for flight in self.aircraft if flight.flight_id == flight_id)

    def _run_until(self, predicate, limit=200):
        for _ in range(limit):
            if predicate():
                return
            self._tick()
        self.fail("Simulation did not reach the expected runway state")

    def test_departure_clears_runway_before_route_completion_and_releases_after_delay(self):
        dal = self._flight("DAL731")
        jbu = self._flight("JBU402")
        initial_position = dal.current_position

        self.controller.start()
        self.assertTrue(self.controller.grant_takeoff_clearance(dal.flight_id))
        self.assertTrue(self.controller.grant_takeoff_clearance(jbu.flight_id))
        self.assertEqual(dal.assigned_runway, "27L")
        self.assertEqual(jbu.assigned_runway, "27R")
        self.assertEqual(dal.route_waypoints[0], initial_position)
        self.assertIn(self.runways[0].centerline[-1], dal.route_waypoints)
        self.assertIn(self.runways[1].centerline[-1], jbu.route_waypoints)
        self.assertEqual(
            dal.route_waypoints[dal.runway_clearance_waypoint_index],
            self.runways[0].centerline[-1],
        )

        self._tick()
        self.assertEqual(self.runways[0].status, RunwayStatus.OCCUPIED)
        self.assertEqual(self.runways[1].status, RunwayStatus.OCCUPIED)
        self.assertEqual(dal.status, TAKING_OFF)
        self.assertEqual(jbu.status, TAKING_OFF)

        self._run_until(lambda: self.runways[0].status is RunwayStatus.CLEARING)
        self.assertEqual(dal.status, TAKING_OFF)
        self.assertFalse(dal.route_complete)
        self.assertEqual(self.runways[0].remaining_clearing_seconds, 1.0)
        position_at_clearance = dal.current_position

        self.controller.pause()
        self.assertFalse(self.pending_ticks)
        self.assertEqual(self.runways[0].remaining_clearing_seconds, 1.0)
        self.controller.resume()
        self._tick()
        self.assertEqual(self.runways[0].remaining_clearing_seconds, 0.5)
        self.assertNotEqual(dal.current_position, position_at_clearance)

        self.controller.pause()
        self.assertEqual(self.runways[0].remaining_clearing_seconds, 0.5)
        self.controller.resume()
        self._tick()
        self.assertEqual(self.runways[0].status, RunwayStatus.AVAILABLE)
        self.assertIsNone(self.runways[0].assigned_aircraft_id)
        self.assertIsNone(self.runways[0].operation_type)
        self.assertEqual(self.runways[0].remaining_clearing_seconds, 0.0)
        self.assertEqual(dal.status, TAKING_OFF)
        self.assertFalse(dal.route_complete)

        release_events = [
            event
            for event in self.controller.activity_log.events
            if event.message == "Runway 27L released."
        ]
        self.assertEqual(len(release_events), 1)

    def test_landing_vacates_runway_and_released_resource_can_be_reassigned(self):
        aal = self._flight("AAL204")
        ual = self._flight("UAL518")
        self.controller.start()
        self._run_until(lambda: aal.status == HOLDING)
        self.assertEqual(aal.route_mode, RouteMode.HOLDING)

        self.controller.pause()
        position_at_clearance = aal.current_position
        self.assertTrue(self.controller.grant_landing_clearance(aal.flight_id))
        self.assertNotEqual(aal.status, CLEARED_TO_LAND)
        self.assertEqual(aal.route_waypoints[0], position_at_clearance)
        self.assertFalse(self.pending_ticks)

        self.controller.resume()
        self._run_until(lambda: aal.status == CLEARED_TO_LAND)
        self.assertEqual(aal.route_waypoints[-1], self.runways[0].landing_vacate_point)
        self._tick()
        self.assertEqual(aal.status, LANDING)
        self.assertEqual(self.runways[0].status, RunwayStatus.OCCUPIED)
        self._run_until(lambda: aal.status == LANDED)
        self.assertEqual(self.runways[0].status, RunwayStatus.CLEARING)
        self.assertEqual(aal.current_position, self.runways[0].landing_vacate_point)

        self.controller.pause()
        self.assertEqual(self.runways[0].remaining_clearing_seconds, 1.0)
        self.controller.resume()
        self._tick()
        self.assertEqual(self.runways[0].remaining_clearing_seconds, 0.5)
        self._tick()
        self.assertEqual(self.runways[0].status, RunwayStatus.AVAILABLE)

        self._run_until(lambda: ual.status == HOLDING)
        self.assertTrue(self.controller.grant_landing_clearance(ual.flight_id))
        self.assertEqual(ual.assigned_runway, "27L")
        self.assertEqual(self.runways[0].assigned_aircraft_id, ual.flight_id)

        messages = [event.message for event in self.controller.activity_log.events]
        self.assertIn("Landing sequence started on runway 27L.", messages)
        self.assertIn("Landing completed.", messages)
        self.assertIn("Aircraft cleared runway.", messages)
        self.assertIn("Runway 27L released.", messages)

    def test_reset_cancels_clearing_state_and_restores_original_routes(self):
        dal = self._flight("DAL731")
        original_route = dal.route_waypoints
        self.controller.start()
        self.assertTrue(self.controller.grant_takeoff_clearance(dal.flight_id))
        self._tick()
        self._run_until(lambda: self.runways[0].status is RunwayStatus.CLEARING)
        self.controller.reset()

        self.assertEqual(self.controller.state, SimulationState.READY)
        self.assertEqual(dal.current_position, dal.initial_position)
        self.assertEqual(dal.route_waypoints, original_route)
        self.assertTrue(
            all(
                runway.status is RunwayStatus.AVAILABLE
                and runway.remaining_clearing_seconds == 0
                for runway in self.runways
            )
        )
        self.assertFalse(self.pending_ticks)
        self.assertEqual(
            [event.message for event in self.controller.activity_log.events],
            ["Simulation reset."],
        )

    def test_reset_during_active_landing_restores_aircraft_and_runways(self):
        aal = self._flight("AAL204")
        original_route = aal.route_waypoints
        self.controller.start()
        self._run_until(lambda: aal.status == HOLDING)
        self.assertTrue(self.controller.grant_landing_clearance(aal.flight_id))
        self._run_until(lambda: aal.status == CLEARED_TO_LAND)
        self._tick()

        self.assertEqual(aal.status, LANDING)
        self.assertEqual(self.runways[0].status, RunwayStatus.OCCUPIED)
        self.controller.reset()

        self.assertEqual(aal.current_position, aal.initial_position)
        self.assertEqual(aal.route_waypoints, original_route)
        self.assertEqual(aal.status, "Approach")
        self.assertIsNone(aal.assigned_runway)
        self.assertTrue(
            all(
                runway.status is RunwayStatus.AVAILABLE
                and runway.assigned_aircraft_id is None
                and runway.operation_type is None
                and runway.remaining_clearing_seconds == 0
                for runway in self.runways
            )
        )
        self.assertFalse(self.pending_ticks)
        self.assertEqual(
            [event.message for event in self.controller.activity_log.events],
            ["Simulation reset."],
        )

    def test_full_sample_simulation_completes_without_duplicate_transitions(self):
        self.controller.start()
        for _ in range(2000):
            for flight in self.aircraft:
                if self.controller.can_grant_takeoff_clearance(flight.flight_id):
                    self.assertTrue(
                        self.controller.grant_takeoff_clearance(flight.flight_id)
                    )
                if self.controller.can_grant_landing_clearance(flight.flight_id):
                    self.assertTrue(
                        self.controller.grant_landing_clearance(flight.flight_id)
                    )
            self._tick()
            if self.controller.state is SimulationState.COMPLETED:
                break
        else:
            self.fail("Full sample simulation did not complete")

        self.assertEqual(self.controller.state, SimulationState.COMPLETED)
        self.assertTrue(
            all(flight.status in (DEPARTED, LANDED) for flight in self.aircraft)
        )
        self.assertTrue(
            all(runway.status is RunwayStatus.AVAILABLE for runway in self.runways)
        )
        events = self.controller.activity_log.events
        event_ids = [event.event_id for event in events]
        self.assertEqual(event_ids, list(range(1, len(events) + 1)))
        self.assertEqual(
            sum(
                event.message
                == "All aircraft operations and runway clearances completed."
                for event in events
            ),
            1,
        )
        for flight in self.aircraft:
            expected_message = (
                "Landing completed."
                if flight.flight_type == "Arrival"
                else "Aircraft departed."
            )
            self.assertEqual(
                sum(
                    event.message == expected_message
                    and event.pnr_id == flight.flight_id
                    for event in events
                ),
                1,
            )
            transition_messages = (
                ("Aircraft began descent.", "Aircraft began final approach.")
                if flight.flight_type == "Arrival"
                else ("Aircraft lifted off and began climb.",)
            )
            for message in transition_messages:
                self.assertEqual(
                    sum(
                        event.message == message and event.pnr_id == flight.flight_id
                        for event in events
                    ),
                    1,
                    f"{flight.flight_id} should log {message!r} once",
                )


if __name__ == "__main__":
    unittest.main()

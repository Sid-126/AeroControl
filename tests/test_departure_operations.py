"""Focused tests for departure clearance and runway state transitions."""

from copy import deepcopy
import unittest

from core.aircraft import (
    AWAITING_TAKEOFF_CLEARANCE,
    CLEARED_FOR_TAKEOFF,
    DEPARTED,
    HOLDING,
    TAKING_OFF,
)
from core.runway import Runway, RunwayOperation, RunwayStatus
from core.simulation import SimulationController
from data.sample_flights import SAMPLE_FLIGHTS, SAMPLE_RUNWAYS


class SimulationControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.aircraft = deepcopy(SAMPLE_FLIGHTS)
        self.runways = deepcopy(SAMPLE_RUNWAYS)
        self.pending_ticks = {}
        self._next_tick_id = 0
        self.controller = SimulationController(
            self.aircraft,
            self._schedule,
            self._cancel,
            runways=self.runways,
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

    def test_departures_wait_for_clearance_and_use_independent_runways(self) -> None:
        dal = self._flight("DAL731")
        jbu = self._flight("JBU402")
        initial_positions = (dal.current_position, jbu.current_position)

        self.controller.start()
        for _ in range(5):
            self._tick()
        self.assertEqual((dal.current_position, jbu.current_position), initial_positions)
        self.assertEqual(dal.status, AWAITING_TAKEOFF_CLEARANCE)

        self.assertTrue(self.controller.grant_takeoff_clearance(dal.flight_id))
        self.assertEqual(dal.status, CLEARED_FOR_TAKEOFF)
        self.assertEqual(self.runways[0].status, RunwayStatus.RESERVED)
        self.assertEqual(self.runways[0].operation_type, RunwayOperation.TAKEOFF)
        self.assertFalse(self.controller.grant_takeoff_clearance(dal.flight_id))

        self._tick()
        self.assertEqual(dal.status, TAKING_OFF)
        self.assertEqual(self.runways[0].status, RunwayStatus.OCCUPIED)
        self.assertNotEqual(dal.current_position, initial_positions[0])

        self.assertTrue(self.controller.grant_takeoff_clearance(jbu.flight_id))
        self.assertEqual(jbu.assigned_runway, "27R")
        self._tick()
        self.assertEqual(jbu.status, TAKING_OFF)
        self.assertEqual(self.runways[1].status, RunwayStatus.OCCUPIED)
        self.assertEqual(self.runways[0].status, RunwayStatus.OCCUPIED)

        for _ in range(150):
            if dal.status == DEPARTED and jbu.status == DEPARTED:
                break
            self._tick()
        self.assertEqual(dal.status, DEPARTED)
        self.assertEqual(jbu.status, DEPARTED)
        for _ in range(100):
            if all(runway.available for runway in self.runways):
                break
            self._tick()
        self.assertEqual(self.runways[0].status, RunwayStatus.AVAILABLE)
        self.assertEqual(self.runways[1].status, RunwayStatus.AVAILABLE)
        self.assertIsNone(self.runways[0].assigned_aircraft_id)
        self.assertIsNone(self.runways[1].assigned_aircraft_id)
        granted_events = [
            event
            for event in self.controller.activity_log.events
            if event.message.startswith("Takeoff clearance granted")
        ]
        self.assertEqual(len(granted_events), 2)
        release_events = [
            event
            for event in self.controller.activity_log.events
            if event.message.startswith("Runway ") and event.message.endswith(" released.")
        ]
        self.assertEqual(len(release_events), 2)

    def test_paused_clearance_waits_and_reset_restores_state_and_log(self) -> None:
        dal = self._flight("DAL731")
        initial_position = dal.initial_position
        self.controller.start()
        self.controller.pause()
        self.assertTrue(self.controller.grant_takeoff_clearance(dal.flight_id))
        self.assertEqual(dal.status, CLEARED_FOR_TAKEOFF)
        self.assertFalse(self.pending_ticks)

        self.controller.resume()
        self._tick()
        self.assertEqual(dal.status, TAKING_OFF)
        takeoff_position = dal.current_position
        self.controller.pause()
        self.assertEqual(dal.current_position, takeoff_position)

        previous_event_id = self.controller.activity_log.events[-1].event_id
        self.controller.reset()
        self.assertEqual(dal.current_position, initial_position)
        self.assertEqual(dal.status, AWAITING_TAKEOFF_CLEARANCE)
        self.assertTrue(all(runway.available for runway in self.runways))
        self.assertEqual(
            [event.message for event in self.controller.activity_log.events],
            ["Simulation reset."],
        )
        self.assertGreater(
            self.controller.activity_log.events[0].event_id, previous_event_id
        )

    def test_clearance_immediately_followed_by_pause_waits_for_resume(self) -> None:
        dal = self._flight("DAL731")
        initial_position = dal.current_position
        self.controller.start()

        self.assertTrue(self.controller.grant_takeoff_clearance(dal.flight_id))
        self.controller.pause()
        self.assertEqual(dal.current_position, initial_position)
        self.assertEqual(dal.status, CLEARED_FOR_TAKEOFF)
        self.assertEqual(self.runways[0].status, RunwayStatus.RESERVED)
        self.assertFalse(self.pending_ticks)
        self.assertFalse(
            any(
                event.message.startswith("Takeoff initiated")
                for event in self.controller.activity_log.events
            )
        )

        self.controller.resume()
        self._tick()
        self.assertEqual(dal.status, TAKING_OFF)
        self.assertNotEqual(dal.current_position, initial_position)
        self.assertEqual(
            sum(
                event.message.startswith("Takeoff clearance granted")
                for event in self.controller.activity_log.events
            ),
            1,
        )
        self.assertEqual(
            sum(
                event.message.startswith("Takeoff initiated")
                for event in self.controller.activity_log.events
            ),
            1,
        )

    def test_repeated_start_resume_and_pause_do_not_duplicate_ticks_or_events(self) -> None:
        self.controller.start()
        self.controller.start()
        self.controller.resume()
        self.assertEqual(len(self.pending_ticks), 1)

        self.controller.pause()
        self.controller.pause()
        self.assertFalse(self.pending_ticks)
        self.controller.resume()
        self.controller.resume()
        self.controller.start()
        self.assertEqual(len(self.pending_ticks), 1)

        messages = [
            event.message for event in self.controller.activity_log.events
        ]
        self.assertEqual(messages.count("Simulation started."), 1)
        self.assertEqual(messages.count("Simulation paused."), 1)
        self.assertEqual(messages.count("Simulation resumed."), 1)

    def test_landing_clearance_still_reserves_a_runway(self) -> None:
        arrival = self._flight("AAL204")
        self.controller.start()
        for _ in range(250):
            if arrival.status == HOLDING:
                break
            self._tick()

        self.assertEqual(arrival.status, HOLDING)
        self.assertTrue(self.controller.grant_landing_clearance(arrival.flight_id))
        self.assertEqual(self.runways[0].status, RunwayStatus.RESERVED)
        self.assertEqual(self.runways[0].operation_type, RunwayOperation.LANDING)

    def test_arrival_and_conflicting_departure_clearances_are_rejected(self) -> None:
        arrival = self._flight("AAL204")
        dal = self._flight("DAL731")
        jbu = self._flight("JBU402")
        self.controller.runways = [Runway("27L", True)]

        self.assertFalse(self.controller.grant_takeoff_clearance(arrival.flight_id))
        self.assertIsNone(arrival.assigned_runway)
        self.assertTrue(self.controller.grant_takeoff_clearance(dal.flight_id))
        self.assertFalse(self.controller.grant_takeoff_clearance(jbu.flight_id))
        self.assertIsNone(jbu.assigned_runway)
        self.assertEqual(self.controller.runways[0].assigned_aircraft_id, dal.flight_id)
        self.assertTrue(
            any(
                "no operational runway is available" in event.message
                for event in self.controller.activity_log.events
            )
        )


if __name__ == "__main__":
    unittest.main()

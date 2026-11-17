"""Regression tests for shared altitude and flight-phase simulation state."""

from collections.abc import Callable
from copy import deepcopy
import unittest

from core.aircraft import Aircraft, EXITING_HOLD, FlightPhase, HOLDING
from core.simulation import SimulationController
from data.sample_flights import SAMPLE_FLIGHTS, SAMPLE_RUNWAYS


class AltitudeSimulationTests(unittest.TestCase):
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

    def _run_until(self, predicate, limit: int = 300) -> None:
        for _ in range(limit):
            if predicate():
                return
            self._tick()
        self.fail("Simulation did not reach the expected altitude or phase")

    def _flight(self, flight_id: str) -> Aircraft:
        return next(flight for flight in self.aircraft if flight.flight_id == flight_id)

    def test_arrival_descends_to_checkpoint_then_final_approach(self) -> None:
        arrival = self._flight("AAL204")
        initial_altitude = arrival.altitude_ft
        self.controller.start()

        self._tick()
        self.assertLess(arrival.altitude_ft, initial_altitude)
        self.assertEqual(arrival.flight_phase, FlightPhase.DESCENT)
        previous_altitude = arrival.altitude_ft
        self._run_until(lambda: arrival.status == HOLDING)
        self.assertLessEqual(arrival.altitude_ft, previous_altitude)
        self.assertEqual(arrival.altitude_ft, self.controller.arrival_checkpoint_altitude_ft)
        self.assertEqual(arrival.flight_phase, FlightPhase.HOLDING)

        checkpoint_altitude = arrival.altitude_ft
        for _ in range(3):
            self._tick()
        self.assertEqual(arrival.altitude_ft, checkpoint_altitude)

        self.assertTrue(self.controller.grant_landing_clearance(arrival.flight_id))
        self.assertEqual(arrival.status, EXITING_HOLD)
        self.assertEqual(arrival.altitude_ft, checkpoint_altitude)
        self._run_until(lambda: arrival.status == "CLEARED TO LAND")
        self._tick()
        self.assertEqual(arrival.status, "LANDING")
        self.assertEqual(arrival.flight_phase, FlightPhase.FINAL_APPROACH)
        self.assertLess(arrival.altitude_ft, checkpoint_altitude)

        self._run_until(lambda: arrival.flight_phase is FlightPhase.GROUND)
        self.assertEqual(arrival.status, "LANDING")
        self.assertEqual(arrival.altitude_ft, 0.0)
        self._run_until(lambda: arrival.status == "LANDED")
        self.assertEqual(arrival.flight_phase, FlightPhase.LANDED)

    def test_departure_climbs_only_after_liftoff_and_pause_freezes_altitude(self) -> None:
        departure = self._flight("DAL731")
        initial_position = departure.current_position
        self.controller.start()
        for _ in range(4):
            self._tick()
        self.assertEqual(departure.current_position, initial_position)
        self.assertEqual(departure.altitude_ft, 0.0)
        self.assertEqual(departure.flight_phase, FlightPhase.GROUND)

        self.assertTrue(self.controller.grant_takeoff_clearance(departure.flight_id))
        self.assertEqual(departure.altitude_ft, 0.0)
        self._run_until(lambda: departure.flight_phase is FlightPhase.CLIMB)
        self.assertGreater(departure.altitude_ft, 0.0)

        self.controller.pause()
        paused_altitude = departure.altitude_ft
        self.assertFalse(self.pending_ticks)
        self.controller.resume()
        self._tick()
        self.assertGreater(departure.altitude_ft, paused_altitude)

        self._run_until(lambda: departure.status == "DEPARTED")
        self.assertEqual(
            departure.altitude_ft,
            self.controller.departure_climb_target_altitude_ft,
        )
        self.assertEqual(departure.flight_phase, FlightPhase.DEPARTED)
        terminal_altitude = departure.altitude_ft
        self._tick()
        self.assertEqual(departure.altitude_ft, terminal_altitude)

    def test_reset_restores_original_altitude_and_phase(self) -> None:
        arrival = self._flight("AAL204")
        initial_altitude = arrival.initial_altitude_ft
        self.controller.start()
        self._tick()
        self.assertLess(arrival.altitude_ft, initial_altitude)
        self.controller.reset()

        self.assertEqual(arrival.altitude_ft, initial_altitude)
        self.assertEqual(
            arrival.target_altitude_ft,
            self.controller.arrival_checkpoint_altitude_ft,
        )
        self.assertEqual(arrival.flight_phase, FlightPhase.APPROACH)
        self.assertEqual(arrival.current_position, arrival.initial_position)
        self.assertFalse(self.pending_ticks)

    def test_reset_during_climb_restores_departure_altitude_and_runway(self) -> None:
        departure = self._flight("DAL731")
        self.controller.start()
        self.assertTrue(self.controller.grant_takeoff_clearance(departure.flight_id))
        self._run_until(lambda: departure.flight_phase is FlightPhase.CLIMB)
        self.assertGreater(departure.altitude_ft, 0.0)

        self.controller.reset()

        self.assertEqual(departure.altitude_ft, 0.0)
        self.assertEqual(departure.flight_phase, FlightPhase.GROUND)
        self.assertEqual(departure.current_position, departure.initial_position)
        self.assertIsNone(departure.assigned_runway)
        self.assertTrue(all(runway.available for runway in self.runways))
        self.assertFalse(self.pending_ticks)

    def test_phase_events_are_transition_based_not_per_tick(self) -> None:
        self.controller.start()
        for _ in range(5):
            self._tick()
        messages = [
            event.message for event in self.controller.activity_log.events
        ]
        self.assertEqual(messages.count("Aircraft began descent."), 3)

        for _ in range(5):
            self._tick()
        messages = [
            event.message for event in self.controller.activity_log.events
        ]
        self.assertEqual(messages.count("Aircraft began descent."), 3)

    def test_altitude_is_distance_based_across_zero_length_waypoints(self) -> None:
        kwargs = dict(
            flight_id="TEST-1",
            altitude_ft=1000,
            speed_kts=120,
            flight_type="Arrival",
            status="Approach",
            initial_position=(0.0, 0.0),
            route_waypoints=(
                (0.0, 0.0),
                (0.5, 0.0),
                (0.5, 0.0),
                (1.0, 0.0),
            ),
            movement_speed=1.0,
        )
        one_step = Aircraft(**kwargs)
        two_steps = deepcopy(one_step)
        for flight in (one_step, two_steps):
            flight.configure_altitude_profile(((0, 1000.0), (3, 0.0)))

        one_step.advance(0.5)
        two_steps.advance(0.25)
        two_steps.advance(0.25)

        self.assertEqual(one_step.current_position, two_steps.current_position)
        self.assertEqual(one_step.altitude_ft, two_steps.altitude_ft)
        self.assertEqual(one_step.altitude_ft, 500.0)


if __name__ == "__main__":
    unittest.main()

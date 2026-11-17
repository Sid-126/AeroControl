"""Regression tests for operational runway closure and reopening."""

from collections.abc import Callable
from copy import deepcopy
import unittest

from core.aircraft import (
    AWAITING_TAKEOFF_CLEARANCE,
    CLEARED_TO_LAND,
    DEPARTED,
    HOLDING,
    LANDED,
    Aircraft,
)
from core.runway import (
    Runway,
    RunwayOperationalStatus,
    RunwayStatus,
)
from core.simulation import SimulationController
from data.sample_flights import SAMPLE_FLIGHTS, SAMPLE_RUNWAYS


class RunwayClosureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.aircraft = deepcopy(SAMPLE_FLIGHTS)
        self.runways = deepcopy(SAMPLE_RUNWAYS)
        self.pending_ticks: dict[str, Callable[[], None]] = {}
        self.next_tick_id = 0
        self.controller = self._make_controller()

    def _make_controller(self) -> SimulationController:
        return SimulationController(
            self.aircraft,
            self._schedule,
            self._cancel,
            interval_ms=500,
            runways=self.runways,
            runway_clearing_delay_seconds=1.0,
        )

    def _schedule(self, _delay_ms: int, callback: Callable[[], None]) -> str:
        self.next_tick_id += 1
        token = str(self.next_tick_id)
        self.pending_ticks[token] = callback
        return token

    def _cancel(self, token: str) -> None:
        self.pending_ticks.pop(token, None)

    def _tick(self) -> None:
        self.assertTrue(self.pending_ticks, "Expected a scheduled simulation tick")
        token = next(iter(self.pending_ticks))
        callback = self.pending_ticks.pop(token)
        callback()

    def _run_until(self, predicate: Callable[[], bool], limit: int = 600) -> None:
        for _ in range(limit):
            if predicate():
                return
            self._tick()
        self.fail("Simulation did not reach the requested runway state")

    def _flight(self, flight_id: str) -> Aircraft:
        return next(flight for flight in self.aircraft if flight.flight_id == flight_id)

    def _runway(self, runway_id: str = "27L") -> Runway:
        return next(runway for runway in self.runways if runway.runway_id == runway_id)

    def _reach_holding(self, flight_id: str = "AAL204") -> Aircraft:
        aircraft = self._flight(flight_id)
        self.controller.start()
        self._run_until(lambda: aircraft.status == HOLDING)
        return aircraft

    def _events(self, message: str) -> list:
        return [
            event
            for event in self.controller.activity_log.events
            if event.message.startswith(message)
        ]

    def test_closing_available_runway_preserves_physical_availability(self) -> None:
        runway = self._runway()
        self.assertTrue(self.controller.close_runway(runway.runway_id, "Inspection"))
        self.assertEqual(runway.status, RunwayStatus.AVAILABLE)
        self.assertTrue(runway.available)
        self.assertEqual(runway.operational_status, RunwayOperationalStatus.CLOSED)
        self.assertEqual(runway.closure_reason, "Inspection")
        self.assertFalse(runway.eligible_for_operation)
        self.assertFalse(self.controller.close_runway(runway.runway_id, "Inspection"))
        with self.assertRaisesRegex(ValueError, "Unknown runway"):
            self.controller.reopen_runway("unknown")

        self.assertEqual(len(self._events("Runway 27L closure requested:")), 1)
        self.assertEqual(len(self._events("Runway 27L closed:")), 1)

    def test_closed_runway_is_skipped_by_allocator_and_can_be_reopened(self) -> None:
        self.assertTrue(self.controller.close_runway("27L", "Maintenance"))
        self.controller.start()
        departure = self._flight("DAL731")
        self.assertTrue(self.controller.grant_takeoff_clearance(departure.flight_id))
        self.assertEqual(departure.assigned_runway, "27R")

        self.assertTrue(self.controller.reopen_runway("27L"))
        self.assertTrue(self.controller.can_grant_takeoff_clearance("JBU402"))
        self.assertFalse(self.controller.reopen_runway("27L"))
        self.assertEqual(len(self._events("Runway 27L reopened.")), 1)

    def test_deferred_closure_preserves_reserved_takeoff_and_blocks_that_runway(self) -> None:
        self.controller.start()
        dal = self._flight("DAL731")
        jbu = self._flight("JBU402")
        self.assertTrue(self.controller.grant_takeoff_clearance(dal.flight_id))
        runway = self._runway()
        self.assertEqual(runway.status, RunwayStatus.RESERVED)
        self.assertTrue(self.controller.close_runway("27L", "Surface inspection"))
        self.assertEqual(
            runway.operational_status, RunwayOperationalStatus.CLOSURE_PENDING
        )
        event_count = len(self.controller.activity_log.events)
        self.assertFalse(self.controller.close_runway("27L", "Duplicate request"))
        self.assertEqual(len(self.controller.activity_log.events), event_count)
        self.assertEqual(runway.assigned_aircraft_id, dal.flight_id)
        self.assertTrue(self.controller.can_grant_takeoff_clearance(jbu.flight_id))
        self.assertTrue(self.controller.grant_takeoff_clearance(jbu.flight_id))
        self.assertEqual(jbu.assigned_runway, "27R")

        self._run_until(
            lambda: runway.operational_status is RunwayOperationalStatus.CLOSED
        )
        self.assertTrue(runway.available)
        self.assertEqual(runway.status, RunwayStatus.AVAILABLE)
        self.assertIsNone(runway.assigned_aircraft_id)
        self.assertIn(dal.status, ("TAKING OFF", DEPARTED))
        self.assertEqual(len(self._events("Runway 27L closure deferred")), 1)
        self.assertEqual(len(self._events("Runway 27L closed after clearing:")), 1)

    def test_closure_during_occupancy_and_clearing_finishes_after_delay(self) -> None:
        self.controller.start()
        departure = self._flight("DAL731")
        self.assertTrue(self.controller.grant_takeoff_clearance(departure.flight_id))
        self._tick()
        runway = self._runway()
        self.assertEqual(runway.status, RunwayStatus.OCCUPIED)
        pnr_id = runway.assigned_aircraft_id
        self.assertTrue(self.controller.close_runway("27L", "Runway work"))
        self.assertEqual(runway.operational_status, RunwayOperationalStatus.CLOSURE_PENDING)
        self.assertEqual(runway.assigned_aircraft_id, pnr_id)

        self._run_until(lambda: runway.status is RunwayStatus.CLEARING)
        self.assertEqual(runway.assigned_aircraft_id, pnr_id)
        remaining = runway.remaining_clearing_seconds
        self.controller.pause()
        self.assertFalse(self.pending_ticks)
        self.assertEqual(runway.remaining_clearing_seconds, remaining)
        self.controller.resume()
        self._tick()
        self.assertEqual(runway.operational_status, RunwayOperationalStatus.CLOSURE_PENDING)
        self._tick()
        self.assertEqual(runway.operational_status, RunwayOperationalStatus.CLOSED)
        self.assertEqual(runway.status, RunwayStatus.AVAILABLE)
        self.assertIsNone(runway.assigned_aircraft_id)

    def test_closure_requested_during_clearing_completes_only_after_countdown(self) -> None:
        self.controller.start()
        departure = self._flight("DAL731")
        self.assertTrue(self.controller.grant_takeoff_clearance(departure.flight_id))
        runway = self._runway()
        self._run_until(lambda: runway.status is RunwayStatus.CLEARING)
        pnr_id = runway.assigned_aircraft_id
        self.assertTrue(self.controller.close_runway("27L", "Post-operation inspection"))
        self.assertEqual(runway.operational_status, RunwayOperationalStatus.CLOSURE_PENDING)
        self.assertEqual(runway.assigned_aircraft_id, pnr_id)
        self.assertEqual(runway.remaining_clearing_seconds, 1.0)

        self.controller.pause()
        self.assertEqual(runway.remaining_clearing_seconds, 1.0)
        self.controller.resume()
        self._tick()
        self.assertEqual(runway.remaining_clearing_seconds, 0.5)
        self.assertEqual(runway.operational_status, RunwayOperationalStatus.CLOSURE_PENDING)
        self._tick()
        self.assertEqual(runway.status, RunwayStatus.AVAILABLE)
        self.assertEqual(runway.operational_status, RunwayOperationalStatus.CLOSED)
        self.assertIsNone(runway.assigned_aircraft_id)

    def test_pending_closure_can_be_explicitly_cancelled(self) -> None:
        self.controller.start()
        departure = self._flight("DAL731")
        self.assertTrue(self.controller.grant_takeoff_clearance(departure.flight_id))
        runway = self._runway()
        self.assertTrue(self.controller.close_runway("27L", "Inspection"))
        self.assertTrue(self.controller.cancel_pending_runway_closure("27L"))
        self.assertEqual(runway.operational_status, RunwayOperationalStatus.OPEN)
        self.assertIsNone(runway.closure_reason)
        self.assertEqual(runway.status, RunwayStatus.RESERVED)
        self.assertEqual(runway.assigned_aircraft_id, departure.flight_id)
        self.assertFalse(self.controller.cancel_pending_runway_closure("27L"))
        self.assertFalse(self.controller.reopen_runway("27L"))

        self._run_until(lambda: runway.status is RunwayStatus.AVAILABLE)
        self.assertEqual(runway.operational_status, RunwayOperationalStatus.OPEN)
        self.assertEqual(len(self._events("Pending closure for runway 27L cancelled.")), 1)
        self.assertFalse(self._events("Runway 27L closed after clearing:"))

    def test_both_closed_arrivals_hold_departures_wait_and_clearance_is_blocked(self) -> None:
        self.assertTrue(self.controller.close_runway("27L", "Maintenance"))
        self.assertTrue(self.controller.close_runway("27R", "Maintenance"))
        self.controller.start()
        arrival = self._flight("AAL204")
        departure = self._flight("DAL731")
        self._run_until(lambda: arrival.status == HOLDING)
        self.assertEqual(arrival.altitude_ft, self.controller.holding_altitude_ft)
        position = departure.current_position
        self._tick()
        self.assertEqual(arrival.status, HOLDING)
        self.assertEqual(arrival.altitude_ft, self.controller.holding_altitude_ft)
        self.assertEqual(departure.status, AWAITING_TAKEOFF_CLEARANCE)
        self.assertEqual(departure.current_position, position)
        self.assertIsNone(departure.assigned_runway)
        self.assertFalse(self.controller.can_grant_landing_clearance(arrival.flight_id))
        self.assertFalse(self.controller.grant_landing_clearance(arrival.flight_id))
        self.assertFalse(self.controller.can_grant_takeoff_clearance(departure.flight_id))
        self.assertFalse(self.controller.grant_takeoff_clearance(departure.flight_id))

        self.assertTrue(self.controller.reopen_runway("27L"))
        self.assertTrue(self.controller.grant_landing_clearance(arrival.flight_id))
        self.assertEqual(arrival.assigned_runway, "27L")
        self._run_until(lambda: arrival.status == CLEARED_TO_LAND)
        self._tick()
        self._run_until(lambda: arrival.status == LANDED)
        self._run_until(lambda: self._runway().status is RunwayStatus.AVAILABLE)
        self.assertEqual(self._runway().operational_status, RunwayOperationalStatus.OPEN)

    def test_end_to_end_closure_scenario_reopens_releases_and_resets(self) -> None:
        self.controller.start()
        self.assertTrue(self.controller.close_runway("27L", "Scheduled inspection"))
        dal = self._flight("DAL731")
        jbu = self._flight("JBU402")
        arrival = self._flight("AAL204")
        self.assertTrue(self.controller.grant_takeoff_clearance(dal.flight_id))
        self.assertEqual(dal.assigned_runway, "27R")

        self.assertTrue(self.controller.close_runway("27R", "Surface inspection"))
        self.assertEqual(
            self._runway("27R").operational_status,
            RunwayOperationalStatus.CLOSURE_PENDING,
        )
        self.assertFalse(self.controller.can_grant_takeoff_clearance(jbu.flight_id))
        self.assertFalse(self.controller.grant_takeoff_clearance(jbu.flight_id))
        self._run_until(lambda: arrival.status == HOLDING)
        self.assertFalse(self.controller.can_grant_landing_clearance(arrival.flight_id))
        self.assertEqual(arrival.altitude_ft, self.controller.holding_altitude_ft)

        self._run_until(
            lambda: self._runway("27R").operational_status
            is RunwayOperationalStatus.CLOSED
        )
        self.assertEqual(dal.assigned_runway, "27R")
        self.assertIsNone(self._runway("27R").assigned_aircraft_id)
        self.assertEqual(arrival.status, HOLDING)

        self.assertTrue(self.controller.reopen_runway("27L"))
        self.assertTrue(self.controller.grant_landing_clearance(arrival.flight_id))
        self.assertEqual(arrival.assigned_runway, "27L")
        self._run_until(lambda: arrival.status == LANDED)
        self._run_until(
            lambda: self._runway("27L").status is RunwayStatus.AVAILABLE
        )
        self.assertEqual(
            self._runway("27L").operational_status,
            RunwayOperationalStatus.OPEN,
        )

        self.controller.reset()
        self.assertTrue(
            all(
                runway.operational_status is RunwayOperationalStatus.OPEN
                for runway in self.runways
            )
        )
        self.assertTrue(
            all(runway.available and runway.assigned_aircraft_id is None for runway in self.runways)
        )

    def test_pending_closure_can_be_cancelled_by_reserved_landing_go_around(self) -> None:
        arrival = self._reach_holding()
        self.assertTrue(self.controller.grant_landing_clearance(arrival.flight_id))
        self.assertTrue(self.controller.close_runway("27L", "Maintenance"))
        self.assertEqual(
            self._runway().operational_status,
            RunwayOperationalStatus.CLOSURE_PENDING,
        )

        self.assertTrue(self.controller.go_around(arrival.flight_id))
        self.assertEqual(self._runway().status, RunwayStatus.AVAILABLE)
        self.assertEqual(
            self._runway().operational_status, RunwayOperationalStatus.CLOSED
        )
        self.assertIsNone(self._runway().assigned_aircraft_id)
        self.assertEqual(len(self._events("Runway 27L closed after its reservation")), 1)

    def test_previously_cleared_landing_finishes_before_deferred_closure(self) -> None:
        arrival = self._reach_holding()
        self.assertTrue(self.controller.grant_landing_clearance(arrival.flight_id))
        self.assertTrue(self.controller.close_runway("27L", "Surface inspection"))
        self.assertEqual(arrival.status, "EXITING HOLD")
        self.assertEqual(
            self._runway().operational_status,
            RunwayOperationalStatus.CLOSURE_PENDING,
        )

        self._run_until(lambda: arrival.status == LANDED)
        self._run_until(
            lambda: self._runway().operational_status
            is RunwayOperationalStatus.CLOSED
        )
        self.assertEqual(arrival.status, LANDED)
        self.assertTrue(self._runway().available)
        self.assertIsNone(self._runway().assigned_aircraft_id)

    def test_reset_restores_open_and_permanently_unavailable_baselines(self) -> None:
        self.runways = [*self.runways, Runway("09", False)]
        self.controller = self._make_controller()
        self.assertTrue(self.controller.close_runway("27L", "Maintenance"))
        self.controller.start()
        departure = self._flight("DAL731")
        self.assertTrue(self.controller.grant_takeoff_clearance(departure.flight_id))
        self.assertTrue(self.controller.close_runway("27R", "Runway works"))
        self.assertEqual(
            self._runway("27R").operational_status,
            RunwayOperationalStatus.CLOSURE_PENDING,
        )
        self.assertFalse(self.controller.can_reopen_runway("09"))
        self.assertFalse(self.controller.reopen_runway("09"))
        self.assertEqual(
            self.runways[2].operational_status,
            RunwayOperationalStatus.UNAVAILABLE,
        )
        self.controller.reset()

        self.assertEqual(self._runway("27L").operational_status, RunwayOperationalStatus.OPEN)
        self.assertIsNone(self._runway("27L").closure_reason)
        self.assertEqual(self._runway("09").status, RunwayStatus.UNAVAILABLE)
        self.assertEqual(
            self._runway("09").operational_status,
            RunwayOperationalStatus.UNAVAILABLE,
        )
        self.assertIsNone(self._runway("09").closure_reason)

    def test_empty_or_invalid_closure_reasons_are_rejected(self) -> None:
        for reason in ("", "  "):
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(ValueError, "non-empty reason"):
                    self.controller.close_runway("27L", reason)
        with self.assertRaisesRegex(ValueError, "Unknown runway"):
            self.controller.close_runway("09", "Inspection")


if __name__ == "__main__":
    unittest.main()

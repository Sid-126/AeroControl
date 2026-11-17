"""Regression tests for aircraft and runway model invariants."""

import math
import unittest

from core.aircraft import Aircraft
from core.runway import Runway, RunwayOperation, RunwayStatus


class ModelInvariantTests(unittest.TestCase):
    def test_runways_reject_preconfigured_assignments_and_operations(self) -> None:
        invalid_configurations = (
            {"available": True, "reserved_by": "PNR-1"},
            {
                "available": False,
                "reserved_by": "PNR-1",
                "operation_type": RunwayOperation.TAKEOFF,
            },
            {"available": True, "operation_type": RunwayOperation.LANDING},
            {"available": False, "operation_type": RunwayOperation.TAKEOFF},
        )
        for configuration in invalid_configurations:
            with self.subTest(configuration=configuration):
                with self.assertRaisesRegex(ValueError, "Initial runway reservations"):
                    Runway("27L", **configuration)

    def test_reset_restores_clean_available_and_unavailable_baselines(self) -> None:
        available = Runway("27L", True)
        unavailable = Runway("27R", False)
        self.assertEqual(available.status, RunwayStatus.AVAILABLE)
        self.assertEqual(unavailable.status, RunwayStatus.UNAVAILABLE)

        self.assertTrue(available.reserve("PNR-1", RunwayOperation.TAKEOFF))
        self.assertTrue(available.occupy("PNR-1", RunwayOperation.TAKEOFF))
        self.assertTrue(available.begin_clearing("PNR-1", 5.0))
        available.reset()
        unavailable.reset()

        self.assertEqual(available.status, RunwayStatus.AVAILABLE)
        self.assertTrue(available.available)
        self.assertIsNone(available.assigned_aircraft_id)
        self.assertIsNone(available.operation_type)
        self.assertEqual(available.remaining_clearing_seconds, 0.0)
        self.assertEqual(unavailable.status, RunwayStatus.UNAVAILABLE)
        self.assertFalse(unavailable.available)
        self.assertIsNone(unavailable.assigned_aircraft_id)
        self.assertIsNone(unavailable.operation_type)

    def test_aircraft_rejects_an_initial_runway_assignment(self) -> None:
        with self.assertRaisesRegex(ValueError, "managed by SimulationController"):
            Aircraft(
                "PNR-1",
                1000,
                120,
                "Departure",
                "Awaiting clearance",
                initial_position=(0.0, 0.0),
                route_waypoints=((0.0, 0.0), (1.0, 0.0)),
                assigned_runway="27L",
            )

    def test_non_finite_or_non_positive_movement_speeds_are_rejected(self) -> None:
        for speed in (0.0, -0.1, math.nan, math.inf, -math.inf):
            with self.subTest(speed=speed):
                with self.assertRaisesRegex(ValueError, "movement_speed"):
                    Aircraft(
                        "PNR-1",
                        1000,
                        120,
                        "Arrival",
                        "Approach",
                        initial_position=(0.0, 0.0),
                        route_waypoints=((0.0, 0.0), (1.0, 0.0)),
                        movement_speed=speed,
                    )

    def test_mutated_invalid_speed_is_rejected_before_route_movement(self) -> None:
        aircraft = Aircraft(
            "PNR-1",
            1000,
            120,
            "Arrival",
            "Approach",
            initial_position=(0.0, 0.0),
            route_waypoints=((0.0, 0.0), (1.0, 0.0)),
        )
        aircraft.movement_speed = 0.0
        with self.assertRaisesRegex(ValueError, "movement_speed"):
            aircraft.advance(1.0)


if __name__ == "__main__":
    unittest.main()

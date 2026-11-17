"""Sample aircraft and runway configuration for the simulator."""

from core.aircraft import AWAITING_TAKEOFF_CLEARANCE, Aircraft
from core.runway import Runway


SAMPLE_FLIGHTS = (
    Aircraft(
        "AAL204", 8400, 248, "Arrival", "Approach",
        initial_position=(-0.68, -0.36),
        route_waypoints=((-0.68, -0.36), (-0.43, -0.23), (-0.20, -0.11), (0.0, 0.0)),
        movement_speed=0.105,
    ),
    Aircraft(
        "UAL518", 12500, 286, "Arrival", "En route",
        initial_position=(0.62, -0.76),
        route_waypoints=((0.62, -0.76), (0.36, -0.45), (0.18, -0.22), (0.03, -0.03)),
        movement_speed=0.085,
    ),
    Aircraft(
        "DAL731", 0, 0, "Departure", AWAITING_TAKEOFF_CLEARANCE,
        initial_position=(0.10, 0.05),
        route_waypoints=((0.10, 0.05), (0.32, 0.08), (0.58, 0.12), (0.84, 0.16)),
        movement_speed=0.12,
    ),
    Aircraft(
        "SWA116", 9200, 265, "Arrival", "En route",
        initial_position=(-0.16, 0.78),
        route_waypoints=((-0.16, 0.78), (-0.10, 0.49), (-0.04, 0.22), (0.0, 0.0)),
        movement_speed=0.095,
    ),
    Aircraft(
        "JBU402", 0, 0, "Departure", AWAITING_TAKEOFF_CLEARANCE,
        initial_position=(-0.08, -0.06),
        route_waypoints=((-0.08, -0.06), (-0.28, -0.23), (-0.53, -0.42), (-0.78, -0.58)),
        movement_speed=0.11,
    ),
)

SAMPLE_RUNWAYS = (
    Runway(
        "27L",
        True,
        centerline=((0.12, 0.07), (0.50, 0.07)),
        departure_end_point=(0.88, 0.07),
        landing_vacate_point=(0.55, 0.20),
    ),
    Runway(
        "27R",
        True,
        centerline=((0.12, -0.07), (0.50, -0.07)),
        departure_end_point=(0.88, -0.07),
        landing_vacate_point=(0.55, -0.20),
    ),
)

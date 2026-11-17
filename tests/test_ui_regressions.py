"""Tk widget regression checks for status summaries and simulation controls."""

from collections.abc import Callable
from copy import deepcopy
import tkinter as tk
import unittest

from core.aircraft import (
    GOING_AROUND,
    HOLDING,
    LANDED,
    LANDING,
    Aircraft,
    FlightPhase,
    RouteMode,
)
from core.simulation import SimulationController, SimulationState
from core.runway import RunwayOperationalStatus
from data.sample_flights import SAMPLE_FLIGHTS, SAMPLE_RUNWAYS
from ui.dashboard import Dashboard
from ui.theme import (
    GO_AROUND_COLOR,
    HOLDING_COLOR,
    RUNWAY_CLOSED_COLOR,
    RUNWAY_PENDING_CLOSURE_COLOR,
)


class DashboardRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk is unavailable: {error}")
        self.root.withdraw()
        self.aircraft = deepcopy(SAMPLE_FLIGHTS)
        self.runways = deepcopy(SAMPLE_RUNWAYS)
        self.pending_ticks: dict[str, Callable[[], None]] = {}
        self._next_tick_id = 0
        self.simulation = SimulationController(
            self.aircraft,
            self._schedule,
            self._cancel,
            interval_ms=500,
            runways=self.runways,
            runway_clearing_delay_seconds=1.0,
        )
        self.dashboard = Dashboard(
            self.root,
            aircraft=self.aircraft,
            runways=self.runways,
            simulation=self.simulation,
            on_start=self.simulation.start,
            on_pause=self.simulation.pause,
            on_resume=self.simulation.resume,
            on_reset=self.simulation.reset,
        )
        self.dashboard.pack(fill=tk.BOTH, expand=True)
        self.root.update_idletasks()

    def tearDown(self) -> None:
        if hasattr(self, "simulation"):
            self.simulation.shutdown()
        if hasattr(self, "root"):
            try:
                self.root.destroy()
            except tk.TclError:
                pass

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

    def _run_until(
        self, predicate: Callable[[], bool], limit: int = 300
    ) -> None:
        for _ in range(limit):
            if predicate():
                return
            self._tick()
        self.fail("Simulation did not reach the expected aircraft state")

    def _flight(self, flight_id: str) -> Aircraft:
        return next(flight for flight in self.aircraft if flight.flight_id == flight_id)

    @staticmethod
    def _is_enabled(button) -> bool:
        return button.instate(["!disabled"])

    def test_arrival_summary_tracks_operational_states_without_navigation(self) -> None:
        page = self.dashboard.pages["flights"]
        arrival = self._flight("AAL204")
        active_arrival_count = page.summary_values["arrivals"]
        self.assertEqual(active_arrival_count.cget("text"), "3")

        self.simulation.start()
        self._run_until(lambda: arrival.status == HOLDING)
        self.assertFalse(arrival.route_complete)
        self.assertEqual(active_arrival_count.cget("text"), "3")

        self.assertTrue(self.simulation.grant_landing_clearance(arrival.flight_id))
        self.assertEqual(active_arrival_count.cget("text"), "3")
        self._run_until(lambda: arrival.status == LANDING)
        self.assertEqual(arrival.status, LANDING)
        self.assertEqual(active_arrival_count.cget("text"), "3")

        self._run_until(lambda: arrival.status == LANDED)
        self.assertEqual(active_arrival_count.cget("text"), "2")

    def test_simulation_buttons_match_controller_state_on_both_pages(self) -> None:
        overview = self.dashboard.pages["overview"]
        radar = self.dashboard.live_radar_page
        control_sets = (
            (overview.start_button, overview.pause_button, overview.resume_button),
            (radar.start_button, radar.pause_button, radar.resume_button),
        )

        def assert_states(start: bool, pause: bool, resume: bool) -> None:
            for buttons in control_sets:
                self.assertEqual(
                    tuple(self._is_enabled(button) for button in buttons),
                    (start, pause, resume),
                )

        assert_states(True, False, False)
        self.simulation.start()
        assert_states(False, True, False)
        self.simulation.pause()
        assert_states(False, False, True)
        self.simulation.resume()
        assert_states(False, True, False)
        self.simulation.reset()
        self.assertEqual(self.simulation.state, SimulationState.READY)
        assert_states(True, False, False)

    def test_altitude_and_phase_refresh_across_shared_pages(self) -> None:
        arrival = self._flight("AAL204")
        radar = self.dashboard.live_radar_page
        overview = self.dashboard.pages["overview"]
        flights = self.dashboard.pages["flights"]

        self.simulation.select_aircraft(arrival.flight_id)
        self.dashboard.show_page("radar")
        self.assertIn("8,400 ft", radar.detail_labels["Altitude"].cget("text"))
        self.assertIn(
            FlightPhase.APPROACH.value,
            radar.detail_labels["Flight phase"].cget("text"),
        )
        self.assertIs(overview.radar.aircraft[0], arrival)
        self.assertIs(radar.radar.aircraft[0], arrival)

        self.simulation.start()
        self._tick()
        self.root.update_idletasks()

        self.assertLess(arrival.altitude_ft, arrival.initial_altitude_ft)
        self.assertIn(
            FlightPhase.DESCENT.value,
            radar.detail_labels["Flight phase"].cget("text"),
        )
        self.assertIn(
            f"{arrival.altitude_ft:,.0f} ft",
            radar.detail_labels["Altitude"].cget("text"),
        )
        row_values = flights.flight_tree.item(arrival.flight_id, "values")
        self.assertEqual(row_values[2], f"{arrival.altitude_ft:,.0f} ft")
        self.assertEqual(row_values[3], FlightPhase.DESCENT.value)
        self.assertIn("Descent 3", overview.flight_phase_summary.cget("text"))

    def test_holding_and_go_around_refresh_controls_details_and_route_styles(self) -> None:
        arrival = self._flight("AAL204")
        radar_page = self.dashboard.live_radar_page
        overview = self.dashboard.pages["overview"]
        flights = self.dashboard.pages["flights"]
        self.simulation.select_aircraft(arrival.flight_id)
        self.assertTrue(self._is_enabled(radar_page.enter_holding_button))
        self.assertTrue(self._is_enabled(radar_page.go_around_button))

        self.assertTrue(self.simulation.enter_holding(arrival.flight_id))
        self.simulation.start()
        self._run_until(lambda: arrival.status == HOLDING)

        self.assertEqual(arrival.route_mode, RouteMode.HOLDING)
        self.assertIn("ROUTE MODE  HOLDING", radar_page.detail_labels["Route mode"].cget("text"))
        self.assertTrue(self._is_enabled(radar_page.landing_clearance_button))
        self.assertFalse(self._is_enabled(radar_page.enter_holding_button))
        self.assertFalse(self._is_enabled(radar_page.go_around_button))
        row_values = flights.flight_tree.item(arrival.flight_id, "values")
        self.assertEqual(row_values[4], RouteMode.HOLDING.value)
        self.assertEqual(row_values[5], "0")
        self.assertIn("Holding 1", overview.flight_phase_summary.cget("text"))
        active_route_id = radar_page.radar._route_items[arrival.flight_id][1]
        self.assertEqual(
            radar_page.radar.itemcget(active_route_id, "fill"),
            HOLDING_COLOR,
        )

        self.simulation.reset()
        self.simulation.select_aircraft(arrival.flight_id)
        self.simulation.start()
        self._tick()
        self.assertTrue(self._is_enabled(radar_page.go_around_button))
        self.assertTrue(self.simulation.go_around(arrival.flight_id))
        self.assertEqual(arrival.status, GOING_AROUND)
        self.assertEqual(arrival.route_waypoints[0], arrival.current_position)
        self.assertIn("GO-AROUND", radar_page.detail_labels["Route mode"].cget("text"))
        self.assertEqual(
            radar_page.radar.itemcget(active_route_id, "fill"),
            GO_AROUND_COLOR,
        )

    def test_runway_closure_state_synchronizes_across_all_views(self) -> None:
        overview = self.dashboard.pages["overview"]
        runway_page = self.dashboard.pages["runways"]
        radar_items = self.dashboard.live_radar_page.radar._runway_items
        overview_items = overview.radar._runway_items
        runway = self.runways[0]

        self.assertTrue(self.simulation.close_runway(runway.runway_id, "Inspection"))
        self.assertEqual(runway.operational_status, RunwayOperationalStatus.CLOSED)
        self.assertIn("CLOSED", runway_page.status_labels[0].cget("text"))
        self.assertEqual(runway_page.operational_labels[0].cget("text"), "CLOSED")
        self.assertEqual(runway_page.closure_reason_labels[0].cget("text"), "Inspection")
        self.assertFalse(self._is_enabled(runway_page.close_buttons[runway.runway_id]))
        self.assertTrue(self._is_enabled(runway_page.reopen_buttons[runway.runway_id]))
        self.assertEqual(overview.kpi_values[3].cget("text"), "1")
        for items in (radar_items, overview_items):
            line_id, label_id = items[runway.runway_id]
            canvas = (
                self.dashboard.live_radar_page.radar
                if items is radar_items
                else overview.radar
            )
            self.assertEqual(canvas.itemcget(line_id, "fill"), RUNWAY_CLOSED_COLOR)
            self.assertEqual(canvas.itemcget(label_id, "text"), "27L CLOSED")

        self.assertTrue(self.simulation.reopen_runway(runway.runway_id))
        departure = self._flight("DAL731")
        self.assertTrue(self.simulation.grant_takeoff_clearance(departure.flight_id))
        self.assertTrue(self.simulation.close_runway(runway.runway_id, "Deferred work"))
        self.assertEqual(
            runway.operational_status,
            RunwayOperationalStatus.CLOSURE_PENDING,
        )
        self.assertEqual(runway_page.operational_labels[0].cget("text"), "CLOSURE PENDING")
        self.assertTrue(
            self._is_enabled(runway_page.cancel_closure_buttons[runway.runway_id])
        )
        line_id, label_id = radar_items[runway.runway_id]
        self.assertEqual(
            self.dashboard.live_radar_page.radar.itemcget(line_id, "fill"),
            RUNWAY_PENDING_CLOSURE_COLOR,
        )
        self.assertEqual(
            self.dashboard.live_radar_page.radar.itemcget(label_id, "text"),
            "27L CLOSURE",
        )
        self.assertTrue(
            self.simulation.cancel_pending_runway_closure(runway.runway_id)
        )
        self.assertEqual(runway.operational_status, RunwayOperationalStatus.OPEN)


if __name__ == "__main__":
    unittest.main()

"""Canvas radar with persistent marker items for shared aircraft state."""

import tkinter as tk
from typing import Callable, Sequence

from core.aircraft import Aircraft, Position, RouteMode
from core.runway import Runway, RunwayOperationalStatus, RunwayStatus
from ui.theme import (
    ACCENT,
    ARRIVAL_COLOR,
    DEPARTURE_COLOR,
    GO_AROUND_COLOR,
    HOLDING_COLOR,
    PANELS,
    PRIMARY_TEXT,
    RADAR_BACKGROUND,
    RADAR_GRID,
    RUNWAY_CLOSED_COLOR,
    RUNWAY_PENDING_CLOSURE_COLOR,
    RUNWAY_UNAVAILABLE_COLOR,
    SECONDARY_TEXT,
)

SelectedAircraft = Callable[[], Aircraft | None]
AircraftSelectionHandler = Callable[[Aircraft], None]


class RadarDisplay(tk.Canvas):
    """Draw static radar geometry once and update aircraft marker coordinates."""

    BACKGROUND = RADAR_BACKGROUND
    GRID = RADAR_GRID
    TEXT = SECONDARY_TEXT

    def __init__(
        self,
        master: tk.Misc,
        aircraft: Sequence[Aircraft],
        background: str = PANELS,
        get_selected: SelectedAircraft | None = None,
        on_select: AircraftSelectionHandler | None = None,
        runways: Sequence[Runway] = (),
    ) -> None:
        super().__init__(
            master,
            background=background,
            highlightthickness=0,
            borderwidth=0,
            height=320,
            width=520,
        )
        self.aircraft = aircraft
        self.runways = runways
        self.get_selected = get_selected or (lambda: None)
        self.on_select = on_select
        self._drawn_size = (0, 0)
        self._aircraft_items: dict[str, tuple[int, int, int]] = {}
        self._route_items: dict[str, tuple[int, int]] = {}
        self._runway_items: dict[str, tuple[int, int]] = {}
        self.bind("<Configure>", self._handle_resize)
        self.bind("<Button-1>", self._handle_click, add="+")
        self._draw_static()
        self._create_runway_items()
        self._create_route_items()
        self._create_aircraft_items()
        self.refresh_aircraft_positions()

    def refresh_aircraft_positions(self) -> None:
        """Position persistent markers from normalized shared model coordinates."""
        width = max(self.winfo_width(), 1)
        height = max(self.winfo_height(), 1)
        center_x = width / 2
        center_y = height / 2
        radius = max(20, min(width, height) * 0.39)
        selected_aircraft = self.get_selected()
        self._refresh_runway_positions(center_x, center_y, radius)

        for flight in self.aircraft:
            items = self._aircraft_items.get(flight.flight_id)
            if items is None:
                continue
            ring_id, marker_id, label_id = items
            position = flight.current_position or flight.initial_position
            self._refresh_route(flight, center_x, center_y, radius)
            x = center_x + position[0] * radius
            y = center_y + position[1] * radius
            selected = flight is selected_aircraft
            marker_radius = 6 if selected else 4
            ring_radius = 10
            self.coords(
                ring_id,
                x - ring_radius,
                y - ring_radius,
                x + ring_radius,
                y + ring_radius,
            )
            self.coords(
                marker_id,
                x - marker_radius,
                y - marker_radius,
                x + marker_radius,
                y + marker_radius,
            )
            self.coords(label_id, x + 8, y - 8)
            self._style_marker(flight, ring_id, marker_id, label_id, selected)

    def _create_runway_items(self) -> None:
        for runway in self.runways:
            line_id = self.create_line(
                0,
                0,
                0,
                0,
                width=5,
                capstyle=tk.ROUND,
                tags=("runway",),
            )
            label_id = self.create_text(
                0,
                0,
                text=runway.runway_id,
                fill=PRIMARY_TEXT,
                font=("Consolas", 7, "bold"),
                anchor="center",
                tags=("runway",),
            )
            self._runway_items[runway.runway_id] = (line_id, label_id)

    def _create_route_items(self) -> None:
        for flight in self.aircraft:
            inactive_id = self.create_line(
                0,
                0,
                0,
                0,
                fill="#40525e",
                width=1,
                dash=(2, 3),
                state=tk.HIDDEN,
                tags=("route",),
            )
            active_id = self.create_line(
                0,
                0,
                0,
                0,
                fill=(
                    ARRIVAL_COLOR
                    if flight.flight_type == "Arrival"
                    else DEPARTURE_COLOR
                ),
                width=2,
                state=tk.HIDDEN,
                tags=("route",),
            )
            self._route_items[flight.flight_id] = (inactive_id, active_id)

    def _refresh_runway_positions(
        self, center_x: float, center_y: float, radius: float
    ) -> None:
        colors = {
            RunwayStatus.AVAILABLE: "#496b73",
            RunwayStatus.RESERVED: "#b28c43",
            RunwayStatus.OCCUPIED: "#b65e48",
            RunwayStatus.CLEARING: "#7b6989",
            RunwayStatus.UNAVAILABLE: RUNWAY_UNAVAILABLE_COLOR,
        }
        for runway in self.runways:
            items = self._runway_items.get(runway.runway_id)
            if items is None:
                continue
            line_id, label_id = items
            start = self._to_canvas(runway.centerline[0], center_x, center_y, radius)
            end = self._to_canvas(runway.centerline[-1], center_x, center_y, radius)
            self.coords(line_id, *start, *end)
            operational_status = runway.operational_status
            color = {
                RunwayOperationalStatus.CLOSED: RUNWAY_CLOSED_COLOR,
                RunwayOperationalStatus.CLOSURE_PENDING: RUNWAY_PENDING_CLOSURE_COLOR,
                RunwayOperationalStatus.UNAVAILABLE: RUNWAY_UNAVAILABLE_COLOR,
            }.get(operational_status, colors[runway.status])
            label = runway.runway_id
            if operational_status is RunwayOperationalStatus.CLOSED:
                label = f"{label} CLOSED"
            elif operational_status is RunwayOperationalStatus.CLOSURE_PENDING:
                label = f"{label} CLOSURE"
            elif operational_status is RunwayOperationalStatus.UNAVAILABLE:
                label = f"{label} UNAVAILABLE"
            self.itemconfigure(line_id, fill=color)
            self.itemconfigure(label_id, text=label, fill=color)
            self.coords(label_id, (start[0] + end[0]) / 2, start[1] - 8)

    def _refresh_route(
        self,
        flight: Aircraft,
        center_x: float,
        center_y: float,
        radius: float,
    ) -> None:
        items = self._route_items.get(flight.flight_id)
        if items is None or len(flight.route_waypoints) < 2:
            return
        inactive_id, active_id = items
        current = flight.current_position or flight.initial_position
        previous = list(flight.route_waypoints[: flight.route_segment + 1])
        if not previous or previous[-1] != current:
            previous.append(current)
        remaining = [current, *flight.route_waypoints[flight.route_segment + 1 :]]
        self._set_route_coordinates(
            inactive_id, previous, center_x, center_y, radius
        )
        self._set_route_coordinates(
            active_id, remaining, center_x, center_y, radius
        )
        route_colors = {
            RouteMode.APPROACH: (
                ARRIVAL_COLOR
                if flight.flight_type == "Arrival"
                else DEPARTURE_COLOR,
                (),
            ),
            RouteMode.HOLD_ENTRY: (HOLDING_COLOR, (3, 2)),
            RouteMode.HOLDING: (HOLDING_COLOR, (3, 2)),
            RouteMode.HOLD_EXIT: (HOLDING_COLOR, (6, 2)),
            RouteMode.FINAL_APPROACH: (ARRIVAL_COLOR, ()),
            RouteMode.GO_AROUND: (GO_AROUND_COLOR, (6, 2)),
            RouteMode.DEPARTURE: (DEPARTURE_COLOR, ()),
        }
        color, dash = route_colors[flight.route_mode]
        self.itemconfigure(active_id, fill=color, dash=dash)

    def _set_route_coordinates(
        self,
        item_id: int,
        points: Sequence[Position],
        center_x: float,
        center_y: float,
        radius: float,
    ) -> None:
        if len(points) < 2:
            self.itemconfigure(item_id, state=tk.HIDDEN)
            return
        coordinates = [
            coordinate
            for point in points
            for coordinate in self._to_canvas(point, center_x, center_y, radius)
        ]
        self.coords(item_id, *coordinates)
        self.itemconfigure(item_id, state=tk.NORMAL)

    @staticmethod
    def _to_canvas(
        point: Position, center_x: float, center_y: float, radius: float
    ) -> tuple[float, float]:
        return center_x + point[0] * radius, center_y + point[1] * radius

    def _handle_resize(self, event: tk.Event) -> None:
        if event.widget != self:
            return
        size = (max(self.winfo_width(), 1), max(self.winfo_height(), 1))
        if size != self._drawn_size:
            self._draw_static()
            self.refresh_aircraft_positions()

    def _draw_static(self) -> None:
        """Redraw rings and crosshairs only when the Canvas dimensions change."""
        self.delete("static")
        self.configure(background=self.BACKGROUND)
        width = max(self.winfo_width(), 1)
        height = max(self.winfo_height(), 1)
        self._drawn_size = (width, height)
        center_x = width / 2
        center_y = height / 2
        radius = max(20, min(width, height) * 0.39)

        for ring_number in range(1, 5):
            ring_radius = radius * ring_number / 4
            self.create_oval(
                center_x - ring_radius,
                center_y - ring_radius,
                center_x + ring_radius,
                center_y + ring_radius,
                outline=self.GRID,
                width=1,
                tags=("static",),
            )

        self.create_line(
            center_x - radius,
            center_y,
            center_x + radius,
            center_y,
            fill=self.GRID,
            tags=("static",),
        )
        self.create_line(
            center_x,
            center_y - radius,
            center_x,
            center_y + radius,
            fill=self.GRID,
            tags=("static",),
        )
        self.create_line(
            center_x - radius * 0.7,
            center_y + radius * 0.7,
            center_x + radius * 0.7,
            center_y - radius * 0.7,
            fill="#142d3b",
            tags=("static",),
        )
        self.create_oval(
            center_x - 3,
            center_y - 3,
            center_x + 3,
            center_y + 3,
            fill=ACCENT,
            outline="",
            tags=("static",),
        )
        labels = (
            ("40 NM", center_x + 7, center_y - radius / 2, "w"),
            ("80 NM", center_x + 7, center_y - radius + 10, "w"),
            ("N", center_x, center_y - radius - 13, "center"),
            ("E", center_x + radius + 13, center_y, "center"),
        )
        for label, x, y, anchor in labels:
            self.create_text(
                x,
                y,
                text=label,
                fill=self.TEXT,
                font=("Segoe UI", 8),
                anchor=anchor,
                tags=("static",),
            )
        self.tag_lower("static")

    def _create_aircraft_items(self) -> None:
        for index, flight in enumerate(self.aircraft):
            color = ARRIVAL_COLOR if flight.flight_type == "Arrival" else DEPARTURE_COLOR
            ring_id = self.create_oval(
                0,
                0,
                0,
                0,
                fill="",
                outline=ACCENT,
                width=1,
                state=tk.HIDDEN,
            )
            marker_id = self.create_oval(
                0,
                0,
                0,
                0,
                fill=color,
                outline="",
            )
            label_id = self.create_text(
                0,
                0,
                text=flight.flight_id,
                fill="#dbe8f2",
                font=("Consolas", 8, "bold"),
                anchor="w",
            )
            self._aircraft_items[flight.flight_id] = (ring_id, marker_id, label_id)

    def _style_marker(
        self,
        flight: Aircraft,
        ring_id: int,
        marker_id: int,
        label_id: int,
        selected: bool,
    ) -> None:
        self.itemconfigure(
            ring_id,
            state=tk.NORMAL if selected else tk.HIDDEN,
            outline=ACCENT,
        )
        self.itemconfigure(
            marker_id,
            fill=ARRIVAL_COLOR if flight.flight_type == "Arrival" else DEPARTURE_COLOR,
            outline=PRIMARY_TEXT if selected else "",
            width=2 if selected else 1,
        )
        self.itemconfigure(
            label_id,
            fill=ACCENT if selected else "#dbe8f2",
            font=("Consolas", 9 if selected else 8, "bold"),
        )

    def _handle_click(self, event: tk.Event) -> None:
        """Prefer the topmost label, otherwise the nearest marker within 12 px."""
        if self.on_select is None:
            return

        label_hits: list[tuple[int, Aircraft]] = []
        marker_hits: list[tuple[float, int, Aircraft]] = []
        for index, flight in enumerate(self.aircraft):
            items = self._aircraft_items.get(flight.flight_id)
            if items is None:
                continue
            _ring_id, marker_id, label_id = items
            label_bounds = self.bbox(label_id)
            if label_bounds is not None:
                left, top, right, bottom = label_bounds
                if left <= event.x <= right and top <= event.y <= bottom:
                    label_hits.append((index, flight))

            marker_coords = self.coords(marker_id)
            if marker_coords:
                center_x = (marker_coords[0] + marker_coords[2]) / 2
                center_y = (marker_coords[1] + marker_coords[3]) / 2
                distance_squared = (event.x - center_x) ** 2 + (event.y - center_y) ** 2
                if distance_squared <= 12 ** 2:
                    marker_hits.append((distance_squared, index, flight))

        if label_hits:
            _index, flight = max(label_hits, key=lambda hit: hit[0])
            self.on_select(flight)
        elif marker_hits:
            _distance, _index, flight = min(
                marker_hits, key=lambda hit: (hit[0], -hit[1])
            )
            self.on_select(flight)

"""Reusable page frames for AeroControl's sidebar navigation shell."""

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from typing import Callable, Sequence

from core.activity_log import ActivityEvent, EventCategory, EventSeverity
from core.aircraft import (
    AWAITING_LANDING_CLEARANCE,
    AWAITING_TAKEOFF_CLEARANCE,
    ENTERING_HOLD,
    EXITING_HOLD,
    GOING_AROUND,
    HOLDING,
    LANDED,
    Aircraft,
    FlightPhase,
    RouteMode,
)
from core.runway import (
    Runway,
    RunwayOperationalStatus,
    RunwayStatus,
)
from core.simulation import SimulationController, SimulationState
from ui.radar import RadarDisplay
from ui.theme import (
    ACCENT,
    ARRIVAL_COLOR,
    BACKGROUND,
    DEPARTURE_COLOR,
    EMERGENCY,
    FONT,
    GO_AROUND_COLOR,
    HOLDING_COLOR,
    PANELS,
    PRIMARY_TEXT,
    RUNWAY_CLOSED_COLOR,
    RUNWAY_PENDING_CLOSURE_COLOR,
    RUNWAY_UNAVAILABLE_COLOR,
    SECONDARY_TEXT,
    SUCCESS,
    WARNING,
)


SelectedAircraft = Callable[[], Aircraft | None]
AircraftSelectionHandler = Callable[[Aircraft], None]


def _page_heading(parent: ttk.Frame, title: str, subtitle: str) -> None:
    ttk.Label(parent, text=title, style="PageTitle.TLabel").grid(
        row=0, column=0, sticky="w"
    )
    ttk.Label(parent, text=subtitle, style="PageSubtitle.TLabel").grid(
        row=1, column=0, sticky="w", pady=(3, 0)
    )


def _panel(parent: tk.Misc, padding: int = 14) -> ttk.Frame:
    return ttk.Frame(parent, style="Panel.TFrame", padding=padding)


def _panel_title(parent: tk.Misc, text: str, row: int = 0) -> ttk.Label:
    label = ttk.Label(parent, text=text, style="PanelTitle.TLabel")
    label.grid(row=row, column=0, sticky="w", pady=(0, 9))
    return label


def _treeview(
    parent: tk.Misc,
    columns: Sequence[str],
    headings: Sequence[str],
    widths: Sequence[int],
    height: int = 8,
) -> tuple[ttk.Treeview, ttk.Scrollbar]:
    tree = ttk.Treeview(
        parent,
        columns=tuple(columns),
        show="headings",
        height=height,
        selectmode="browse",
    )
    for column, heading, width in zip(columns, headings, widths):
        tree.heading(column, text=heading)
        tree.column(column, width=width, minwidth=min(width, 70), stretch=True)
    scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    return tree, scrollbar


def _status_text(state: SimulationState) -> tuple[str, str]:
    return {
        SimulationState.READY: ("READY", WARNING),
        SimulationState.RUNNING: ("RUNNING", SUCCESS),
        SimulationState.PAUSED: ("PAUSED", WARNING),
        SimulationState.COMPLETED: ("COMPLETED", ACCENT),
    }[state]


def _set_simulation_control_states(
    state: SimulationState,
    start_button: ttk.Button,
    pause_button: ttk.Button,
    resume_button: ttk.Button,
) -> None:
    button_states = {
        SimulationState.READY: (tk.NORMAL, tk.DISABLED, tk.DISABLED),
        SimulationState.RUNNING: (tk.DISABLED, tk.NORMAL, tk.DISABLED),
        SimulationState.PAUSED: (tk.DISABLED, tk.DISABLED, tk.NORMAL),
        SimulationState.COMPLETED: (tk.DISABLED, tk.DISABLED, tk.DISABLED),
    }
    start_state, pause_state, resume_state = button_states[state]
    start_button.configure(state=start_state)
    pause_button.configure(state=pause_state)
    resume_button.configure(state=resume_state)


def _runway_status(runway: Runway) -> tuple[str, str]:
    physical_color = {
        RunwayStatus.AVAILABLE: SUCCESS,
        RunwayStatus.CLEARING: ACCENT,
        RunwayStatus.UNAVAILABLE: RUNWAY_UNAVAILABLE_COLOR,
    }.get(runway.status, WARNING)
    operational_status = runway.operational_status
    if operational_status is RunwayOperationalStatus.CLOSED:
        return f"{runway.status.value} · CLOSED", RUNWAY_CLOSED_COLOR
    if operational_status is RunwayOperationalStatus.CLOSURE_PENDING:
        return (
            f"{runway.status.value} · CLOSURE PENDING",
            RUNWAY_PENDING_CLOSURE_COLOR,
        )
    if operational_status is RunwayOperationalStatus.UNAVAILABLE:
        return "UNAVAILABLE · BASELINE", RUNWAY_UNAVAILABLE_COLOR
    return runway.status.value, physical_color


def _clearing_time(runway: Runway) -> str:
    if runway.status is not RunwayStatus.CLEARING:
        return "—"
    return f"{runway.remaining_clearing_seconds:.1f} s"


def _altitude_text(altitude_ft: float) -> str:
    return f"{altitude_ft:,.0f} ft"


def _event_row(event: ActivityEvent) -> tuple[str, str, str]:
    return (event.display_time, event.pnr_id or "—", event.message)


def _sync_recent_events(
    tree: ttk.Treeview,
    events: Sequence[ActivityEvent],
    categories: set[EventCategory] | None = None,
    limit: int = 6,
) -> None:
    relevant = (
        event
        for event in reversed(events)
        if categories is None or event.category in categories
    )
    latest = tuple(list(relevant)[:limit])
    desired_ids = {str(event.event_id) for event in latest}
    for item_id in tree.get_children():
        if item_id not in desired_ids:
            tree.delete(item_id)

    for index, event in enumerate(latest):
        item_id = str(event.event_id)
        values = _event_row(event)
        if tree.exists(item_id):
            if tuple(tree.item(item_id, "values")) != values:
                tree.item(item_id, values=values)
        else:
            tree.insert("", tk.END, iid=item_id, values=values)
        order = tree.get_children()
        if index >= len(order) or order[index] != item_id:
            tree.move(item_id, "", index)


class OperationsOverviewPage(ttk.Frame):
    """Overview KPIs, radar preview, runway summary, and recent activity."""

    def __init__(
        self,
        parent: tk.Misc,
        aircraft: Sequence[Aircraft],
        runways: Sequence[Runway],
        simulation: SimulationController,
        get_selected: SelectedAircraft,
        on_select: AircraftSelectionHandler,
        on_start: Callable[[], None],
        on_pause: Callable[[], None],
        on_resume: Callable[[], None],
        on_reset: Callable[[], None],
    ) -> None:
        super().__init__(parent, style="Workspace.TFrame", padding=18)
        self.aircraft = aircraft
        self.runways = runways
        self.simulation = simulation
        self.get_selected = get_selected
        self.on_select = on_select
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        _page_heading(self, "Operations Overview", "Airport activity at a glance")

        self.kpi_row = ttk.Frame(self, style="Workspace.TFrame")
        self.kpi_row.grid(row=2, column=0, sticky="ew", pady=(12, 10))
        for column in range(5):
            self.kpi_row.columnconfigure(column, weight=1, uniform="kpi")
        self.kpi_values: list[tk.Label] = []
        self.kpi_notes: list[tk.Label] = []
        for column, (title, color) in enumerate(
            (
                ("AIRCRAFT TRACKED", ACCENT),
                ("ARRIVALS", SUCCESS),
                ("DEPARTURES", WARNING),
                ("OPEN RUNWAYS", SUCCESS),
                ("ROUTES COMPLETE", ACCENT),
            )
        ):
            card = _panel(self.kpi_row, padding=12)
            card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 8, 0))
            tk.Label(
                card, text=title, bg=PANELS, fg=SECONDARY_TEXT,
                font=(FONT, 8, "bold"), anchor="w"
            ).pack(fill="x")
            value = tk.Label(
                card, text="0", bg=PANELS, fg=color,
                font=(FONT, 19, "bold"), anchor="w"
            )
            value.pack(fill="x", pady=(3, 0))
            note = tk.Label(
                card, text="", bg=PANELS, fg=SECONDARY_TEXT,
                font=(FONT, 8), anchor="w", wraplength=170, justify=tk.LEFT
            )
            note.pack(fill="x")
            self.kpi_values.append(value)
            self.kpi_notes.append(note)

        body = ttk.Frame(self, style="Workspace.TFrame")
        body.grid(row=3, column=0, sticky="nsew")
        body.columnconfigure(0, weight=3, uniform="overview")
        body.columnconfigure(1, weight=2, uniform="overview")
        body.rowconfigure(0, weight=1)

        radar_panel = _panel(body)
        radar_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        radar_panel.columnconfigure(0, weight=1)
        radar_panel.rowconfigure(1, weight=1)
        _panel_title(radar_panel, "AIRSPACE PREVIEW")
        self.radar = RadarDisplay(
            radar_panel,
            self.aircraft,
            background=PANELS,
            get_selected=self.get_selected,
            on_select=self.on_select,
            runways=self.runways,
        )
        self.radar.grid(row=1, column=0, sticky="nsew")

        side = ttk.Frame(body, style="Workspace.TFrame")
        side.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        side.columnconfigure(0, weight=1)
        side.rowconfigure(2, weight=1)
        self.runway_panel = _panel(side, padding=12)
        self.runway_panel.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.runway_panel.columnconfigure(1, weight=1)
        _panel_title(self.runway_panel, "RUNWAY STATUS")
        self.runway_rows: list[tuple[tk.Label, tk.Label]] = []
        for row, runway in enumerate(self.runways, start=1):
            name = tk.Label(
                self.runway_panel, text=runway.runway_id, bg=PANELS,
                fg=PRIMARY_TEXT, font=(FONT, 10, "bold"), anchor="w"
            )
            state = tk.Label(
                self.runway_panel, text="", bg=PANELS,
                font=(FONT, 8, "bold"), anchor="e"
            )
            name.grid(row=row, column=0, sticky="w", pady=2)
            state.grid(row=row, column=1, sticky="e", pady=2)
            self.runway_rows.append((name, state))
        self.runway_utilization_label = tk.Label(
            self.runway_panel,
            text="",
            bg=PANELS,
            fg=SECONDARY_TEXT,
            font=(FONT, 7),
            anchor="w",
            justify=tk.LEFT,
            wraplength=310,
        )
        self.runway_utilization_label.grid(
            row=len(self.runway_rows) + 1,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(5, 0),
        )
        self.flight_phase_summary = tk.Label(
            self.runway_panel,
            text="",
            bg=PANELS,
            fg=SECONDARY_TEXT,
            font=(FONT, 7),
            anchor="w",
            justify=tk.LEFT,
            wraplength=310,
        )
        self.flight_phase_summary.grid(
            row=len(self.runway_rows) + 2,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(4, 0),
        )

        status_panel = _panel(side, padding=12)
        status_panel.grid(row=1, column=0, sticky="ew", pady=8)
        status_panel.columnconfigure(1, weight=1)
        _panel_title(status_panel, "SIMULATION STATUS")
        tk.Label(
            status_panel, text="Controller", bg=PANELS, fg=SECONDARY_TEXT,
            font=(FONT, 9), anchor="w"
        ).grid(row=1, column=0, sticky="w")
        self.simulation_status = tk.Label(
            status_panel, text="", bg=PANELS, font=(FONT, 9, "bold"), anchor="e"
        )
        self.simulation_status.grid(row=1, column=1, sticky="e")
        controls = ttk.Frame(status_panel, style="Panel.TFrame")
        controls.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        controls.columnconfigure((0, 1, 2, 3), weight=1)
        self.start_button = ttk.Button(
            controls, text="Start", style="Accent.TButton", command=on_start
        )
        self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.pause_button = ttk.Button(
            controls, text="Pause", style="Secondary.TButton", command=on_pause
        )
        self.pause_button.grid(row=0, column=1, sticky="ew", padx=3)
        self.resume_button = ttk.Button(
            controls, text="Resume", style="Secondary.TButton", command=on_resume
        )
        self.resume_button.grid(row=0, column=2, sticky="ew", padx=3)
        self.reset_button = ttk.Button(
            controls, text="Reset", style="Secondary.TButton", command=on_reset
        )
        self.reset_button.grid(row=0, column=3, sticky="ew", padx=(3, 0))

        log_panel = _panel(side, padding=12)
        log_panel.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        log_panel.columnconfigure(0, weight=1)
        log_panel.rowconfigure(1, weight=1)
        _panel_title(log_panel, "RECENT ACTIVITY")
        self.log_tree, log_scroll = _treeview(
            log_panel,
            ("time", "pnr", "message"),
            ("TIME", "PNR", "EVENT"),
            (68, 66, 180),
            height=5,
        )
        self.log_tree.grid(row=1, column=0, sticky="nsew")
        log_scroll.grid(row=1, column=1, sticky="ns")
        self._activity_revision = -1
        self.refresh()

    def refresh(self) -> None:
        arrivals = sum(flight.flight_type == "Arrival" for flight in self.aircraft)
        holding = sum(flight.status == HOLDING for flight in self.aircraft)
        final_approach = sum(
            flight.route_mode is RouteMode.FINAL_APPROACH for flight in self.aircraft
        )
        going_around = sum(
            flight.status == GOING_AROUND for flight in self.aircraft
        )
        departures = sum(flight.flight_type == "Departure" for flight in self.aircraft)
        active_departures = sum(
            flight.flight_type == "Departure" and flight.status != "DEPARTED"
            for flight in self.aircraft
        )
        departed = sum(
            flight.flight_type == "Departure" and flight.status == "DEPARTED"
            for flight in self.aircraft
        )
        available = sum(
            runway.operational_status is RunwayOperationalStatus.OPEN
            for runway in self.runways
        )
        physically_available = sum(runway.available for runway in self.runways)
        closed = sum(
            runway.operational_status is RunwayOperationalStatus.CLOSED
            for runway in self.runways
        )
        pending_closures = sum(
            runway.operational_status
            is RunwayOperationalStatus.CLOSURE_PENDING
            for runway in self.runways
        )
        reserved = sum(runway.status is RunwayStatus.RESERVED for runway in self.runways)
        occupied = sum(runway.status is RunwayStatus.OCCUPIED for runway in self.runways)
        clearing = sum(runway.status is RunwayStatus.CLEARING for runway in self.runways)
        completed = sum(flight.route_complete for flight in self.aircraft)
        values = (
            (str(len(self.aircraft)), "Sample targets"),
            (
                str(arrivals),
                f"Inbound · H {holding} · F {final_approach} · GA {going_around}",
            ),
            (str(departures), f"{active_departures} active · {departed} departed"),
            (
                str(available),
                f"{closed} closed · {pending_closures} pending closure",
            ),
            (str(completed), "Routes finished"),
        )
        for label, value_note in zip(self.kpi_values, values):
            if label.cget("text") != value_note[0]:
                label.configure(text=value_note[0])
        for label, value_note in zip(self.kpi_notes, values):
            if label.cget("text") != value_note[1]:
                label.configure(text=value_note[1])
        for runway, row_labels in zip(self.runways, self.runway_rows):
            status = row_labels[1]
            text, color = _runway_status(runway)
            if status.cget("text") != text or status.cget("fg") != color:
                status.configure(text=text, fg=color)
        self.runway_utilization_label.configure(
            text=(
                f"Physically clear {physically_available}  ·  Reserved {reserved}  ·  "
                f"Occupied {occupied}  ·  Clearing {clearing}"
            )
        )
        approach = sum(
            flight.flight_phase is FlightPhase.APPROACH for flight in self.aircraft
        )
        descending = sum(
            flight.flight_phase is FlightPhase.DESCENT for flight in self.aircraft
        )
        final_approach = sum(
            flight.flight_phase is FlightPhase.FINAL_APPROACH
            for flight in self.aircraft
        )
        takeoff_roll = sum(
            flight.flight_phase is FlightPhase.TAKEOFF_ROLL for flight in self.aircraft
        )
        climbing = sum(
            flight.flight_phase is FlightPhase.CLIMB for flight in self.aircraft
        )
        ground = sum(
            flight.flight_phase is FlightPhase.GROUND for flight in self.aircraft
        )
        level = sum(
            flight.flight_phase is FlightPhase.LEVEL for flight in self.aircraft
        )
        holding = sum(
            flight.flight_phase is FlightPhase.HOLDING for flight in self.aircraft
        )
        going_around = sum(
            flight.flight_phase is FlightPhase.GOING_AROUND
            for flight in self.aircraft
        )
        awaiting_clearance = sum(
            flight.status
            in (AWAITING_LANDING_CLEARANCE, AWAITING_TAKEOFF_CLEARANCE)
            for flight in self.aircraft
        )
        self.flight_phase_summary.configure(
            text=(
                f"Approach {approach} · Descent {descending} · Final {final_approach}\n"
                f"Holding {holding} · Go-around {going_around} · Roll {takeoff_roll} · "
                f"Climb {climbing} · Ground {ground} · Level {level} · "
                f"Awaiting {awaiting_clearance}"
            )
        )
        self.set_simulation_status(self.simulation.state)
        self.refresh_aircraft_positions()
        self._refresh_activity_log()

    def set_simulation_status(self, state: SimulationState) -> None:
        status, color = _status_text(state)
        if self.simulation_status.cget("text") != status:
            self.simulation_status.configure(text=status, fg=color)
        elif self.simulation_status.cget("fg") != color:
            self.simulation_status.configure(fg=color)
        _set_simulation_control_states(
            state,
            self.start_button,
            self.pause_button,
            self.resume_button,
        )

    def refresh_aircraft_positions(self) -> None:
        self.radar.refresh_aircraft_positions()

    def _refresh_activity_log(self) -> None:
        activity_log = self.simulation.activity_log
        if self._activity_revision == activity_log.revision:
            return
        self._activity_revision = activity_log.revision
        _sync_recent_events(self.log_tree, activity_log.events, limit=6)


class LiveRadarPage(ttk.Frame):
    """Large shared-data radar with selected flight and placeholder controls."""

    def __init__(
        self,
        parent: tk.Misc,
        aircraft: Sequence[Aircraft],
        runways: Sequence[Runway],
        simulation: SimulationController,
        get_selected: SelectedAircraft,
        on_select: AircraftSelectionHandler,
        on_start: Callable[[], None],
        on_pause: Callable[[], None],
        on_resume: Callable[[], None],
        on_reset: Callable[[], None],
        on_landing_clearance: Callable[[], None],
        on_takeoff_clearance: Callable[[], None],
        on_enter_holding: Callable[[], None],
        on_go_around: Callable[[], None],
    ) -> None:
        super().__init__(parent, style="Workspace.TFrame", padding=18)
        self.aircraft = aircraft
        self.runways = runways
        self.simulation = simulation
        self.get_selected = get_selected
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        self.rowconfigure(4, minsize=180)
        _page_heading(
            self,
            "Live Radar",
            "Educational simulated routes · not real-world trajectories",
        )

        legend = ttk.Frame(self, style="Workspace.TFrame")
        legend.grid(row=2, column=0, sticky="ew", pady=(12, 12))
        self._legend_item(legend, DEPARTURE_COLOR, "DEPARTURE")
        self._legend_item(legend, ARRIVAL_COLOR, "ARRIVAL")
        self._legend_item(legend, HOLDING_COLOR, "HOLDING")
        self._legend_item(legend, GO_AROUND_COLOR, "GO-AROUND")
        tk.Label(
            legend,
            text="Solid: active route  ·  dashed: flown route",
            bg=BACKGROUND,
            fg=SECONDARY_TEXT,
            font=(FONT, 8),
        ).pack(side=tk.RIGHT)
        body = ttk.Frame(self, style="Workspace.TFrame")
        body.grid(row=3, column=0, sticky="nsew")
        body.columnconfigure(0, weight=7, uniform="radar")
        body.columnconfigure(1, weight=3, uniform="radar")
        body.rowconfigure(0, weight=1)

        radar_panel = _panel(body, padding=12)
        radar_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        radar_panel.columnconfigure(0, weight=1)
        radar_panel.rowconfigure(1, weight=1)
        _panel_title(radar_panel, "AIRSPACE SURVEILLANCE")
        self.radar = RadarDisplay(
            radar_panel, self.aircraft, background=PANELS,
            get_selected=self.get_selected, on_select=on_select,
            runways=self.runways,
        )
        self.radar.grid(row=1, column=0, sticky="nsew")

        side = ttk.Frame(body, style="Workspace.TFrame")
        side.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        side.columnconfigure(0, weight=1)
        self.selected_panel = _panel(side, padding=9)
        self.selected_panel.grid(row=0, column=0, sticky="ew", pady=(0, 7))
        self.selected_panel.columnconfigure((0, 1), weight=1)
        title = _panel_title(self.selected_panel, "SELECTED AIRCRAFT")
        title.grid_configure(columnspan=2)
        self.detail_labels: dict[str, tk.Label] = {}
        self.selection_message = tk.Label(
            self.selected_panel,
            text="Select an aircraft on the radar.",
            bg=PANELS,
            fg=SECONDARY_TEXT,
            font=(FONT, 8),
            anchor="w",
        )
        self.selection_message.grid(row=1, column=0, columnspan=2, sticky="w")
        self.details_container = ttk.Frame(self.selected_panel, style="Panel.TFrame")
        self.details_container.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.details_container.columnconfigure((0, 1), weight=1)
        detail_fields = (
            "PNR",
            "Operational status",
            "Flight phase",
            "Route mode",
            "Altitude",
            "Holding altitude",
            "Holding circuits",
            "Go-arounds",
            "Flight type",
            "Ground speed",
            "Assigned runway",
        )
        for index, field in enumerate(detail_fields):
            row = 1 + index // 2
            column = index % 2
            value = tk.Label(
                self.details_container,
                text="",
                bg=PANELS,
                fg=PRIMARY_TEXT,
                font=(FONT, 8, "bold"),
                anchor="w",
            )
            value.grid(
                row=row,
                column=column,
                sticky="w",
                pady=(0, 4),
            )
            self.detail_labels[field] = value
        altitude_scale_max = max(
            self.simulation.departure_climb_target_altitude_ft,
            *(flight.initial_altitude_ft for flight in self.aircraft),
            1.0,
        )
        self.altitude_indicator = ttk.Progressbar(
            self.details_container,
            maximum=altitude_scale_max,
            style="Aero.Horizontal.TProgressbar",
        )
        self.altitude_indicator.grid(
            row=7, column=0, columnspan=2, sticky="ew", pady=(1, 5)
        )
        self.details_container.grid_remove()

        runway_panel = _panel(side, padding=8)
        runway_panel.grid(row=1, column=0, sticky="ew", pady=7)
        runway_panel.columnconfigure((0, 1), weight=1)
        title = _panel_title(runway_panel, "RUNWAYS")
        title.grid_configure(columnspan=2)
        self.runway_statuses: list[tk.Label] = []
        for column, runway in enumerate(self.runways[:2]):
            status = tk.Label(
                runway_panel,
                text=f"{runway.runway_id}",
                bg=PANELS,
                font=(FONT, 8, "bold"),
                anchor="w",
            )
            status.grid(row=1, column=column, sticky="ew", pady=2)
            self.runway_statuses.append(status)

        simulation_panel = _panel(side, padding=8)
        simulation_panel.grid(row=2, column=0, sticky="ew", pady=7)
        simulation_panel.columnconfigure((0, 1), weight=1)
        _panel_title(simulation_panel, "SIM CONTROLS")
        self.simulation_status = tk.Label(
            simulation_panel,
            text="",
            bg=PANELS,
            font=(FONT, 8, "bold"),
            anchor="e",
        )
        self.simulation_status.grid(row=0, column=1, sticky="e", pady=(0, 9))
        self.start_button = ttk.Button(
            simulation_panel, text="Start", style="CompactAccent.TButton", command=on_start
        )
        self.start_button.grid(row=1, column=0, sticky="ew", padx=(0, 3), pady=2)
        self.pause_button = ttk.Button(
            simulation_panel, text="Pause", style="Compact.TButton", command=on_pause
        )
        self.pause_button.grid(row=1, column=1, sticky="ew", padx=(3, 0), pady=2)
        self.resume_button = ttk.Button(
            simulation_panel, text="Resume", style="Compact.TButton", command=on_resume
        )
        self.resume_button.grid(row=2, column=0, sticky="ew", padx=(0, 3), pady=2)
        self.reset_button = ttk.Button(
            simulation_panel, text="Reset", style="Compact.TButton", command=on_reset
        )
        self.reset_button.grid(row=2, column=1, sticky="ew", padx=(3, 0), pady=2)

        bottom = ttk.Frame(self, style="Workspace.TFrame")
        bottom.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        bottom.columnconfigure(0, weight=7, uniform="radar-bottom")
        bottom.columnconfigure(1, weight=3, uniform="radar-bottom")
        activity_panel = _panel(bottom, padding=9)
        activity_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        activity_panel.columnconfigure(0, weight=1)
        activity_panel.rowconfigure(1, weight=1)
        _panel_title(activity_panel, "RECENT ATC ACTIVITY")
        self.activity_tree, activity_scroll = _treeview(
            activity_panel,
            ("time", "pnr", "message"),
            ("TIME", "PNR", "EVENT"),
            (68, 66, 180),
            height=5,
        )
        self.activity_tree.grid(row=1, column=0, sticky="nsew")
        activity_scroll.grid(row=1, column=1, sticky="ns")
        self._activity_revision = -1

        action_panel = _panel(bottom, padding=9)
        action_panel.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        action_panel.columnconfigure((0, 1), weight=1)
        _panel_title(action_panel, "HOLDING / APPROACH CONTROL · STAGE 4B")
        self.landing_clearance_button = ttk.Button(
            action_panel,
            text="Landing\nClearance",
            style="Compact.TButton",
            state=tk.DISABLED,
            command=on_landing_clearance,
        )
        self.landing_clearance_button.grid(
            row=1, column=0, sticky="ew", padx=3, pady=3
        )
        self.takeoff_clearance_button = ttk.Button(
            action_panel,
            text="Takeoff\nClearance",
            style="Compact.TButton",
            state=tk.DISABLED,
            command=on_takeoff_clearance,
        )
        self.takeoff_clearance_button.grid(
            row=1, column=1, sticky="ew", padx=3, pady=3
        )
        self.enter_holding_button = ttk.Button(
            action_panel,
            text="Enter Holding",
            style="Compact.TButton",
            state=tk.DISABLED,
            command=on_enter_holding,
        )
        self.enter_holding_button.grid(
            row=2, column=0, sticky="ew", padx=3, pady=3
        )
        self.go_around_button = ttk.Button(
            action_panel,
            text="Go-Around",
            style="CompactEmergency.TButton",
            state=tk.DISABLED,
            command=on_go_around,
        )
        self.go_around_button.grid(
            row=2, column=1, sticky="ew", padx=3, pady=3
        )
        self._disabled_action(action_panel, "Emergency Priority", 3, 0, EMERGENCY, 2)
        self.action_feedback = tk.Label(
            action_panel,
            text="Select a departure awaiting clearance.",
            bg=PANELS,
            fg=SECONDARY_TEXT,
            font=(FONT, 8),
            anchor="w",
            justify=tk.LEFT,
            wraplength=250,
        )
        self.action_feedback.grid(
            row=4, column=0, columnspan=2, sticky="ew", padx=3, pady=(5, 0)
        )
        self._last_action_context: tuple[object, ...] | None = None
        self._action_feedback_override: tuple[tuple[object, ...], str, str] | None = None
        self.refresh()

    @staticmethod
    def _legend_item(parent: ttk.Frame, color: str, text: str) -> None:
        item = tk.Label(
            parent, text=f"●  {text}", bg=BACKGROUND, fg=color,
            font=(FONT, 8, "bold")
        )
        item.pack(side=tk.LEFT, padx=(0, 16))

    @staticmethod
    def _disabled_action(
        parent: ttk.Frame,
        text: str,
        row: int,
        column: int,
        color: str = ACCENT,
        columnspan: int = 1,
    ) -> None:
        button = ttk.Button(
            parent,
            text=text.replace(" ", "\n", 1),
            style="CompactEmergency.TButton" if color == EMERGENCY else "Compact.TButton",
            state=tk.DISABLED,
            takefocus=False,
        )
        button.grid(
            row=row,
            column=column,
            columnspan=columnspan,
            sticky="ew",
            padx=3,
            pady=3,
        )

    def refresh(self) -> None:
        self.refresh_selected_aircraft_details()
        for runway, status in zip(self.runways, self.runway_statuses):
            operation = (
                f" · {runway.operation_type.value}"
                if runway.operation_type is not None
                else ""
            )
            assigned = f" · {runway.assigned_aircraft_id}" if runway.assigned_aircraft_id else ""
            runway_state, color = _runway_status(runway)
            clearing_time = (
                f" · {_clearing_time(runway)}"
                if runway.status is RunwayStatus.CLEARING
                else ""
            )
            status.configure(
                text=(
                    f"{runway.runway_id}  {runway_state}{operation}"
                    f"{assigned}{clearing_time}"
                ),
                fg=color,
            )
        self.set_simulation_status(self.simulation.state)
        self.radar.refresh_aircraft_positions()
        activity_log = self.simulation.activity_log
        if self._activity_revision != activity_log.revision:
            self._activity_revision = activity_log.revision
            _sync_recent_events(
                self.activity_tree,
                activity_log.events,
                categories={
                    EventCategory.AIRCRAFT,
                    EventCategory.ATC,
                    EventCategory.RUNWAY,
                },
                limit=6,
            )

    def refresh_selected_aircraft_details(self) -> None:
        flight = self.get_selected()
        context = (
            flight.flight_id if flight else None,
            flight.status if flight else None,
            flight.flight_phase if flight else None,
            flight.route_mode if flight else None,
            flight.assigned_runway if flight else None,
            tuple(
                runway.runway_id
                for runway in self.runways
                if runway.eligible_for_operation
            ),
        )
        if context != self._last_action_context:
            self._last_action_context = context
            self._action_feedback_override = None
        self._refresh_action_feedback(flight, context)
        if flight is None:
            self.landing_clearance_button.configure(state=tk.DISABLED)
            self.takeoff_clearance_button.configure(state=tk.DISABLED)
            self.enter_holding_button.configure(state=tk.DISABLED)
            self.go_around_button.configure(state=tk.DISABLED)
            if self.details_container.winfo_manager() == "grid":
                self.details_container.grid_remove()
            if self.selection_message.winfo_manager() != "grid":
                self.selection_message.grid()
            return

        if self.selection_message.winfo_manager() == "grid":
            self.selection_message.grid_remove()
        if self.details_container.winfo_manager() != "grid":
            self.details_container.grid()
        values = (
            flight.flight_id,
            flight.status,
            flight.flight_phase.value,
            flight.route_mode.value,
            _altitude_text(flight.altitude_ft),
            _altitude_text(flight.holding_altitude_ft)
            if flight.flight_type == "Arrival"
            else "—",
            str(flight.holding_circuit_count),
            str(flight.go_around_count),
            flight.flight_type,
            f"{flight.speed_kts} kt",
            flight.assigned_runway or "—",
        )
        for field, value in zip(self.detail_labels, values):
            label = field.upper()
            display_value = f"{label}  {value}"
            if self.detail_labels[field].cget("text") != display_value:
                self.detail_labels[field].configure(text=display_value)
        self.altitude_indicator.configure(
            value=min(
                max(flight.altitude_ft, 0.0),
                float(self.altitude_indicator.cget("maximum")),
            )
        )
        can_clear = self.simulation.can_grant_landing_clearance(flight.flight_id)
        self.landing_clearance_button.configure(
            state=tk.NORMAL if can_clear else tk.DISABLED
        )
        can_takeoff = self.simulation.can_grant_takeoff_clearance(flight.flight_id)
        self.takeoff_clearance_button.configure(
            state=tk.NORMAL if can_takeoff else tk.DISABLED
        )
        self.enter_holding_button.configure(
            state=(
                tk.NORMAL
                if self.simulation.can_enter_holding(flight.flight_id)
                else tk.DISABLED
            )
        )
        self.go_around_button.configure(
            state=(
                tk.NORMAL
                if self.simulation.can_go_around(flight.flight_id)
                else tk.DISABLED
            )
        )

    def show_action_feedback(self, message: str, warning: bool = False) -> None:
        context = self._last_action_context or ()
        color = WARNING if warning else SUCCESS
        self._action_feedback_override = (context, message, color)
        self.action_feedback.configure(text=message, fg=color)

    def _refresh_action_feedback(
        self,
        flight: Aircraft | None,
        context: tuple[object, ...],
    ) -> None:
        if self._action_feedback_override is not None:
            override_context, message, color = self._action_feedback_override
            if override_context == context:
                self.action_feedback.configure(text=message, fg=color)
                return
            self._action_feedback_override = None

        if flight is None:
            message = "Select an aircraft to view approach controls."
        elif flight.status == HOLDING:
            message = (
                f"Holding at {_altitude_text(flight.holding_altitude_ft)} · "
                f"{flight.holding_circuit_count} circuits completed."
            )
        elif flight.status == ENTERING_HOLD:
            message = "Aircraft is joining its holding pattern."
        elif flight.status == EXITING_HOLD:
            message = "Landing cleared; aircraft is exiting holding."
        elif flight.status == GOING_AROUND:
            message = "Go-around in progress; returning to holding."
        elif flight.flight_type == "Arrival":
            if flight.status == "Awaiting landing clearance":
                message = (
                    "Arrival ready for landing clearance."
                    if self.simulation.can_grant_landing_clearance(flight.flight_id)
                    else "No operational runway is available for landing clearance."
                )
            else:
                message = f"Arrival phase: {flight.status}."
        elif flight.status == AWAITING_TAKEOFF_CLEARANCE and not any(
            runway.eligible_for_operation for runway in self.runways
        ):
            message = "No operational runway is available for takeoff clearance."
        elif flight.status == AWAITING_TAKEOFF_CLEARANCE:
            message = "Departure ready for takeoff clearance."
        else:
            message = f"Departure phase: {flight.status}."
        self.action_feedback.configure(text=message, fg=SECONDARY_TEXT)

    def set_simulation_status(self, state: SimulationState) -> None:
        status, color = _status_text(state)
        self.simulation_status.configure(text=status, fg=color)
        _set_simulation_control_states(
            state,
            self.start_button,
            self.pause_button,
            self.resume_button,
        )

    def refresh_aircraft_positions(self) -> None:
        self.radar.refresh_aircraft_positions()

class FlightManagementPage(ttk.Frame):
    """Filterable flight table with persistent application-level selection."""

    def __init__(
        self,
        parent: tk.Misc,
        aircraft: Sequence[Aircraft],
        simulation: SimulationController,
        get_selected: SelectedAircraft,
        on_select: AircraftSelectionHandler,
    ) -> None:
        super().__init__(parent, style="Workspace.TFrame", padding=18)
        self.aircraft = aircraft
        self.simulation = simulation
        self.get_selected = get_selected
        self.on_select = on_select
        self._refreshing = False
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)
        _page_heading(self, "Flight Management", "Search and inspect the in-memory sample flight list")

        summary = ttk.Frame(self, style="Workspace.TFrame")
        summary.grid(row=2, column=0, sticky="ew", pady=(12, 8))
        for column in range(4):
            summary.columnconfigure(column, weight=1, uniform="flight-summary")
        self.summary_values: dict[str, tk.Label] = {}
        for column, (key, title, color) in enumerate(
            (
                ("total", "TOTAL FLIGHTS", ACCENT),
                ("arrivals", "ACTIVE ARRIVALS", SUCCESS),
                ("departures", "ACTIVE DEPARTURES", WARNING),
                ("completed", "ROUTES COMPLETE", ACCENT),
            )
        ):
            card = _panel(summary, padding=10)
            card.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 7, 0))
            tk.Label(
                card, text=title, bg=PANELS, fg=SECONDARY_TEXT,
                font=(FONT, 8, "bold"), anchor="w"
            ).pack(fill="x")
            value = tk.Label(
                card, text="0", bg=PANELS, fg=color,
                font=(FONT, 16, "bold"), anchor="w"
            )
            value.pack(fill="x", pady=(3, 0))
            self.summary_values[key] = value

        search_row = ttk.Frame(self, style="Workspace.TFrame")
        search_row.grid(row=3, column=0, sticky="ew", pady=(5, 8))
        search_row.columnconfigure(1, weight=1)
        ttk.Label(search_row, text="Search flights", style="PageSubtitle.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 10)
        )
        self.search_value = tk.StringVar()
        self.search_entry = ttk.Entry(search_row, textvariable=self.search_value)
        self.search_entry.grid(row=0, column=1, sticky="ew")
        self.search_value.trace_add("write", self._on_search)

        table_panel = _panel(self, padding=10)
        table_panel.grid(row=4, column=0, sticky="nsew")
        table_panel.columnconfigure(0, weight=1)
        table_panel.rowconfigure(0, weight=1)
        self.flight_tree, scrollbar = _treeview(
            table_panel,
            (
                "pnr",
                "type",
                "altitude",
                "phase",
                "route",
                "circuits",
                "speed",
                "status",
                "runway",
            ),
            (
                "PNR",
                "FLIGHT TYPE",
                "ALTITUDE",
                "FLIGHT PHASE",
                "ROUTE MODE",
                "HOLD CIRCUITS",
                "GROUND SPEED",
                "STATUS",
                "RUNWAY",
            ),
            (82, 88, 78, 105, 98, 74, 90, 108, 70),
            height=7,
        )
        self.flight_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.flight_tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        details = _panel(self, padding=12)
        details.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        details.columnconfigure((0, 1, 2), weight=1)
        ttk.Label(details, text="SELECTED FLIGHT DETAILS", style="PanelTitle.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8)
        )
        self.detail_values: dict[str, tk.Label] = {}
        self.route_progress_bar: ttk.Progressbar | None = None
        self.route_progress_value: tk.Label | None = None
        detail_fields = (
            "PNR",
            "Flight type",
            "Operational status",
            "Flight phase",
            "Route mode",
            "Altitude",
            "Holding altitude",
            "Holding circuits",
            "Go-arounds",
            "Ground speed",
            "Assigned runway",
            "Route progress",
        )
        for index, field in enumerate(detail_fields):
            holder = ttk.Frame(details, style="Panel.TFrame")
            holder.grid(
                row=1 + index // 3,
                column=index % 3,
                sticky="ew",
                padx=(0, 8),
                pady=(0, 5),
            )
            display_field = field.upper()
            tk.Label(
                holder, text=display_field, bg=PANELS, fg=SECONDARY_TEXT,
                font=(FONT, 7, "bold"), anchor="w"
            ).pack(anchor="w")
            if field == "Route progress":
                self.route_progress_value = tk.Label(
                    holder, text="—", bg=PANELS, fg=PRIMARY_TEXT,
                    font=(FONT, 8, "bold"), anchor="w"
                )
                self.route_progress_value.pack(anchor="w", pady=(2, 1))
                self.route_progress_bar = ttk.Progressbar(
                    holder, maximum=100, style="Aero.Horizontal.TProgressbar"
                )
                self.route_progress_bar.pack(fill="x", pady=(1, 0))
            else:
                value = tk.Label(
                    holder, text="—", bg=PANELS, fg=PRIMARY_TEXT,
                    font=(FONT, 9, "bold"), anchor="w"
                )
                value.pack(anchor="w", pady=(4, 0))
                self.detail_values[field] = value
        self.refresh()

    def _on_search(self, *_args: str) -> None:
        _ = _args
        self.refresh()

    def _on_tree_select(self, _event: tk.Event) -> None:
        _ = _event
        if self._refreshing:
            return
        selected = self.flight_tree.selection()
        if not selected:
            return
        pnr_id = selected[0]
        flight = next((item for item in self.aircraft if item.flight_id == pnr_id), None)
        if flight is not None and flight is not self.get_selected():
            self.on_select(flight)

    def refresh(self) -> None:
        self.sync_from_model()

    def sync_from_model(self) -> None:
        query = self.search_value.get().strip().casefold()
        visible_flights = []
        for flight in self.aircraft:
            searchable = " ".join(
                (
                    flight.flight_id,
                    flight.flight_type,
                    f"{flight.altitude_ft:.0f}",
                    flight.flight_phase.value,
                    flight.route_mode.value,
                    str(flight.holding_circuit_count),
                    str(flight.go_around_count),
                    str(flight.speed_kts),
                    flight.status,
                    flight.assigned_runway or "",
                )
            ).casefold()
            if not query or query in searchable:
                visible_flights.append(flight)

        visible_ids = {flight.flight_id for flight in visible_flights}
        y_position = self.flight_tree.yview()[0]
        structure_changed = False
        self._refreshing = True
        try:
            for item_id in self.flight_tree.get_children():
                if item_id not in visible_ids:
                    self.flight_tree.delete(item_id)
                    structure_changed = True

            for index, flight in enumerate(visible_flights):
                values = (
                    flight.flight_id,
                    flight.flight_type,
                    _altitude_text(flight.altitude_ft),
                    flight.flight_phase.value,
                    flight.route_mode.value,
                    str(flight.holding_circuit_count),
                    f"{flight.speed_kts} kt",
                    flight.status,
                    flight.assigned_runway or "—",
                )
                if self.flight_tree.exists(flight.flight_id):
                    if tuple(self.flight_tree.item(flight.flight_id, "values")) != values:
                        self.flight_tree.item(flight.flight_id, values=values)
                else:
                    self.flight_tree.insert("", tk.END, iid=flight.flight_id, values=values)
                    structure_changed = True

                current_order = self.flight_tree.get_children()
                if index >= len(current_order) or current_order[index] != flight.flight_id:
                    self.flight_tree.move(flight.flight_id, "", index)
                    structure_changed = True

            selected_flight = self.get_selected()
            selected_id = (
                selected_flight.flight_id
                if selected_flight is not None
                and self.flight_tree.exists(selected_flight.flight_id)
                else None
            )
            selected_rows = self.flight_tree.selection()
            if selected_id is not None:
                if selected_rows != (selected_id,):
                    self.flight_tree.selection_set(selected_id)
                if self.flight_tree.focus() != selected_id:
                    self.flight_tree.focus(selected_id)
            elif selected_rows:
                self.flight_tree.selection_remove(*selected_rows)

            if structure_changed:
                self.flight_tree.yview_moveto(y_position)
        finally:
            self._refreshing = False

        self._refresh_summary()
        self._refresh_details()

    def _refresh_summary(self) -> None:
        values = {
            "total": str(len(self.aircraft)),
            "arrivals": str(
                sum(
                    flight.flight_type == "Arrival" and flight.status != LANDED
                    for flight in self.aircraft
                )
            ),
            "departures": str(
                sum(
                    flight.flight_type == "Departure" and not flight.route_complete
                    for flight in self.aircraft
                )
            ),
            "completed": str(sum(flight.route_complete for flight in self.aircraft)),
        }
        for key, value in values.items():
            label = self.summary_values[key]
            if label.cget("text") != value:
                label.configure(text=value)

    def _refresh_details(self) -> None:
        flight = self.get_selected()
        values = {
            "PNR": flight.flight_id if flight else "—",
            "Flight type": flight.flight_type if flight else "—",
            "Operational status": flight.status if flight else "No selection",
            "Flight phase": flight.flight_phase.value if flight else "—",
            "Route mode": flight.route_mode.value if flight else "—",
            "Altitude": _altitude_text(flight.altitude_ft) if flight else "—",
            "Holding altitude": (
                _altitude_text(flight.holding_altitude_ft)
                if flight and flight.flight_type == "Arrival"
                else "—"
            ),
            "Holding circuits": str(flight.holding_circuit_count) if flight else "—",
            "Go-arounds": str(flight.go_around_count) if flight else "—",
            "Ground speed": f"{flight.speed_kts} kt" if flight else "—",
            "Assigned runway": (flight.assigned_runway or "—") if flight else "—",
        }
        for field, value in values.items():
            label = self.detail_values[field]
            if label.cget("text") != value:
                label.configure(text=value)

        has_route = flight is not None and len(flight.route_waypoints) >= 2
        progress = flight.route_progress * 100 if has_route and flight else 0
        progress_text = f"{progress:.0f}%" if has_route else "—"
        if self.route_progress_value is not None:
            if self.route_progress_value.cget("text") != progress_text:
                self.route_progress_value.configure(text=progress_text)
        if self.route_progress_bar is not None:
            current_progress = float(self.route_progress_bar.cget("value"))
            if current_progress != progress:
                self.route_progress_bar.configure(value=progress)


class RunwayOperationsPage(ttk.Frame):
    """Runway state cards and live operations backed by shared runway models."""

    def __init__(
        self,
        parent: tk.Misc,
        runways: Sequence[Runway],
        aircraft: Sequence[Aircraft],
        simulation: SimulationController,
    ) -> None:
        super().__init__(parent, style="Workspace.TFrame", padding=18)
        self.runways = runways
        self.aircraft = aircraft
        self.simulation = simulation
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        _page_heading(
            self,
            "Runway Operations",
            "Physical occupancy and operational availability are tracked separately",
        )
        ttk.Label(
            self,
            text="Clearance and closure requests are coordinated by the shared controller.",
            style="PageSubtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(14, 8))

        cards = ttk.Frame(self, style="Workspace.TFrame")
        cards.grid(row=2, column=0, sticky="ew")
        for column in range(2):
            cards.columnconfigure(column, weight=1, uniform="runways")
        for row in range((len(self.runways) + 1) // 2):
            cards.rowconfigure(row, weight=1)
        self.status_labels: list[tk.Label] = []
        self.operational_labels: list[tk.Label] = []
        self.assignment_labels: list[tk.Label] = []
        self.operation_labels: list[tk.Label] = []
        self.remaining_labels: list[tk.Label] = []
        self.closure_reason_labels: list[tk.Label] = []
        self.close_buttons: dict[str, ttk.Button] = {}
        self.reopen_buttons: dict[str, ttk.Button] = {}
        self.cancel_closure_buttons: dict[str, ttk.Button] = {}
        for index, runway in enumerate(self.runways):
            row, column = divmod(index, 2)
            card = _panel(cards, padding=20)
            card.grid(
                row=row,
                column=column,
                sticky="nsew",
                padx=(0 if column == 0 else 9, 0),
                pady=(0 if row == 0 else 9, 0),
            )
            card.columnconfigure(1, weight=1)
            tk.Label(
                card, text=f"RUNWAY  {runway.runway_id}", bg=PANELS, fg=PRIMARY_TEXT,
                font=(FONT, 14, "bold"), anchor="w"
            ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 16))
            tk.Label(
                card, text="CURRENT STATUS", bg=PANELS, fg=SECONDARY_TEXT,
                font=(FONT, 8, "bold"), anchor="w"
            ).grid(row=1, column=0, sticky="w", pady=6)
            status = tk.Label(
                card, text="", bg=PANELS, font=(FONT, 10, "bold"), anchor="e"
            )
            status.grid(row=1, column=1, sticky="e", pady=6)
            self.status_labels.append(status)
            tk.Label(
                card, text="OPERATIONAL", bg=PANELS, fg=SECONDARY_TEXT,
                font=(FONT, 8, "bold"), anchor="w"
            ).grid(row=2, column=0, sticky="w", pady=6)
            operational = tk.Label(
                card, text="", bg=PANELS, font=(FONT, 9, "bold"), anchor="e"
            )
            operational.grid(row=2, column=1, sticky="e", pady=6)
            self.operational_labels.append(operational)
            tk.Label(
                card, text="ASSIGNED PNR", bg=PANELS, fg=SECONDARY_TEXT,
                font=(FONT, 8, "bold"), anchor="w"
            ).grid(row=3, column=0, sticky="w", pady=6)
            assignment = tk.Label(
                card, text="Not assigned", bg=PANELS, fg=PRIMARY_TEXT,
                font=(FONT, 9), anchor="e"
            )
            assignment.grid(row=3, column=1, sticky="e", pady=6)
            self.assignment_labels.append(assignment)
            tk.Label(
                card, text="OPERATION", bg=PANELS, fg=SECONDARY_TEXT,
                font=(FONT, 8, "bold"), anchor="w"
            ).grid(row=4, column=0, sticky="w", pady=6)
            operation = tk.Label(
                card, text="—", bg=PANELS, fg=PRIMARY_TEXT,
                font=(FONT, 9), anchor="e"
            )
            operation.grid(row=4, column=1, sticky="e", pady=6)
            self.operation_labels.append(operation)
            tk.Label(
                card, text="CLEARING TIME", bg=PANELS, fg=SECONDARY_TEXT,
                font=(FONT, 8, "bold"), anchor="w"
            ).grid(row=5, column=0, sticky="w", pady=6)
            remaining = tk.Label(
                card, text="—", bg=PANELS, fg=PRIMARY_TEXT,
                font=(FONT, 9), anchor="e"
            )
            remaining.grid(row=5, column=1, sticky="e", pady=6)
            self.remaining_labels.append(remaining)
            tk.Label(
                card, text="CLOSURE REASON", bg=PANELS, fg=SECONDARY_TEXT,
                font=(FONT, 8, "bold"), anchor="w"
            ).grid(row=6, column=0, sticky="w", pady=6)
            closure_reason = tk.Label(
                card, text="—", bg=PANELS, fg=PRIMARY_TEXT,
                font=(FONT, 9), anchor="e", wraplength=250, justify=tk.RIGHT
            )
            closure_reason.grid(row=6, column=1, sticky="e", pady=6)
            self.closure_reason_labels.append(closure_reason)
            ttk.Separator(card).grid(row=7, column=0, columnspan=2, sticky="ew", pady=12)
            actions = ttk.Frame(card, style="Panel.TFrame")
            actions.grid(row=8, column=0, columnspan=2, sticky="ew")
            actions.columnconfigure((0, 1, 2), weight=1)
            close_button = ttk.Button(
                actions,
                text="Close Runway",
                style="Secondary.TButton",
                command=lambda selected=runway: self._request_runway_closure(selected),
            )
            close_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
            self.close_buttons[runway.runway_id] = close_button
            reopen_button = ttk.Button(
                actions,
                text="Reopen",
                style="Secondary.TButton",
                command=lambda selected=runway: self._reopen_runway(selected),
            )
            reopen_button.grid(row=0, column=1, sticky="ew", padx=3)
            self.reopen_buttons[runway.runway_id] = reopen_button
            cancel_button = ttk.Button(
                actions,
                text="Cancel Pending",
                style="Secondary.TButton",
                command=lambda selected=runway: self._cancel_runway_closure(selected),
            )
            cancel_button.grid(row=0, column=2, sticky="ew", padx=(3, 0))
            self.cancel_closure_buttons[runway.runway_id] = cancel_button

        operations_panel = _panel(self, padding=10)
        operations_panel.grid(row=3, column=0, sticky="nsew", pady=(10, 0))
        operations_panel.columnconfigure(0, weight=1)
        operations_panel.rowconfigure(1, weight=1)
        _panel_title(operations_panel, "ACTIVE RUNWAY OPERATIONS")
        self.operations_tree, operations_scroll = _treeview(
            operations_panel,
            ("runway", "pnr", "operation", "state", "remaining"),
            ("RUNWAY", "PNR", "OPERATION", "STATE", "CLEARING TIME"),
            (100, 110, 110, 110, 120),
            height=5,
        )
        self.operations_tree.grid(row=1, column=0, sticky="nsew")
        operations_scroll.grid(row=1, column=1, sticky="ns")
        self.empty_operations_label = tk.Label(
            operations_panel,
            text="No active runway operations.",
            bg=PANELS,
            fg=SECONDARY_TEXT,
            font=(FONT, 9),
            anchor="center",
        )
        self.empty_operations_label.grid(row=1, column=0, sticky="nsew")
        self.refresh()

    def refresh(self) -> None:
        for index, runway in enumerate(self.runways):
            status = self.status_labels[index]
            text, color = _runway_status(runway)
            if status.cget("text") != text or status.cget("fg") != color:
                status.configure(text=text, fg=color)
            operational_status = runway.operational_status
            operational_color = {
                RunwayOperationalStatus.OPEN: SUCCESS,
                RunwayOperationalStatus.CLOSURE_PENDING: RUNWAY_PENDING_CLOSURE_COLOR,
                RunwayOperationalStatus.CLOSED: RUNWAY_CLOSED_COLOR,
                RunwayOperationalStatus.UNAVAILABLE: RUNWAY_UNAVAILABLE_COLOR,
            }[operational_status]
            operational_text = operational_status.value
            if (
                self.operational_labels[index].cget("text") != operational_text
                or self.operational_labels[index].cget("fg") != operational_color
            ):
                self.operational_labels[index].configure(
                    text=operational_text,
                    fg=operational_color,
                )
            self.closure_reason_labels[index].configure(
                text=runway.closure_reason or "—"
            )
            self.close_buttons[runway.runway_id].configure(
                state=(
                    tk.NORMAL
                    if self.simulation.can_close_runway(runway.runway_id)
                    else tk.DISABLED
                )
            )
            self.reopen_buttons[runway.runway_id].configure(
                state=(
                    tk.NORMAL
                    if self.simulation.can_reopen_runway(runway.runway_id)
                    else tk.DISABLED
                )
            )
            self.cancel_closure_buttons[runway.runway_id].configure(
                state=(
                    tk.NORMAL
                    if self.simulation.can_cancel_pending_runway_closure(
                        runway.runway_id
                    )
                    else tk.DISABLED
                )
            )
        for runway, assignment in zip(self.runways, self.assignment_labels):
            text = runway.assigned_aircraft_id or "Not assigned"
            if assignment.cget("text") != text:
                assignment.configure(text=text)
        for runway, operation in zip(self.runways, self.operation_labels):
            text = runway.operation_type.value if runway.operation_type else "—"
            if operation.cget("text") != text:
                operation.configure(text=text)
        for runway, remaining in zip(self.runways, self.remaining_labels):
            text = _clearing_time(runway)
            if remaining.cget("text") != text:
                remaining.configure(text=text)

        active_runways = tuple(
            runway
            for runway in self.runways
            if runway.status
            in (RunwayStatus.RESERVED, RunwayStatus.OCCUPIED, RunwayStatus.CLEARING)
        )
        active_ids = {runway.runway_id for runway in active_runways}
        for item_id in self.operations_tree.get_children():
            if item_id not in active_ids:
                self.operations_tree.delete(item_id)
        for index, runway in enumerate(active_runways):
            values = (
                runway.runway_id,
                runway.assigned_aircraft_id or "—",
                runway.operation_type.value if runway.operation_type else "—",
                runway.status.value,
                _clearing_time(runway),
            )
            if self.operations_tree.exists(runway.runway_id):
                if tuple(self.operations_tree.item(runway.runway_id, "values")) != values:
                    self.operations_tree.item(runway.runway_id, values=values)
            else:
                self.operations_tree.insert(
                    "", tk.END, iid=runway.runway_id, values=values
                )
            self.operations_tree.move(runway.runway_id, "", index)

        if active_runways:
            self.empty_operations_label.grid_remove()
            self.operations_tree.grid()
        else:
            self.operations_tree.grid_remove()
            self.empty_operations_label.grid()

    def _request_runway_closure(self, runway: Runway) -> None:
        reason = simpledialog.askstring(
            "Close Runway",
            f"Reason for closing runway {runway.runway_id}:",
            parent=self.winfo_toplevel(),
        )
        if reason is None:
            return
        if not reason.strip():
            messagebox.showwarning(
                "Closure reason required",
                "Enter a reason before closing the runway.",
                parent=self.winfo_toplevel(),
            )
            return
        if not self.simulation.close_runway(runway.runway_id, reason):
            messagebox.showwarning(
                "Runway closure rejected",
                "The runway is not eligible for a new closure request.",
                parent=self.winfo_toplevel(),
            )

    def _reopen_runway(self, runway: Runway) -> None:
        if not self.simulation.reopen_runway(runway.runway_id):
            messagebox.showwarning(
                "Runway reopen rejected",
                "Only a temporarily closed, physically clear runway can be reopened.",
                parent=self.winfo_toplevel(),
            )

    def _cancel_runway_closure(self, runway: Runway) -> None:
        if not self.simulation.cancel_pending_runway_closure(runway.runway_id):
            messagebox.showwarning(
                "Closure cancellation rejected",
                "This runway has no pending closure to cancel.",
                parent=self.winfo_toplevel(),
            )


class ActivityLogsPage(ttk.Frame):
    """Filterable live history backed by the shared simulation event log."""

    def __init__(self, parent: tk.Misc, simulation: SimulationController) -> None:
        super().__init__(parent, style="Workspace.TFrame", padding=18)
        self.simulation = simulation
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)
        _page_heading(self, "Activity Logs", "Current session event history · newest events append automatically")

        summary = ttk.Frame(self, style="Workspace.TFrame")
        summary.grid(row=2, column=0, sticky="ew", pady=(12, 8))
        for column in range(4):
            summary.columnconfigure(column, weight=1, uniform="event-summary")
        self.summary_values: dict[str, tk.Label] = {}
        for column, (key, title, color) in enumerate(
            (
                ("total", "TOTAL EVENTS", ACCENT),
                ("atc", "ATC EVENTS", SUCCESS),
                ("runway", "RUNWAY EVENTS", WARNING),
                ("warnings", "WARNINGS / CRITICAL", EMERGENCY),
            )
        ):
            card = _panel(summary, padding=10)
            card.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 7, 0))
            tk.Label(
                card, text=title, bg=PANELS, fg=SECONDARY_TEXT,
                font=(FONT, 8, "bold"), anchor="w"
            ).pack(fill="x")
            value = tk.Label(
                card, text="0", bg=PANELS, fg=color,
                font=(FONT, 16, "bold"), anchor="w"
            )
            value.pack(fill="x", pady=(3, 0))
            self.summary_values[key] = value

        filters = ttk.Frame(self, style="Workspace.TFrame")
        filters.grid(row=3, column=0, sticky="ew", pady=(4, 8))
        filters.columnconfigure(3, weight=1)
        ttk.Label(filters, text="Category", style="PageSubtitle.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 7)
        )
        self.category_value = tk.StringVar(value="ALL")
        self.category_filter = ttk.Combobox(
            filters,
            textvariable=self.category_value,
            values=("ALL", *(category.value for category in EventCategory)),
            state="readonly",
            width=14,
            style="Dark.TCombobox",
        )
        self.category_filter.grid(row=0, column=1, sticky="w", padx=(0, 18))
        self.category_value.trace_add("write", self._on_filters_changed)
        ttk.Label(filters, text="Search PNR / message", style="PageSubtitle.TLabel").grid(
            row=0, column=2, sticky="w", padx=(0, 7)
        )
        self.search_value = tk.StringVar()
        self.search_entry = ttk.Entry(filters, textvariable=self.search_value)
        self.search_entry.grid(row=0, column=3, sticky="ew")
        self.search_value.trace_add("write", self._on_filters_changed)

        panel = _panel(self, padding=10)
        panel.grid(row=4, column=0, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(0, weight=1)
        self.log_tree, scrollbar = _treeview(
            panel,
            ("time", "pnr", "category", "severity", "message"),
            ("TIME", "PNR", "CATEGORY", "SEVERITY", "MESSAGE"),
            (82, 92, 105, 88, 360),
            height=12,
        )
        self.log_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_tree.tag_configure(EventSeverity.SUCCESS.value, foreground=SUCCESS)
        self.log_tree.tag_configure(EventSeverity.WARNING.value, foreground=WARNING)
        self.log_tree.tag_configure(EventSeverity.CRITICAL.value, foreground=EMERGENCY)
        self._known_events: tuple[ActivityEvent, ...] = ()
        self._activity_revision = -1
        self.refresh()

    def refresh(self) -> None:
        self.sync_from_model()

    def sync_from_model(self) -> None:
        activity_log = self.simulation.activity_log
        if self._activity_revision == activity_log.revision:
            return

        events = activity_log.events
        old_ids = tuple(event.event_id for event in self._known_events)
        new_ids = tuple(event.event_id for event in events)
        is_append = (
            len(new_ids) >= len(old_ids)
            and new_ids[:len(old_ids)] == old_ids
        )
        y_position = self.log_tree.yview()[0]
        was_at_bottom = self.log_tree.yview()[1] >= 0.999
        self._known_events = events
        self._activity_revision = activity_log.revision

        if is_append:
            for event in events[len(old_ids):]:
                if self._matches_filter(event):
                    self._insert_event(event)
            if was_at_bottom:
                self.log_tree.yview_moveto(1.0)
            else:
                self.log_tree.yview_moveto(y_position)
        else:
            self._render_events()
            self.log_tree.yview_moveto(y_position)
        self._refresh_summary()

    def _on_filters_changed(self, *_args: str) -> None:
        _ = _args
        self._render_events()

    def _matches_filter(self, event: ActivityEvent) -> bool:
        category = self.category_value.get()
        if category != "ALL" and event.category.value != category:
            return False
        query = self.search_value.get().strip().casefold()
        searchable = f"{event.pnr_id or ''} {event.message}".casefold()
        return not query or query in searchable

    def _render_events(self) -> None:
        y_position = self.log_tree.yview()[0]
        current_items = self.log_tree.get_children()
        if current_items:
            self.log_tree.delete(*current_items)
        for event in self._known_events:
            if self._matches_filter(event):
                self._insert_event(event)
        self.log_tree.yview_moveto(y_position)

    def _insert_event(self, event: ActivityEvent) -> None:
        self.log_tree.insert(
            "",
            tk.END,
            iid=str(event.event_id),
            values=(
                event.display_time,
                event.pnr_id or "—",
                event.category.value,
                event.severity.value,
                event.message,
            ),
            tags=(event.severity.value,),
        )

    def _refresh_summary(self) -> None:
        events = self._known_events
        values = {
            "total": str(len(events)),
            "atc": str(sum(event.category is EventCategory.ATC for event in events)),
            "runway": str(sum(event.category is EventCategory.RUNWAY for event in events)),
            "warnings": str(
                sum(
                    event.severity in (EventSeverity.WARNING, EventSeverity.CRITICAL)
                    for event in events
                )
            ),
        }
        for key, value in values.items():
            label = self.summary_values[key]
            if label.cget("text") != value:
                label.configure(text=value)

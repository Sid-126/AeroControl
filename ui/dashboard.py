"""Persistent application shell with sidebar navigation and shared page state."""

from datetime import datetime
import tkinter as tk
from tkinter import ttk
from typing import Callable, Sequence

from core.aircraft import Aircraft
from core.runway import Runway
from core.simulation import SimulationController, SimulationState
from ui.pages import (
    ActivityLogsPage,
    FlightManagementPage,
    LiveRadarPage,
    OperationsOverviewPage,
    RunwayOperationsPage,
)
from ui.theme import (
    ACCENT,
    FONT,
    PRIMARY_TEXT,
    SECONDARY_TEXT,
    SIDEBAR,
    SUCCESS,
    WARNING,
    configure_theme,
)


class Dashboard(ttk.Frame):
    """Top-level visual shell; all pages share the same domain objects."""

    NAV_ITEMS = (
        ("overview", "Operations Overview"),
        ("radar", "Live Radar"),
        ("flights", "Flight Management"),
        ("runways", "Runway Operations"),
        ("logs", "Activity Logs"),
    )

    def __init__(
        self,
        master: tk.Misc,
        aircraft: Sequence[Aircraft],
        runways: Sequence[Runway],
        simulation: SimulationController,
        on_start: Callable[[], None],
        on_pause: Callable[[], None],
        on_resume: Callable[[], None],
        on_reset: Callable[[], None],
    ) -> None:
        super().__init__(master, style="App.TFrame")
        configure_theme(master)
        self.aircraft = aircraft
        self.runways = runways
        self.simulation = simulation
        self.active_page = "overview"
        self._unsubscribe_simulation: Callable[[], None] | None = None
        self.columnconfigure(0, weight=0, minsize=220)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

        self._build_header()
        self._build_sidebar()
        self._build_workspace(on_start, on_pause, on_resume, on_reset)
        self._unsubscribe_simulation = self.simulation.subscribe(
            self._on_simulation_update
        )
        self._on_simulation_update(self.simulation)
        self.show_page("overview")

    def _build_header(self) -> None:
        header = ttk.Frame(self, style="Header.TFrame", padding=(20, 10))
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        header.columnconfigure(1, weight=1)
        brand_mark = tk.Label(
            header,
            text="AC",
            bg="#164252",
            fg=ACCENT,
            font=(FONT, 12, "bold"),
            padx=8,
            pady=6,
        )
        brand_mark.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 11))
        tk.Label(
            header,
            text="AeroControl",
            bg=SIDEBAR,
            fg=PRIMARY_TEXT,
            font=(FONT, 16, "bold"),
        ).grid(row=0, column=1, sticky="sw")
        tk.Label(
            header,
            text="AIRPORT OPERATIONS CENTER  /  STAGE 4B",
            bg=SIDEBAR,
            fg=SECONDARY_TEXT,
            font=(FONT, 7, "bold"),
        ).grid(row=1, column=1, sticky="nw", pady=(1, 0))

        status_area = ttk.Frame(header, style="Header.TFrame")
        status_area.grid(row=0, column=2, rowspan=2, sticky="e")
        self.status_dot = tk.Label(
            status_area, text="●", bg=SIDEBAR, fg=WARNING, font=(FONT, 9)
        )
        self.status_dot.grid(row=0, column=0, sticky="e", padx=(0, 6))
        self.status_label = tk.Label(
            status_area,
            text="READY",
            bg=SIDEBAR,
            fg=WARNING,
            font=(FONT, 8, "bold"),
        )
        self.status_label.grid(row=0, column=1, sticky="e")
        self.clock_label = tk.Label(
            status_area,
            text="--:--:--",
            bg=SIDEBAR,
            fg=PRIMARY_TEXT,
            font=("Consolas", 12, "bold"),
        )
        self.clock_label.grid(row=1, column=0, columnspan=2, sticky="e", pady=(3, 0))

    def _build_sidebar(self) -> None:
        sidebar = ttk.Frame(self, style="Sidebar.TFrame", padding=(12, 18))
        sidebar.grid(row=1, column=0, sticky="nsew")
        sidebar.columnconfigure(0, weight=1)
        sidebar.rowconfigure(7, weight=1)
        tk.Label(
            sidebar,
            text="WORKSPACE",
            bg=SIDEBAR,
            fg=SECONDARY_TEXT,
            font=(FONT, 8, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=10, pady=(0, 9))
        self.nav_buttons: dict[str, ttk.Button] = {}
        for row, (key, label) in enumerate(self.NAV_ITEMS, start=1):
            button = ttk.Button(
                sidebar,
                text=label,
                style="Nav.TButton",
                command=lambda page_key=key: self.show_page(page_key),
            )
            button.grid(row=row, column=0, sticky="ew", pady=2)
            self.nav_buttons[key] = button

        separator = ttk.Separator(sidebar)
        separator.grid(row=8, column=0, sticky="ew", padx=8, pady=(12, 12))
        tk.Label(
            sidebar,
            text=(
                "EDUCATIONAL SIMULATION\nRoutes are illustrative.\n"
                "Landing / takeoff clearance, holding,\n"
                "and go-around controls are enabled."
            ),
            bg=SIDEBAR,
            fg=SECONDARY_TEXT,
            font=(FONT, 8),
            justify=tk.LEFT,
            anchor="w",
        ).grid(row=9, column=0, sticky="sw", padx=10)

    def _build_workspace(
        self,
        on_start: Callable[[], None],
        on_pause: Callable[[], None],
        on_resume: Callable[[], None],
        on_reset: Callable[[], None],
    ) -> None:
        self.workspace = ttk.Frame(self, style="Workspace.TFrame")
        self.workspace.grid(row=1, column=1, sticky="nsew")
        self.workspace.columnconfigure(0, weight=1)
        self.workspace.rowconfigure(0, weight=1)
        shared = {
            "aircraft": self.aircraft,
            "runways": self.runways,
            "simulation": self.simulation,
        }
        self.live_radar_page = LiveRadarPage(
            self.workspace,
            **shared,
            get_selected=self.get_selected_aircraft,
            on_select=self.select_aircraft,
            on_start=on_start,
            on_pause=on_pause,
            on_resume=on_resume,
            on_reset=on_reset,
            on_landing_clearance=self.grant_selected_landing_clearance,
            on_takeoff_clearance=self.grant_selected_takeoff_clearance,
            on_enter_holding=self.enter_selected_holding,
            on_go_around=self.go_around_selected_aircraft,
        )
        self.pages: dict[str, ttk.Frame] = {
            "overview": OperationsOverviewPage(
                self.workspace,
                **shared,
                get_selected=self.get_selected_aircraft,
                on_select=self.select_aircraft,
                on_start=on_start,
                on_pause=on_pause,
                on_resume=on_resume,
                on_reset=on_reset,
            ),
            "radar": self.live_radar_page,
            "flights": FlightManagementPage(
                self.workspace,
                aircraft=self.aircraft,
                simulation=self.simulation,
                get_selected=self.get_selected_aircraft,
                on_select=self.select_aircraft,
            ),
            "runways": RunwayOperationsPage(
                self.workspace,
                runways=self.runways,
                aircraft=self.aircraft,
                simulation=self.simulation,
            ),
            "logs": ActivityLogsPage(
                self.workspace,
                simulation=self.simulation,
            ),
        }
        for page in self.pages.values():
            page.grid(row=0, column=0, sticky="nsew")

    def get_selected_aircraft(self) -> Aircraft | None:
        return self.simulation.selected_aircraft

    def select_aircraft(self, aircraft: Aircraft) -> None:
        """Store selection centrally; the controller notifies all dashboard views."""
        self.simulation.select_aircraft(aircraft.flight_id)

    def grant_selected_landing_clearance(self) -> None:
        page = self.live_radar_page
        aircraft = self.get_selected_aircraft()
        if aircraft is None:
            page.show_action_feedback("Select an arrival aircraft first.", warning=True)
            return

        granted = self.simulation.grant_landing_clearance(aircraft.flight_id)
        if granted:
            page.show_action_feedback(
                f"Landing clearance granted for {aircraft.flight_id} "
                f"on runway {aircraft.assigned_runway}."
            )
        else:
            page.show_action_feedback(
                f"Landing clearance rejected for {aircraft.flight_id}. "
                "Check runway availability and Activity Logs.",
                warning=True,
            )

    def grant_selected_takeoff_clearance(self) -> None:
        page = self.live_radar_page
        aircraft = self.get_selected_aircraft()
        if aircraft is None:
            page.show_action_feedback("Select a departure aircraft first.", warning=True)
            return

        granted = self.simulation.grant_takeoff_clearance(aircraft.flight_id)
        if granted:
            page.show_action_feedback(
                f"Takeoff clearance granted for {aircraft.flight_id} "
                f"on runway {aircraft.assigned_runway}."
            )
        else:
            page.show_action_feedback(
                f"Takeoff clearance rejected for {aircraft.flight_id}. "
                "See Activity Logs for details.",
                warning=True,
            )

    def enter_selected_holding(self) -> None:
        page = self.live_radar_page
        aircraft = self.get_selected_aircraft()
        if aircraft is None:
            page.show_action_feedback("Select an arriving aircraft first.", warning=True)
            return
        if self.simulation.enter_holding(aircraft.flight_id):
            page.show_action_feedback(f"{aircraft.flight_id} is entering holding.")
        else:
            page.show_action_feedback(
                f"Holding entry rejected for {aircraft.flight_id}.",
                warning=True,
            )

    def go_around_selected_aircraft(self) -> None:
        page = self.live_radar_page
        aircraft = self.get_selected_aircraft()
        if aircraft is None:
            page.show_action_feedback("Select an arriving aircraft first.", warning=True)
            return
        if self.simulation.go_around(aircraft.flight_id):
            page.show_action_feedback(f"Go-around initiated for {aircraft.flight_id}.")
        else:
            page.show_action_feedback(
                f"Go-around rejected for {aircraft.flight_id}.",
                warning=True,
            )

    def show_page(self, page_key: str) -> None:
        """Raise an already-created page and highlight its navigation button."""
        if page_key not in self.pages:
            raise ValueError(f"Unknown dashboard page: {page_key}")
        self.active_page = page_key
        self.pages[page_key].tkraise()
        refresh = getattr(self.pages[page_key], "refresh", None)
        if refresh is not None:
            refresh()
        for key, button in self.nav_buttons.items():
            button.state(["selected"] if key == page_key else ["!selected"])

    def set_simulation_status(self, state: SimulationState) -> None:
        status, color = {
            SimulationState.READY: ("READY", WARNING),
            SimulationState.RUNNING: ("RUNNING", SUCCESS),
            SimulationState.PAUSED: ("PAUSED", WARNING),
            SimulationState.COMPLETED: ("COMPLETED", ACCENT),
        }[state]
        self.status_label.configure(text=status, fg=color)
        self.status_dot.configure(fg=color)
        for page in self.pages.values():
            refresh = getattr(page, "refresh", None)
            if refresh is not None:
                refresh()

    def _on_simulation_update(self, simulation: SimulationController) -> None:
        if simulation is self.simulation:
            self.set_simulation_status(simulation.state)

    def destroy(self) -> None:
        unsubscribe = self._unsubscribe_simulation
        if unsubscribe is not None:
            self._unsubscribe_simulation = None
            unsubscribe()
        super().destroy()

    def set_current_time(self) -> None:
        self.clock_label.configure(text=datetime.now().strftime("%H:%M:%S"))

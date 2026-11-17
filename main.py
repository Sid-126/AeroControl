"""Application entry point for AeroControl."""

import tkinter as tk

from core.simulation import SimulationController
from data.sample_flights import SAMPLE_FLIGHTS, SAMPLE_RUNWAYS
from ui.dashboard import Dashboard
from ui.theme import BACKGROUND


class AeroControlApp(tk.Tk):
    """Top-level window and application controller."""

    def __init__(self) -> None:
        super().__init__()
        self.title("AeroControl | Airport Operations")
        self.geometry("1440x900")
        self.minsize(1100, 700)
        self.configure(background=BACKGROUND)

        self.dashboard: Dashboard | None = None
        self._clock_after_id: str | None = None
        self._closing = False
        self.simulation = SimulationController(
            SAMPLE_FLIGHTS,
            schedule_after=self.after,
            cancel_after=self.after_cancel,
            runways=SAMPLE_RUNWAYS,
        )
        self.dashboard = Dashboard(
            self,
            aircraft=SAMPLE_FLIGHTS,
            runways=SAMPLE_RUNWAYS,
            simulation=self.simulation,
            on_start=self.start_simulation,
            on_pause=self.pause_simulation,
            on_resume=self.resume_simulation,
            on_reset=self.reset_simulation,
        )
        self.dashboard.pack(fill=tk.BOTH, expand=True)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._refresh_clock()

    def start_simulation(self) -> None:
        self.simulation.start()

    def pause_simulation(self) -> None:
        self.simulation.pause()

    def resume_simulation(self) -> None:
        self.simulation.resume()

    def reset_simulation(self) -> None:
        self.simulation.reset()

    def _refresh_clock(self) -> None:
        if self.dashboard is None or not self.winfo_exists():
            return
        self.dashboard.set_current_time()
        self._clock_after_id = self.after(1000, self._refresh_clock)

    def destroy(self) -> None:
        """Stop all pending work before destroying the single Tk root."""
        if self._closing:
            return
        self._closing = True
        self.simulation.shutdown()
        if self._clock_after_id is not None:
            after_id = self._clock_after_id
            self._clock_after_id = None
            self.after_cancel(after_id)
        super().destroy()


def main() -> None:
    app = AeroControlApp()
    app.mainloop()


if __name__ == "__main__":
    main()

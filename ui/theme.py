"""Shared colors, typography, and ttk styles for AeroControl."""

import tkinter as tk
from tkinter import ttk


BACKGROUND = "#071321"
SIDEBAR = "#0C2032"
PANELS = "#102235"
PANEL_RAISED = "#142B40"
ACCENT = "#5EEAD4"
PRIMARY_TEXT = "#F0F6FC"
SECONDARY_TEXT = "#91A9BC"
WARNING = "#F5BE62"
EMERGENCY = "#F87171"
SUCCESS = "#6FE0AE"
ARRIVAL_COLOR = ACCENT
DEPARTURE_COLOR = WARNING
HOLDING_COLOR = "#A78BFA"
GO_AROUND_COLOR = "#FB7185"
RUNWAY_CLOSED_COLOR = "#B65E62"
RUNWAY_PENDING_CLOSURE_COLOR = "#F5BE62"
RUNWAY_UNAVAILABLE_COLOR = "#394754"
BORDER = "#203B50"
RADAR_BACKGROUND = "#091A28"
RADAR_GRID = "#1C3B4B"

FONT = "Segoe UI"
MONO_FONT = "Consolas"


def configure_theme(root: tk.Misc) -> None:
    """Apply the shared dark palette to ttk widgets."""
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")

    style.configure("App.TFrame", background=BACKGROUND)
    style.configure("Header.TFrame", background=SIDEBAR)
    style.configure("Sidebar.TFrame", background=SIDEBAR)
    style.configure("Workspace.TFrame", background=BACKGROUND)
    style.configure("Panel.TFrame", background=PANELS)
    style.configure("Raised.TFrame", background=PANEL_RAISED)
    style.configure(
        "Title.TLabel",
        background=SIDEBAR,
        foreground=PRIMARY_TEXT,
        font=(FONT, 17, "bold"),
    )
    style.configure(
        "Subtitle.TLabel",
        background=SIDEBAR,
        foreground=SECONDARY_TEXT,
        font=(FONT, 8),
    )
    style.configure(
        "PageTitle.TLabel",
        background=BACKGROUND,
        foreground=PRIMARY_TEXT,
        font=(FONT, 19, "bold"),
    )
    style.configure(
        "PageSubtitle.TLabel",
        background=BACKGROUND,
        foreground=SECONDARY_TEXT,
        font=(FONT, 9),
    )
    style.configure(
        "PanelTitle.TLabel",
        background=PANELS,
        foreground=PRIMARY_TEXT,
        font=(FONT, 10, "bold"),
    )
    style.configure(
        "PanelMuted.TLabel",
        background=PANELS,
        foreground=SECONDARY_TEXT,
        font=(FONT, 9),
    )
    style.configure(
        "PanelValue.TLabel",
        background=PANELS,
        foreground=PRIMARY_TEXT,
        font=(FONT, 12, "bold"),
    )
    style.configure(
        "Nav.TButton",
        background=SIDEBAR,
        foreground=SECONDARY_TEXT,
        anchor="w",
        font=(FONT, 9, "bold"),
        padding=(12, 11),
        borderwidth=0,
    )
    style.map(
        "Nav.TButton",
        background=[("active", PANEL_RAISED), ("selected", "#164252")],
        foreground=[("active", PRIMARY_TEXT), ("selected", ACCENT)],
    )
    style.configure(
        "Accent.TButton",
        background="#164252",
        foreground=ACCENT,
        font=(FONT, 9, "bold"),
        padding=(10, 8),
        borderwidth=0,
    )
    style.map(
        "Accent.TButton",
        background=[("active", "#20566A"), ("disabled", "#1A2C3B")],
        foreground=[("disabled", "#688195")],
    )
    style.configure(
        "Secondary.TButton",
        background=PANEL_RAISED,
        foreground=PRIMARY_TEXT,
        font=(FONT, 9, "bold"),
        padding=(9, 7),
        borderwidth=0,
    )
    style.map(
        "Secondary.TButton",
        background=[("active", "#1C3B52"), ("disabled", "#1A2C3B")],
        foreground=[("disabled", "#688195")],
    )
    style.configure(
        "CompactAccent.TButton",
        background="#164252",
        foreground=ACCENT,
        font=(FONT, 8, "bold"),
        padding=(7, 4),
        borderwidth=0,
    )
    style.map(
        "CompactAccent.TButton",
        background=[("active", "#20566A"), ("disabled", "#1A2C3B")],
        foreground=[("disabled", "#688195")],
    )
    style.configure(
        "Compact.TButton",
        background=PANEL_RAISED,
        foreground=PRIMARY_TEXT,
        font=(FONT, 8, "bold"),
        padding=(7, 4),
        borderwidth=0,
    )
    style.map(
        "Compact.TButton",
        background=[("active", "#1C3B52"), ("disabled", "#1A2C3B")],
        foreground=[("disabled", "#688195")],
    )
    style.configure(
        "CompactEmergency.TButton",
        background="#3A252E",
        foreground=EMERGENCY,
        font=(FONT, 8, "bold"),
        padding=(7, 4),
        borderwidth=0,
    )
    style.map(
        "CompactEmergency.TButton",
        background=[("active", "#58303A"), ("disabled", "#1A2C3B")],
        foreground=[("disabled", "#688195")],
    )
    style.configure(
        "Emergency.TButton",
        background="#3A252E",
        foreground=EMERGENCY,
        font=(FONT, 9, "bold"),
        padding=(9, 7),
        borderwidth=0,
    )
    style.map(
        "Emergency.TButton",
        background=[("active", "#58303A"), ("disabled", "#1A2C3B")],
        foreground=[("disabled", "#688195")],
    )
    style.configure(
        "Treeview",
        background=PANELS,
        fieldbackground=PANELS,
        foreground=PRIMARY_TEXT,
        rowheight=30,
        borderwidth=0,
        font=(FONT, 9),
    )
    style.configure(
        "Treeview.Heading",
        background=PANEL_RAISED,
        foreground=SECONDARY_TEXT,
        font=(FONT, 8, "bold"),
        relief="flat",
        padding=(8, 7),
    )
    style.map(
        "Treeview",
        background=[("selected", "#17465A")],
        foreground=[("selected", PRIMARY_TEXT)],
    )
    style.configure(
        "TScrollbar",
        background=PANEL_RAISED,
        troughcolor=BACKGROUND,
        bordercolor=BACKGROUND,
        arrowcolor=SECONDARY_TEXT,
        relief="flat",
    )
    style.map(
        "TScrollbar",
        background=[("active", "#28516A")],
        arrowcolor=[("active", PRIMARY_TEXT)],
    )
    style.configure(
        "Aero.Horizontal.TProgressbar",
        troughcolor=PANEL_RAISED,
        background=ACCENT,
        bordercolor=PANEL_RAISED,
        lightcolor=ACCENT,
        darkcolor=ACCENT,
    )
    style.configure(
        "Dark.TCombobox",
        fieldbackground=PANEL_RAISED,
        background=PANEL_RAISED,
        foreground=PRIMARY_TEXT,
        arrowcolor=ACCENT,
        bordercolor=BORDER,
        lightcolor=PANEL_RAISED,
        darkcolor=PANEL_RAISED,
        padding=(6, 5),
    )
    style.map(
        "Dark.TCombobox",
        fieldbackground=[("readonly", PANEL_RAISED), ("focus", PANEL_RAISED)],
        foreground=[("readonly", PRIMARY_TEXT)],
        selectbackground=[("readonly", PANEL_RAISED)],
        selectforeground=[("readonly", PRIMARY_TEXT)],
    )
    style.configure(
        "TEntry",
        fieldbackground=PANEL_RAISED,
        foreground=PRIMARY_TEXT,
        insertcolor=PRIMARY_TEXT,
        bordercolor=BORDER,
        padding=(8, 7),
    )

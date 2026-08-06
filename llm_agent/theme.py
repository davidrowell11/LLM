"""Shared colours and fonts, so the app and its dialogs look like one thing.

Tk has no styling system worth the name, so every widget is coloured
explicitly. Keeping the palette in one place is what stops the dialogs from
drifting back to the default grey.
"""

import tkinter as tk
from tkinter import font as tkfont

# Near-neutral charcoals rather than saturated navy. Tinting every surface
# blue leaves the whole window one muddy value with nothing to separate the
# panels; keeping the base neutral lets a single accent do the work.
SIDEBAR = "#0E0E10"
BG = "#151517"
SURFACE = "#1D1D21"  # assistant bubbles, dialogs
SURFACE_HI = "#232329"  # inputs
RAISED = "#2C2C33"  # hover
LINE = "#26262B"
BORDER = "#31313A"  # 1px edge; definition without extra contrast

SIDEBAR_HOVER = "#1A1A1E"
SIDEBAR_ACTIVE = "#26262C"
# The brand cyan, deepened enough to carry white text. Your side of the
# conversation is the only saturated thing on screen, so it reads instantly.
USER_BUBBLE = "#0E7490"
USER_BUBBLE_EDGE = "#1194B4"

# Accents.
ACCENT = "#22D3EE"
ACCENT_DIM = "#0E7490"
VIOLET = "#C4B5FD"
DANGER = "#F87171"

# Type.
TEXT = "#FAFAFA"
MUTED = "#A1A1AA"
DIM = "#71717A"
ON_ACCENT = "#06282F"

# Roboto is what the rest of ChromeOS uses; the installer adds it, since a
# bare Crostini container only has DejaVu and that alone makes the app look
# a decade old.
UI_FONTS = ["Roboto", "Inter", "Arimo", "Noto Sans", "Ubuntu", "DejaVu Sans"]
MONO_FONTS = ["Roboto Mono", "JetBrains Mono", "Cousine", "DejaVu Sans Mono"]


def pick(root, size, weight="normal", mono=False):
    """Use the nicest font actually installed rather than assuming one."""
    candidates = MONO_FONTS if mono else UI_FONTS
    available = set(tkfont.families(root))
    for name in candidates:
        if name in available:
            return tkfont.Font(root=root, family=name, size=size, weight=weight)
    return tkfont.Font(root=root, size=size, weight=weight)


def rounded_points(x1, y1, x2, y2, r):
    """Corner-doubling point list; with smooth=True this draws a rounded box."""
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


class PillButton(tk.Canvas):
    """A rounded button. Tk's own Button is always a hard rectangle."""

    def __init__(self, parent, text, command, *, font, bg, fg, hover,
                 radius=None, padx=18, pady=9, surface=None):
        self._font = font
        self._bg = bg
        self._fg = fg
        self._hover = hover
        self._command = command
        self._enabled = True

        surface = surface or BG
        width = font.measure(text) + padx * 2
        height = font.metrics("linespace") + pady * 2
        self._radius = radius if radius is not None else height // 2

        super().__init__(parent, width=width, height=height, bg=surface,
                         highlightthickness=0, bd=0, takefocus=0,
                         cursor="hand2")
        self._shape = self.create_polygon(
            rounded_points(0, 0, width, height, self._radius),
            smooth=True, splinesteps=24, fill=bg,
        )
        self._label = self.create_text(
            width / 2, height / 2, text=text, fill=fg, font=font
        )

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

    def _on_enter(self, _event):
        if self._enabled:
            self.itemconfigure(self._shape, fill=self._hover)

    def _on_leave(self, _event):
        if self._enabled:
            self.itemconfigure(self._shape, fill=self._bg)

    def _on_click(self, _event):
        if self._enabled and self._command:
            self._command()

    def set_enabled(self, enabled, disabled_bg=None, disabled_fg=None):
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "")
        self.itemconfigure(
            self._shape, fill=self._bg if enabled else (disabled_bg or SURFACE_HI)
        )
        self.itemconfigure(
            self._label, fill=self._fg if enabled else (disabled_fg or DIM)
        )


class RoundedPanel(tk.Canvas):
    """A rounded container. The inner frame is inset so the canvas corners,
    not the frame's square ones, are what you see."""

    def __init__(self, parent, fill, radius=18, surface=None, inset=6):
        super().__init__(parent, bg=surface or BG, highlightthickness=0, bd=0)
        self._fill = fill
        self._radius = radius
        self._inset = inset
        self._shape = None

        self.body = tk.Frame(self, bg=fill)
        self._window = self.create_window(
            inset, 0, window=self.body, anchor="nw"
        )
        self.body.bind("<Configure>", self._on_body)
        self.bind("<Configure>", self._on_resize)

    def _on_body(self, event):
        self.configure(height=event.height)

    def _on_resize(self, event):
        self.itemconfigure(
            self._window, width=max(1, event.width - self._inset * 2)
        )
        if self._shape is not None:
            self.delete(self._shape)
        self._shape = self.create_polygon(
            rounded_points(0, 0, event.width, event.height, self._radius),
            smooth=True, splinesteps=24, fill=self._fill,
        )
        self.tag_lower(self._shape)


def flat_button(parent, text, command, *, font, bg=SURFACE, fg=TEXT,
                hover=RAISED, padx=14, pady=8, **kwargs):
    """A borderless button that actually reacts to the pointer.

    Tk's activebackground only applies while the mouse is *down*, so without
    explicit Enter/Leave bindings a flat button feels dead.
    """
    button = tk.Button(
        parent, text=text, command=command, font=font,
        bg=bg, fg=fg, activebackground=hover, activeforeground=fg,
        relief="flat", bd=0, highlightthickness=0,
        padx=padx, pady=pady, cursor="hand2", **kwargs,
    )

    # A disabled button must not react, and must not be repainted on the way
    # out either: restoring the resting colour would make a button that is
    # still busy look enabled and clickable again.
    def enter(_event):
        if str(button.cget("state")) != "disabled":
            button.configure(bg=hover)

    def leave(_event):
        if str(button.cget("state")) != "disabled":
            button.configure(bg=bg)

    button.bind("<Enter>", enter)
    button.bind("<Leave>", leave)
    return button

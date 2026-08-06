"""Shared colours and fonts, so the app and its dialogs look like one thing.

Tk has no styling system worth the name, so every widget is coloured
explicitly. Keeping the palette in one place is what stops the dialogs from
drifting back to the default grey.
"""

import tkinter as tk
from tkinter import font as tkfont

# Surfaces, darkest to lightest. The sidebar sits below the content area so
# the conversation reads as the lit surface, with everything else receding.
SIDEBAR = "#070B1B"
BG = "#0B1024"
SURFACE = "#182142"  # assistant bubbles, dialogs
SURFACE_HI = "#1F2950"  # inputs
RAISED = "#2A3766"  # hover
LINE = "#1A2247"

SIDEBAR_HOVER = "#111834"
SIDEBAR_ACTIVE = "#1C2751"
# Deliberately more saturated than any assistant surface, so at a glance you
# can tell your side of the conversation from hers without reading names.
USER_BUBBLE = "#2C46A8"

# Accents.
ACCENT = "#35E0E6"
ACCENT_DIM = "#1B9BA0"
VIOLET = "#B39BFF"
DANGER = "#FF8A8A"

# Type.
TEXT = "#EAEFFF"
MUTED = "#97A6CE"
DIM = "#62719F"
ON_ACCENT = "#04122A"

UI_FONTS = ["Inter", "Ubuntu", "Cantarell", "DejaVu Sans"]
MONO_FONTS = ["JetBrains Mono", "Ubuntu Mono", "DejaVu Sans Mono"]


def pick(root, size, weight="normal", mono=False):
    """Use the nicest font actually installed rather than assuming one."""
    candidates = MONO_FONTS if mono else UI_FONTS
    available = set(tkfont.families(root))
    for name in candidates:
        if name in available:
            return tkfont.Font(root=root, family=name, size=size, weight=weight)
    return tkfont.Font(root=root, size=size, weight=weight)


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

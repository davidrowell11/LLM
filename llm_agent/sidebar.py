"""One row in the conversation list.

A canvas rather than a Frame of Labels, so the selected and hovered states
can be rounded. A square block of colour behind the selected chat was one of
the things that made the sidebar look unfinished next to the rest of the app.
"""

import tkinter as tk

from . import theme

RADIUS = 10
PAD_X = 12
HEIGHT = 52
CLOSE_W = 26


class ChatRow(tk.Canvas):
    def __init__(self, parent, chat, *, selected, title, subtitle, fonts,
                 on_open, on_delete, on_menu):
        super().__init__(parent, height=HEIGHT, bg=theme.SIDEBAR,
                         highlightthickness=0, bd=0, takefocus=0,
                         cursor="hand2")
        self.chat = chat
        self._selected = selected
        self._on_open = on_open
        self._on_delete = on_delete
        self._on_menu = on_menu
        self._hovering = False

        self._shape = self.create_polygon(
            (0, 0, 0, 0), smooth=True, splinesteps=20,
            fill=theme.SIDEBAR_ACTIVE if selected else theme.SIDEBAR, outline="",
        )
        self._title = self.create_text(
            PAD_X, 14, text=title, anchor="nw", font=fonts["row"],
            fill=theme.TEXT if selected else theme.MUTED,
        )
        self._subtitle = self.create_text(
            PAD_X, 32, text=subtitle, anchor="nw", font=fonts["sub"],
            fill=theme.DIM,
        )
        # Drawn only on hover or selection: a row of permanent ✕ marks turns
        # the sidebar into visual noise.
        self._close = self.create_text(
            0, HEIGHT / 2, text="✕", anchor="center", font=fonts["sub"],
            fill=theme.DIM, state="hidden",
        )

        self.bind("<Configure>", self._redraw)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<Motion>", self._motion)
        self.bind("<Button-1>", self._click)
        self.bind("<Button-3>", lambda e: self._on_menu(e, self.chat))

    # --- painting --------------------------------------------------------

    def _redraw(self, event=None):
        width = event.width if event else self.winfo_width()
        if width <= 1:
            return
        self.coords(self._shape, *self._rounded(0, 0, width, HEIGHT, RADIUS))
        self.coords(self._close, width - CLOSE_W / 2 - 4, HEIGHT / 2)
        self._apply_colours()

    def _rounded(self, x1, y1, x2, y2, r):
        return [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]

    def _apply_colours(self):
        if self._selected:
            fill = theme.SIDEBAR_ACTIVE
        elif self._hovering:
            fill = theme.SIDEBAR_HOVER
        else:
            fill = theme.SIDEBAR
        self.itemconfigure(self._shape, fill=fill)
        self.itemconfigure(
            self._title, fill=theme.TEXT if self._selected else theme.MUTED
        )
        self.itemconfigure(
            self._close,
            state="normal" if (self._hovering or self._selected) else "hidden",
        )

    # --- interaction -----------------------------------------------------

    def _enter(self, _event):
        self._hovering = True
        self._apply_colours()

    def _leave(self, _event):
        self._hovering = False
        self.itemconfigure(self._close, fill=theme.DIM)
        self._apply_colours()

    def _over_close(self, x):
        return x >= self.winfo_width() - CLOSE_W - 8

    def _motion(self, event):
        over = self._over_close(event.x)
        self.itemconfigure(self._close, fill=theme.DANGER if over else theme.DIM)

    def _click(self, event):
        if self._over_close(event.x) and (self._hovering or self._selected):
            self._on_delete(self.chat)
        else:
            self._on_open(self.chat.id)

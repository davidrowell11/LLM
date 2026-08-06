"""The message list.

Bubbles are Canvas widgets rather than Labels or Text tags. A Text tag paints
its background across the whole line, and a Label is always a hard rectangle;
only a canvas can give a bubble that both hugs its text and has rounded
corners, which is most of the difference between this looking like a chat app
and looking like a form.

Selection isn't available on a canvas, so each bubble carries right-click Copy.
"""

import tkinter as tk

from . import theme

BUBBLE_FRACTION = 0.60  # of the viewport width
MIN_WRAP = 200
RADIUS = 16
PAD_X = 16
PAD_Y = 12


class Bubble(tk.Canvas):
    """One message: rounded background sized to its own wrapped text."""

    def __init__(self, parent, text, fill, fg, font, wrap, edge=None):
        super().__init__(
            parent, bg=theme.BG, highlightthickness=0, bd=0, takefocus=0
        )
        self.text = text
        self._fill = fill
        self._edge = edge or theme.BORDER
        self._font = font

        self._shape = None
        self._label = self.create_text(
            PAD_X, PAD_Y, text=text, anchor="nw", width=wrap, fill=fg, font=font,
        )
        self.relayout(wrap)

    def relayout(self, wrap):
        """Resize to fit the text at the given wrap width, then redraw."""
        self.itemconfigure(self._label, width=wrap)
        bbox = self.bbox(self._label)
        if not bbox:
            return
        width = bbox[2] - bbox[0] + PAD_X * 2
        height = bbox[3] - bbox[1] + PAD_Y * 2
        self.configure(width=width, height=height)

        if self._shape is not None:
            self.delete(self._shape)
        # Inset by a pixel so the outline isn't clipped by the canvas edge.
        self._shape = self._rounded(1, 1, width - 1, height - 1, RADIUS,
                                    self._fill, self._edge)
        self.tag_lower(self._shape)

    def _rounded(self, x1, y1, x2, y2, r, fill, edge):
        # smooth=True over a point list that doubles back at each corner is
        # the standard way to get rounded corners out of a canvas polygon.
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self.create_polygon(points, smooth=True, splinesteps=24,
                                   fill=fill, outline=edge, width=1)


class Transcript(tk.Frame):
    def __init__(self, parent, fonts):
        super().__init__(parent, bg=theme.BG)
        self.fonts = fonts
        self._bubbles = []
        self._wrappable = []  # plain labels (notices, errors)

        self.canvas = tk.Canvas(self, bg=theme.BG, highlightthickness=0, bd=0)
        self.canvas.pack(side="left", fill="both", expand=True)

        self.scroll = tk.Scrollbar(
            self, command=self.canvas.yview, bg=theme.BG, troughcolor=theme.BG,
            activebackground=theme.DIM, relief="flat", bd=0, width=10,
        )
        self.scroll.pack(side="right", fill="y")
        self.canvas.configure(yscrollcommand=self.scroll.set)

        self.inner = tk.Frame(self.canvas, bg=theme.BG)
        self._window = self.canvas.create_window(
            (0, 0), window=self.inner, anchor="nw"
        )

        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

    # --- geometry --------------------------------------------------------

    def _on_inner_configure(self, _event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self._window, width=event.width)
        wrap = max(MIN_WRAP, int(event.width * BUBBLE_FRACTION))
        for bubble in self._bubbles:
            try:
                bubble.relayout(wrap)
            except tk.TclError:
                pass  # destroyed by a conversation switch mid-resize
        for label in self._wrappable:
            try:
                label.configure(wraplength=wrap)
            except tk.TclError:
                pass

    def _wrap(self):
        width = self.canvas.winfo_width() or 700
        return max(MIN_WRAP, int(width * BUBBLE_FRACTION))

    def scroll_to_end(self):
        self.update_idletasks()
        self.canvas.yview_moveto(1.0)

    def plain_text(self):
        """Everything currently displayed, as text. Used by tests."""
        parts = [b.text for b in self._bubbles]

        def walk(widget):
            for child in widget.winfo_children():
                try:
                    text = child.cget("text")
                except tk.TclError:
                    text = None
                if text:
                    parts.append(str(text))
                walk(child)

        walk(self.inner)
        return "\n".join(parts)

    def clear(self):
        for child in self.inner.winfo_children():
            child.destroy()
        self._bubbles.clear()
        self._wrappable.clear()
        self.canvas.yview_moveto(0.0)

    # --- content ---------------------------------------------------------

    def _row(self, side):
        row = tk.Frame(self.inner, bg=theme.BG)
        row.pack(fill="x", padx=24, pady=(0, 2))
        return row

    def add_message(self, role, text, name=None, researched=""):
        user = role == "user"
        side = "right" if user else "left"

        if researched:
            self.add_research(researched)

        header = self._row(side)
        tk.Label(
            header, text=name or ("You" if user else "Cortana"), bg=theme.BG,
            fg=theme.DIM, font=self.fonts["sub"],
        ).pack(side=side, padx=8, pady=(16, 5))

        row = self._row(side)
        bubble = Bubble(
            row, str(text).strip(),
            fill=theme.USER_BUBBLE if user else theme.SURFACE,
            fg=theme.TEXT, font=self.fonts["body"], wrap=self._wrap(),
            edge=theme.USER_BUBBLE_EDGE if user else theme.BORDER,
        )
        bubble.pack(side=side)
        self._bubbles.append(bubble)
        self._attach_copy(bubble, str(text).strip())
        self.scroll_to_end()

    def add_research(self, query, notes=None):
        row = self._row("left")
        text = f"searched the web for “{query}”"
        if notes:
            text += f" · saved {notes} note(s)"
        chip = Bubble(
            row, text, fill=theme.SURFACE, fg=theme.MUTED,
            font=self.fonts["sub"], wrap=self._wrap(), edge=theme.BORDER,
        )
        chip.pack(side="left", pady=(14, 0), padx=6)
        self._bubbles.append(chip)
        self.scroll_to_end()

    def add_notice(self, text):
        self._simple(text, theme.MUTED)

    def add_error(self, text):
        self._simple(text, theme.DANGER)

    def _simple(self, text, colour):
        row = self._row("left")
        label = tk.Label(
            row, text=str(text).strip(), bg=theme.BG, fg=colour,
            font=self.fonts["sub"], justify="left", anchor="w",
            wraplength=self._wrap(),
        )
        label.pack(side="left", pady=(14, 2), padx=6)
        self._wrappable.append(label)
        self.scroll_to_end()

    # --- copy ------------------------------------------------------------

    def _attach_copy(self, widget, text):
        """A canvas has no selection, so offer an explicit copy instead."""
        def popup(event):
            menu = tk.Menu(
                self, tearoff=0, bg=theme.SURFACE, fg=theme.TEXT,
                activebackground=theme.RAISED, activeforeground=theme.TEXT,
                bd=0, font=self.fonts["row"],
            )
            menu.add_command(label="Copy", command=lambda: self._copy(text))
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()

        widget.bind("<Button-3>", popup)

    def _copy(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)

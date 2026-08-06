"""The message list.

Built from individual widgets in a scrollable frame rather than a single Text
widget. A Text tag paints its background across the entire line, so messages
came out as full-width bands; real bubbles have to hug their own text, which
means one widget per message.

The tradeoff is that Labels aren't selectable, so each bubble carries a
right-click "Copy" instead.
"""

import tkinter as tk

from . import theme

BUBBLE_FRACTION = 0.62  # of the viewport width
MIN_WRAP = 220


class Transcript(tk.Frame):
    def __init__(self, parent, fonts):
        super().__init__(parent, bg=theme.BG)
        self.fonts = fonts
        self._wrappable = []  # labels whose wraplength tracks the window

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
        for label in self._wrappable:
            try:
                label.configure(wraplength=wrap)
            except tk.TclError:
                pass  # destroyed by a conversation switch mid-resize

    def _wrap(self):
        width = self.canvas.winfo_width() or 700
        return max(MIN_WRAP, int(width * BUBBLE_FRACTION))

    def scroll_to_end(self):
        self.update_idletasks()
        self.canvas.yview_moveto(1.0)

    def plain_text(self):
        """Everything currently displayed, as text. Used by tests."""
        parts = []

        def walk(widget):
            for child in widget.winfo_children():
                text = None
                try:
                    text = child.cget("text")
                except tk.TclError:
                    pass
                if text:
                    parts.append(str(text))
                walk(child)

        walk(self.inner)
        return "\n".join(parts)

    def clear(self):
        for child in self.inner.winfo_children():
            child.destroy()
        self._wrappable.clear()
        self.canvas.yview_moveto(0.0)

    # --- content ---------------------------------------------------------

    def _row(self, side):
        row = tk.Frame(self.inner, bg=theme.BG)
        row.pack(fill="x", padx=26, pady=(0, 2), anchor="e" if side == "right" else "w")
        return row

    def add_message(self, role, text, name=None, researched=""):
        user = role == "user"
        side = "right" if user else "left"

        if researched:
            self.add_research(researched)

        header = self._row(side)
        tk.Label(
            header, text=name or ("You" if user else "Cortana"), bg=theme.BG,
            fg=theme.ACCENT if user else theme.VIOLET, font=self.fonts["name"],
        ).pack(side=side, padx=2, pady=(14, 4))

        row = self._row(side)
        bubble = tk.Frame(row, bg=theme.USER_BUBBLE if user else theme.SURFACE)
        bubble.pack(side=side)

        label = tk.Label(
            bubble, text=str(text).strip(), bg=bubble["bg"], fg=theme.TEXT,
            font=self.fonts["body"], justify="left", anchor="w",
            wraplength=self._wrap(), padx=16, pady=12,
        )
        label.pack()
        self._wrappable.append(label)
        self._attach_copy(label, str(text).strip())
        self.scroll_to_end()

    def add_research(self, query, notes=None):
        row = self._row("left")
        text = f"↗ researched “{query}”"
        if notes is not None:
            text += f" · {notes} note(s) saved"
        tk.Label(
            row, text=text, bg=theme.BG, fg=theme.ACCENT, font=self.fonts["mono"],
            anchor="w",
        ).pack(side="left", pady=(12, 0), padx=2)
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
        label.pack(side="left", pady=(12, 2), padx=2)
        self._wrappable.append(label)
        self.scroll_to_end()

    # --- copy ------------------------------------------------------------

    def _attach_copy(self, widget, text):
        """Labels can't be selected, so offer an explicit copy instead."""
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

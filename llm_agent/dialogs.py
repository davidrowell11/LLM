"""Modal dialogs styled to match the app.

tkinter.simpledialog and tkinter.messagebox draw with the platform default
look -- grey, square, and jarring next to a dark app. These are plain
Toplevels with the same palette as everything else.

Each one centres on its parent, grabs focus, and closes on Enter/Escape.
"""

import tkinter as tk

from . import theme


class _Modal(tk.Toplevel):
    def __init__(self, parent, title, width=420):
        super().__init__(parent)
        self.result = None
        self.withdraw()  # place it before showing, to avoid a visible jump
        self.title(title)
        self.configure(bg=theme.SURFACE)
        self.resizable(False, False)
        self.transient(parent)

        self._parent = parent
        self._width = width

        self.body = tk.Frame(self, bg=theme.SURFACE, padx=24, pady=20)
        self.body.pack(fill="both", expand=True)

        self.bind("<Escape>", lambda _e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)

    def _centre(self):
        self.update_idletasks()
        width = max(self._width, self.winfo_reqwidth())
        height = self.winfo_reqheight()
        try:
            x = self._parent.winfo_rootx() + (self._parent.winfo_width() - width) // 2
            y = self._parent.winfo_rooty() + (self._parent.winfo_height() - height) // 3
        except tk.TclError:
            x = y = 200
        self.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 0)}")

    def _show(self):
        self._centre()
        self.deiconify()
        # A window manager maps the window asynchronously, so grabbing right
        # after deiconify can fail with "window not viewable". Waiting for
        # visibility first avoids that; if the grab still fails the dialog
        # simply isn't modal, which is far better than crashing.
        try:
            self.wait_visibility()
            self.grab_set()
        except tk.TclError:
            pass
        self.wait_window()
        return self.result

    def _cancel(self):
        self.result = None
        self.destroy()

    def _buttons(self, ok_label, on_ok, danger=False):
        row = tk.Frame(self.body, bg=theme.SURFACE)
        row.pack(fill="x", pady=(18, 0))

        theme.PillButton(
            row, "Cancel", self._cancel, font=theme.pick(self, 10),
            bg=theme.SURFACE_HI, fg=theme.MUTED, hover=theme.RAISED,
            padx=18, surface=theme.SURFACE,
        ).pack(side="right")

        ok = theme.PillButton(
            row, ok_label, on_ok, font=theme.pick(self, 10, "bold"),
            bg=theme.DANGER if danger else theme.ACCENT,
            fg=theme.ON_ACCENT,
            hover="#FCA5A5" if danger else theme.ACCENT_DIM,
            padx=22, surface=theme.SURFACE,
        )
        ok.pack(side="right", padx=(0, 8))
        return ok


class _TextPrompt(_Modal):
    def __init__(self, parent, title, prompt, initial="", ok_label="OK",
                 placeholder=""):
        super().__init__(parent, title)

        tk.Label(
            self.body, text=prompt, bg=theme.SURFACE, fg=theme.TEXT,
            font=theme.pick(self, 12), anchor="w", justify="left",
            wraplength=self._width - 60,
        ).pack(fill="x")

        if placeholder:
            tk.Label(
                self.body, text=placeholder, bg=theme.SURFACE, fg=theme.DIM,
                font=theme.pick(self, 9), anchor="w", justify="left",
                wraplength=self._width - 60,
            ).pack(fill="x", pady=(4, 0))

        shell = tk.Frame(self.body, bg=theme.SURFACE_HI,
                         highlightthickness=1, highlightbackground=theme.RAISED,
                         highlightcolor=theme.ACCENT)
        shell.pack(fill="x", pady=(14, 0))

        self.var = tk.StringVar(value=initial)
        entry = tk.Entry(
            shell, textvariable=self.var, bg=theme.SURFACE_HI, fg=theme.TEXT,
            insertbackground=theme.ACCENT, relief="flat", bd=0,
            highlightthickness=0, font=theme.pick(self, 12),
        )
        entry.pack(fill="x", padx=12, pady=11)
        entry.bind("<Return>", lambda _e: self._accept())
        entry.select_range(0, "end")

        self._buttons(ok_label, self._accept)
        # Also bound on the dialog, so Return still confirms if focus has
        # moved off the entry -- the other dialogs already behave that way.
        self.bind("<Return>", lambda _e: self._accept())
        entry.focus_set()

    def _accept(self):
        value = self.var.get().strip()
        self.result = value or None
        self.destroy()


class _Confirm(_Modal):
    def __init__(self, parent, title, message, ok_label="Delete", danger=True):
        super().__init__(parent, title)

        tk.Label(
            self.body, text=message, bg=theme.SURFACE, fg=theme.TEXT,
            font=theme.pick(self, 12), anchor="w", justify="left",
            wraplength=self._width - 60,
        ).pack(fill="x")

        self._buttons(ok_label, self._accept, danger=danger)
        self.bind("<Return>", lambda _e: self._accept())
        self.focus_set()

    def _accept(self):
        self.result = True
        self.destroy()


class _Info(_Modal):
    def __init__(self, parent, title, rows, footer=""):
        super().__init__(parent, title)

        for label, value in rows:
            line = tk.Frame(self.body, bg=theme.SURFACE)
            line.pack(fill="x", pady=3)
            tk.Label(
                line, text=label, bg=theme.SURFACE, fg=theme.MUTED,
                font=theme.pick(self, 10), anchor="w", width=16,
            ).pack(side="left")
            tk.Label(
                line, text=value, bg=theme.SURFACE, fg=theme.TEXT,
                font=theme.pick(self, 11, "bold"), anchor="w",
            ).pack(side="left")

        if footer:
            tk.Label(
                self.body, text=footer, bg=theme.SURFACE, fg=theme.DIM,
                font=theme.pick(self, 9), anchor="w", justify="left",
                wraplength=self._width - 60,
            ).pack(fill="x", pady=(14, 0))

        row = tk.Frame(self.body, bg=theme.SURFACE)
        row.pack(fill="x", pady=(18, 0))
        done = theme.PillButton(
            row, "Done", self._cancel, font=theme.pick(self, 10, "bold"),
            bg=theme.ACCENT, fg=theme.ON_ACCENT, hover=theme.ACCENT_DIM,
            padx=22, surface=theme.SURFACE,
        )
        done.pack(side="right")
        self.bind("<Return>", lambda _e: self._cancel())


def ask_text(parent, title, prompt, initial="", ok_label="OK", placeholder=""):
    """Returns the entered text, or None if cancelled."""
    return _TextPrompt(parent, title, prompt, initial, ok_label, placeholder)._show()


def confirm(parent, title, message, ok_label="Delete", danger=True):
    """Returns True only if confirmed."""
    return bool(_Confirm(parent, title, message, ok_label, danger)._show())


def show_info(parent, title, rows, footer=""):
    _Info(parent, title, rows, footer)._show()

"""Cortana's desktop app -- a real window, not a terminal.

All model work happens on a single background worker thread that owns the
Memory object. That matters for two reasons: the UI never freezes while a
local model is generating (which on a Chromebook can take many seconds), and
SQLite connections stay on one thread, which sqlite3 requires by default.

The UI thread never touches the agent, and the worker never touches Tk. They
communicate through two queues.
"""

import queue
import sys
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox, simpledialog
from typing import Optional

from . import config
from .agent import Agent
from .llm_client import OllamaError
from .memory import Memory
from .topics import TopicQueue

# Palette, matching the launcher icon.
BG = "#0D1230"
SURFACE = "#161F44"
SURFACE_HI = "#1E2A5A"
USER_BUBBLE = "#243A7A"
ACCENT = "#2DE2E6"
VIOLET = "#A98BFA"
TEXT = "#E6ECFF"
MUTED = "#8FA0C8"
DIM = "#5D6E9E"

ICON_PATH = Path(__file__).resolve().parent.parent / "assets" / "cortana.png"


@dataclass
class Request:
    kind: str  # "chat" | "learn" | "stats"
    payload: str = ""


@dataclass
class Response:
    kind: str
    text: str = ""
    researched: str = ""
    notes_added: int = 0
    note_count: int = 0
    pending: int = 0
    error: str = ""


def _pick_font(root, candidates, size, weight="normal"):
    """Use the nicest font actually installed rather than assuming one."""
    available = set(tkfont.families(root))
    for name in candidates:
        if name in available:
            return tkfont.Font(root=root, family=name, size=size, weight=weight)
    return tkfont.Font(root=root, size=size, weight=weight)


class Worker(threading.Thread):
    """Owns the Agent and Memory; serialises all model work onto one thread."""

    def __init__(self, requests: "queue.Queue[Optional[Request]]",
                 responses: "queue.Queue[Response]"):
        super().__init__(daemon=True)
        self.requests = requests
        self.responses = responses

    def run(self):
        try:
            memory = Memory()
            agent = Agent(memory=memory)
            topics = TopicQueue()
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            self.responses.put(Response(kind="fatal", error=str(exc)))
            return

        self._emit_stats(memory, topics)

        while True:
            request = self.requests.get()
            if request is None:
                break
            try:
                if request.kind == "chat":
                    result = agent.chat(request.payload)
                    self.responses.put(
                        Response(
                            kind="chat",
                            text=result.answer,
                            researched=result.researched_query,
                            notes_added=result.notes_added,
                        )
                    )
                elif request.kind == "learn":
                    learned = agent.learn(request.payload)
                    if not learned.reachable:
                        self.responses.put(
                            Response(
                                kind="notice",
                                text=f"Couldn't reach the web to research "
                                     f"“{request.payload}”.",
                            )
                        )
                    else:
                        self.responses.put(
                            Response(
                                kind="notice",
                                text=f"Learned {len(learned.notes_added)} note(s) "
                                     f"about “{request.payload}”.",
                            )
                        )
                elif request.kind == "curious":
                    added = topics.add(request.payload)
                    self.responses.put(
                        Response(
                            kind="notice",
                            text=(
                                f"Queued “{request.payload}” for background "
                                "research."
                            )
                            if added
                            else f"“{request.payload}” is already known or queued.",
                        )
                    )
            except OllamaError as exc:
                self.responses.put(Response(kind="error", error=str(exc)))
            except Exception as exc:  # noqa: BLE001
                self.responses.put(Response(kind="error", error=str(exc)))
            self._emit_stats(memory, topics)

        memory.close()
        topics.close()

    def _emit_stats(self, memory, topics):
        try:
            self.responses.put(
                Response(
                    kind="stats",
                    note_count=memory.count(),
                    pending=topics.pending_count(),
                )
            )
        except Exception:  # noqa: BLE001 - stats are cosmetic
            pass


class CortanaApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.requests: "queue.Queue[Optional[Request]]" = queue.Queue()
        self.responses: "queue.Queue[Response]" = queue.Queue()
        self.busy = False
        self._spinner_step = 0
        self._icon_image = None

        root.title(f"{config.ASSISTANT_NAME}")
        root.configure(bg=BG)
        root.geometry("900x680")
        root.minsize(560, 460)

        self.f_title = _pick_font(root, ["Inter", "Ubuntu", "DejaVu Sans"], 17, "bold")
        self.f_sub = _pick_font(root, ["Inter", "Ubuntu", "DejaVu Sans"], 10)
        self.f_body = _pick_font(root, ["Inter", "Ubuntu", "DejaVu Sans"], 12)
        self.f_name = _pick_font(root, ["Inter", "Ubuntu", "DejaVu Sans"], 10, "bold")
        self.f_mono = _pick_font(root, ["JetBrains Mono", "DejaVu Sans Mono"], 10)

        self._load_icon()
        # Order matters: the transcript expands to fill whatever is left, so
        # everything with a fixed size must be packed before it or it gets
        # squeezed out of the window entirely.
        self._build_header()
        self._build_statusbar()
        self._build_composer()
        self._build_transcript()

        Worker(self.requests, self.responses).start()
        self.root.after(60, self._drain_responses)

        self._welcome()
        self.entry.focus_set()

    # --- chrome ---------------------------------------------------------

    def _load_icon(self):
        if not ICON_PATH.exists():
            return
        try:
            full = tk.PhotoImage(file=str(ICON_PATH))
            self.root.iconphoto(True, full)
            # 512px asset -> 48px header mark.
            self._icon_image = full.subsample(11, 11)
        except tk.TclError:
            self._icon_image = None

    def _build_header(self):
        header = tk.Frame(self.root, bg=SURFACE, height=76)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        left = tk.Frame(header, bg=SURFACE)
        left.pack(side="left", padx=18, pady=12)

        if self._icon_image is not None:
            tk.Label(left, image=self._icon_image, bg=SURFACE).pack(side="left")

        titles = tk.Frame(left, bg=SURFACE)
        titles.pack(side="left", padx=12)
        tk.Label(
            titles, text=config.ASSISTANT_NAME, bg=SURFACE, fg=TEXT, font=self.f_title
        ).pack(anchor="w")
        self.subtitle = tk.Label(
            titles,
            text=f"local · {config.CHAT_MODEL}",
            bg=SURFACE,
            fg=MUTED,
            font=self.f_sub,
        )
        self.subtitle.pack(anchor="w")

        actions = tk.Frame(header, bg=SURFACE)
        actions.pack(side="right", padx=16)
        self._ghost_button(actions, "Research…", self._on_learn).pack(side="left", padx=4)
        self._ghost_button(actions, "Queue…", self._on_curious).pack(side="left", padx=4)
        self._ghost_button(actions, "Memory", self._on_memory).pack(side="left", padx=4)

    def _ghost_button(self, parent, label, command):
        btn = tk.Button(
            parent,
            text=label,
            command=command,
            bg=SURFACE_HI,
            fg=TEXT,
            activebackground=USER_BUBBLE,
            activeforeground=TEXT,
            relief="flat",
            font=self.f_sub,
            padx=14,
            pady=7,
            cursor="hand2",
            highlightthickness=0,
            bd=0,
        )
        return btn

    def _build_transcript(self):
        wrap = tk.Frame(self.root, bg=BG)
        wrap.pack(fill="both", expand=True, padx=0, pady=0)

        self.text = tk.Text(
            wrap,
            bg=BG,
            fg=TEXT,
            font=self.f_body,
            wrap="word",
            relief="flat",
            highlightthickness=0,
            padx=26,
            pady=20,
            spacing1=2,
            spacing3=4,
            cursor="arrow",
            insertbackground=BG,
        )
        self.text.pack(side="left", fill="both", expand=True)

        scroll = tk.Scrollbar(
            wrap, command=self.text.yview, bg=BG, troughcolor=BG,
            activebackground=DIM, relief="flat", bd=0, width=10,
        )
        scroll.pack(side="right", fill="y")
        self.text.configure(yscrollcommand=scroll.set)

        # Message styling. Text can't do rounded corners, so bubbles are built
        # from background colour plus generous margins and spacing.
        self.text.tag_configure(
            "user",
            background=USER_BUBBLE, foreground=TEXT,
            lmargin1=140, lmargin2=140, rmargin=8,
            spacing1=8, spacing3=8, borderwidth=10, relief="flat",
            justify="right",
        )
        self.text.tag_configure(
            "assistant",
            background=SURFACE, foreground=TEXT,
            lmargin1=8, lmargin2=8, rmargin=140,
            spacing1=8, spacing3=8, borderwidth=10, relief="flat",
        )
        self.text.tag_configure(
            "who_user", foreground=ACCENT, font=self.f_name,
            lmargin1=140, lmargin2=140, rmargin=8, spacing1=12, justify="right",
        )
        self.text.tag_configure(
            "who_assistant", foreground=VIOLET, font=self.f_name,
            lmargin1=8, lmargin2=8, spacing1=12,
        )
        self.text.tag_configure(
            "notice", foreground=MUTED, font=self.f_sub,
            lmargin1=8, lmargin2=8, spacing1=10, spacing3=6,
        )
        self.text.tag_configure(
            "research", foreground=ACCENT, font=self.f_mono,
            lmargin1=8, lmargin2=8, spacing1=8,
        )
        self.text.tag_configure(
            "error", foreground="#FF8A8A", font=self.f_sub,
            lmargin1=8, lmargin2=8, spacing1=10, spacing3=6,
        )
        self.text.configure(state="disabled")

    def _build_composer(self):
        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", side="bottom", padx=18, pady=(0, 6))

        shell = tk.Frame(bar, bg=SURFACE_HI, highlightthickness=0)
        shell.pack(fill="x", pady=8)

        self.entry = tk.Entry(
            shell,
            bg=SURFACE_HI,
            fg=TEXT,
            insertbackground=ACCENT,
            relief="flat",
            font=self.f_body,
            highlightthickness=0,
            bd=0,
        )
        self.entry.pack(side="left", fill="x", expand=True, padx=16, pady=13)
        self.entry.bind("<Return>", lambda _e: self._on_send())
        self.entry.bind("<FocusIn>", self._clear_placeholder)
        self.entry.bind("<FocusOut>", self._restore_placeholder)
        self._placeholder_active = False
        self._restore_placeholder()

        self.send_btn = tk.Button(
            shell,
            text="Send",
            command=self._on_send,
            bg=ACCENT,
            fg="#08122B",
            activebackground=VIOLET,
            activeforeground="#08122B",
            relief="flat",
            font=self.f_name,
            padx=20,
            pady=8,
            cursor="hand2",
            highlightthickness=0,
            bd=0,
        )
        self.send_btn.pack(side="right", padx=8, pady=6)

    PLACEHOLDER = "Ask anything — I'll look it up if I don't know…"

    def _clear_placeholder(self, _event=None):
        if self._placeholder_active:
            self.entry.delete(0, "end")
            self.entry.configure(fg=TEXT)
            self._placeholder_active = False

    def _restore_placeholder(self, _event=None):
        if not self.entry.get():
            self._placeholder_active = True
            self.entry.configure(fg=DIM)
            self.entry.insert(0, self.PLACEHOLDER)

    def _build_statusbar(self):
        bar = tk.Frame(self.root, bg=BG, height=26)
        bar.pack(fill="x", side="bottom")
        self.status = tk.Label(
            bar, text="Ready", bg=BG, fg=DIM, font=self.f_sub, anchor="w"
        )
        self.status.pack(side="left", padx=22, pady=(0, 8))
        self.memory_label = tk.Label(
            bar, text="", bg=BG, fg=DIM, font=self.f_sub, anchor="e"
        )
        self.memory_label.pack(side="right", padx=22, pady=(0, 8))

    # --- transcript helpers ---------------------------------------------

    def _append(self, body, tag, who=None, who_tag=None):
        self.text.configure(state="normal")
        if who:
            self.text.insert("end", f"{who}\n", who_tag)
        self.text.insert("end", f"{body}\n", tag)
        self.text.configure(state="disabled")
        self.text.see("end")

    def _welcome(self):
        self._append(
            "I run entirely on this Chromebook and remember what I learn. Ask me "
            "anything — if I don't know, I'll research it myself and keep the notes.",
            "assistant",
            who=config.ASSISTANT_NAME,
            who_tag="who_assistant",
        )

    # --- actions ---------------------------------------------------------

    def _set_busy(self, busy, label="Thinking"):
        self.busy = busy
        self.send_btn.configure(
            state="disabled" if busy else "normal",
            bg=DIM if busy else ACCENT,
        )
        if busy:
            self._spinner_label = label
            self._spin()
        else:
            self.status.configure(text="Ready")

    def _spin(self):
        if not self.busy:
            return
        dots = "." * (self._spinner_step % 4)
        self.status.configure(text=f"{self._spinner_label}{dots}")
        self._spinner_step += 1
        self.root.after(400, self._spin)

    def _on_send(self):
        if self.busy:
            return
        if self._placeholder_active:
            return
        message = self.entry.get().strip()
        if not message:
            return
        self.entry.delete(0, "end")
        self._append(message, "user", who="You", who_tag="who_user")
        self._set_busy(True, "Thinking")
        self.requests.put(Request(kind="chat", payload=message))
        # The entry keeps focus after sending, so no FocusOut fires to bring
        # the hint back on its own.
        if self.root.focus_get() is not self.entry:
            self._restore_placeholder()

    def _prompt(self, title, prompt):
        return simpledialog.askstring(title, prompt, parent=self.root)

    def _on_learn(self):
        if self.busy:
            return
        topic = self._prompt("Research now", "What should I research?")
        if not topic:
            return
        self._append(f"Researching “{topic}” …", "notice")
        self._set_busy(True, "Researching")
        self.requests.put(Request(kind="learn", payload=topic))

    def _on_curious(self):
        topic = self._prompt("Queue for later", "What should I look into later?")
        if not topic:
            return
        self.requests.put(Request(kind="curious", payload=topic))

    def _on_memory(self):
        messagebox.showinfo(
            "Memory",
            f"{self._note_count} note(s) stored.\n"
            f"{self._pending} topic(s) queued for background research.\n\n"
            f"Chat model: {config.CHAT_MODEL}\n"
            f"Database: {config.MEMORY_DB_PATH}",
            parent=self.root,
        )

    _note_count = 0
    _pending = 0

    # --- worker plumbing -------------------------------------------------

    def _drain_responses(self):
        try:
            while True:
                response = self.responses.get_nowait()
                self._handle(response)
        except queue.Empty:
            pass
        self.root.after(60, self._drain_responses)

    def _handle(self, response: Response):
        if response.kind == "stats":
            self._note_count = response.note_count
            self._pending = response.pending
            notes = "note" if response.note_count == 1 else "notes"
            self.memory_label.configure(
                text=f"{response.note_count} {notes} · {response.pending} queued"
            )
            return

        if response.kind == "chat":
            if response.researched:
                self._append(
                    f"↗ researched “{response.researched}” · "
                    f"{response.notes_added} note(s) saved",
                    "research",
                )
            self._append(
                response.text, "assistant",
                who=config.ASSISTANT_NAME, who_tag="who_assistant",
            )
            self._set_busy(False)
            return

        if response.kind == "notice":
            self._append(response.text, "notice")
            self._set_busy(False)
            return

        if response.kind == "error":
            self._append(response.error, "error")
            self._set_busy(False)
            return

        if response.kind == "fatal":
            self._append(f"Couldn't start: {response.error}", "error")
            self._set_busy(False)


def main():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(
            "Couldn't open a window. If you're on a Chromebook, install the Tk "
            "bindings with:\n  sudo apt-get install -y python3-tk\n"
            f"({exc})",
            file=sys.stderr,
        )
        raise SystemExit(1)

    CortanaApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

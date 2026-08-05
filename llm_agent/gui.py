"""Cortana's desktop app -- a real window, not a terminal.

All model work happens on a single background worker thread that owns the
Memory and conversation stores. That matters for two reasons: the UI never
freezes while a local model is generating (which on a Chromebook can take
many seconds), and SQLite connections stay on one thread, which sqlite3
requires by default.

The UI thread never touches the agent, and the worker never touches Tk. They
communicate through two queues.
"""

import queue
import sys
import threading
import tkinter as tk
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox, simpledialog
from typing import Any, Dict, List, Optional

from . import config
from .agent import Agent
from .conversations import ConversationStore, Message
from .llm_client import OllamaError
from .memory import Memory
from .topics import TopicQueue

# Palette, matching the launcher icon.
BG = "#0D1230"
SIDEBAR = "#090D24"
SIDEBAR_HOVER = "#141C3F"
SIDEBAR_ACTIVE = "#1E2A5A"
SURFACE = "#161F44"
SURFACE_HI = "#1E2A5A"
USER_BUBBLE = "#243A7A"
ACCENT = "#2DE2E6"
VIOLET = "#A98BFA"
TEXT = "#E6ECFF"
MUTED = "#8FA0C8"
DIM = "#5D6E9E"
LINE = "#1B2450"

SIDEBAR_WIDTH = 248
ICON_PATH = Path(__file__).resolve().parent.parent / "assets" / "cortana.png"


@dataclass
class Request:
    kind: str
    payload: str = ""
    conversation_id: Optional[int] = None


@dataclass
class Response:
    kind: str
    text: str = ""
    researched: str = ""
    notes_added: int = 0
    note_count: int = 0
    pending: int = 0
    error: str = ""
    conversation_id: Optional[int] = None
    chats: List[Any] = field(default_factory=list)
    messages: List[Message] = field(default_factory=list)
    title: str = ""
    # True only for responses to a request that put the UI into its busy
    # state. Without this, a quick "topic queued" notice landing mid-chat
    # would re-enable Send and let a second request overlap the first.
    clears_busy: bool = False


def relative_time(iso: str) -> str:
    try:
        then = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    seconds = (datetime.now(timezone.utc) - then).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    if seconds < 172800:
        return "yesterday"
    return f"{int(seconds // 86400)}d ago"


def _pick_font(root, candidates, size, weight="normal"):
    """Use the nicest font actually installed rather than assuming one."""
    available = set(tkfont.families(root))
    for name in candidates:
        if name in available:
            return tkfont.Font(root=root, family=name, size=size, weight=weight)
    return tkfont.Font(root=root, size=size, weight=weight)


class Worker(threading.Thread):
    """Owns the agent and the stores; serialises all model work onto one thread."""

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
            chats = ConversationStore()
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            self.responses.put(Response(kind="fatal", error=str(exc)))
            return

        # Reopen whatever was last in use, or start a first conversation.
        current = chats.most_recent()
        current_id = current.id if current else chats.create()
        self.responses.put(
            Response(
                kind="opened",
                conversation_id=current_id,
                messages=chats.messages(current_id),
                chats=chats.list(),
            )
        )
        self._emit_stats(memory, topics)

        while True:
            request = self.requests.get()
            if request is None:
                break
            try:
                self._handle(request, agent, chats, topics)
            except OllamaError as exc:
                self.responses.put(
                    Response(kind="error", error=str(exc), clears_busy=True)
                )
            except Exception as exc:  # noqa: BLE001
                self.responses.put(
                    Response(kind="error", error=str(exc), clears_busy=True)
                )
            self._emit_stats(memory, topics)

        memory.close()
        topics.close()
        chats.close()

    def _handle(self, request, agent, chats, topics):
        if request.kind == "chat":
            # Don't depend on the UI having opened a conversation first; a
            # missing id would otherwise surface as a NOT NULL constraint
            # error rather than simply working.
            cid = request.conversation_id or chats.create()
            chats.add_message(cid, "user", request.payload)
            # History is read back from the store, so a conversation resumed
            # after a restart keeps its context.
            history = chats.history(cid)[:-1]  # exclude the message just added
            result = agent.chat(request.payload, history=history)
            chats.add_message(
                cid, "assistant", result.answer, researched=result.researched_query
            )
            self.responses.put(
                Response(
                    kind="chat",
                    text=result.answer,
                    researched=result.researched_query,
                    notes_added=result.notes_added,
                    conversation_id=cid,
                    chats=chats.list(),
                    clears_busy=True,
                )
            )

        elif request.kind == "new_chat":
            cid = chats.create()
            self.responses.put(
                Response(kind="opened", conversation_id=cid, messages=[],
                         chats=chats.list())
            )

        elif request.kind == "open_chat":
            cid = request.conversation_id
            self.responses.put(
                Response(kind="opened", conversation_id=cid,
                         messages=chats.messages(cid), chats=chats.list())
            )

        elif request.kind == "delete_chat":
            chats.delete(request.conversation_id)
            remaining = chats.most_recent()
            cid = remaining.id if remaining else chats.create()
            self.responses.put(
                Response(kind="opened", conversation_id=cid,
                         messages=chats.messages(cid), chats=chats.list())
            )

        elif request.kind == "rename_chat":
            chats.rename(request.conversation_id, request.payload)
            self.responses.put(Response(kind="chats", chats=chats.list()))

        elif request.kind == "learn":
            learned = agent.learn(request.payload)
            text = (
                f"Couldn't reach the web to research “{request.payload}”."
                if not learned.reachable
                else f"Learned {len(learned.notes_added)} note(s) about "
                     f"“{request.payload}”."
            )
            self.responses.put(Response(kind="notice", text=text, clears_busy=True))

        elif request.kind == "curious":
            added = topics.add(request.payload)
            self.responses.put(
                Response(
                    kind="notice",
                    text=f"Queued “{request.payload}” for background research."
                    if added
                    else f"“{request.payload}” is already known or queued.",
                )
            )

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
    PLACEHOLDER = "Ask anything — I'll look it up if I don't know…"

    def __init__(self, root: tk.Tk):
        self.root = root
        self.requests: "queue.Queue[Optional[Request]]" = queue.Queue()
        self.responses: "queue.Queue[Response]" = queue.Queue()
        self.busy = False
        self._spinner_step = 0
        self._spinner_label = "Thinking"
        self._icon_image = None
        self.conversation_id: Optional[int] = None
        self._chat_rows: Dict[int, Dict[str, Any]] = {}
        self._note_count = 0
        self._pending = 0

        root.title(config.ASSISTANT_NAME)
        root.configure(bg=BG)
        root.geometry("1040x700")
        root.minsize(640, 460)

        self.f_title = _pick_font(root, ["Inter", "Ubuntu", "DejaVu Sans"], 15, "bold")
        self.f_sub = _pick_font(root, ["Inter", "Ubuntu", "DejaVu Sans"], 9)
        self.f_body = _pick_font(root, ["Inter", "Ubuntu", "DejaVu Sans"], 12)
        self.f_name = _pick_font(root, ["Inter", "Ubuntu", "DejaVu Sans"], 10, "bold")
        self.f_row = _pick_font(root, ["Inter", "Ubuntu", "DejaVu Sans"], 10)
        self.f_mono = _pick_font(root, ["JetBrains Mono", "DejaVu Sans Mono"], 9)

        self._load_icon()
        self._build_sidebar()
        self._build_main()

        Worker(self.requests, self.responses).start()
        self.root.after(60, self._drain_responses)
        self.entry.focus_set()

    # --- chrome ---------------------------------------------------------

    def _load_icon(self):
        if not ICON_PATH.exists():
            return
        try:
            full = tk.PhotoImage(file=str(ICON_PATH))
            self.root.iconphoto(True, full)
            self._icon_image = full.subsample(16, 16)  # 512 -> 32px
        except tk.TclError:
            self._icon_image = None

    def _build_sidebar(self):
        bar = tk.Frame(self.root, bg=SIDEBAR, width=SIDEBAR_WIDTH)
        bar.pack(side="left", fill="y")
        bar.pack_propagate(False)

        brand = tk.Frame(bar, bg=SIDEBAR)
        brand.pack(fill="x", padx=16, pady=(16, 12))
        if self._icon_image is not None:
            tk.Label(brand, image=self._icon_image, bg=SIDEBAR).pack(side="left")
        tk.Label(
            brand, text=config.ASSISTANT_NAME, bg=SIDEBAR, fg=TEXT, font=self.f_title
        ).pack(side="left", padx=10)

        new_btn = tk.Button(
            bar,
            text="+   New chat",
            anchor="w",
            command=self._on_new_chat,
            bg=SIDEBAR_ACTIVE,
            fg=TEXT,
            activebackground=USER_BUBBLE,
            activeforeground=TEXT,
            relief="flat",
            font=self.f_name,
            padx=14,
            pady=10,
            cursor="hand2",
            highlightthickness=0,
            bd=0,
        )
        new_btn.pack(fill="x", padx=12, pady=(0, 12))

        tk.Label(
            bar, text="CHATS", bg=SIDEBAR, fg=DIM, font=self.f_sub, anchor="w"
        ).pack(fill="x", padx=18, pady=(0, 4))

        # Scrollable list, since conversations accumulate.
        holder = tk.Frame(bar, bg=SIDEBAR)
        holder.pack(fill="both", expand=True, padx=6)
        self._chat_canvas = tk.Canvas(
            holder, bg=SIDEBAR, highlightthickness=0, bd=0
        )
        self._chat_canvas.pack(side="left", fill="both", expand=True)
        self.chat_list = tk.Frame(self._chat_canvas, bg=SIDEBAR)
        self._chat_window = self._chat_canvas.create_window(
            (0, 0), window=self.chat_list, anchor="nw"
        )
        self.chat_list.bind(
            "<Configure>",
            lambda _e: self._chat_canvas.configure(
                scrollregion=self._chat_canvas.bbox("all")
            ),
        )
        self._chat_canvas.bind(
            "<Configure>",
            lambda e: self._chat_canvas.itemconfigure(self._chat_window, width=e.width),
        )
        self._chat_canvas.bind_all(
            "<Button-4>", lambda _e: self._chat_canvas.yview_scroll(-1, "units")
        )
        self._chat_canvas.bind_all(
            "<Button-5>", lambda _e: self._chat_canvas.yview_scroll(1, "units")
        )

        footer = tk.Frame(bar, bg=SIDEBAR)
        footer.pack(fill="x", side="bottom", pady=12, padx=16)
        tk.Frame(footer, bg=LINE, height=1).pack(fill="x", pady=(0, 10))
        self.memory_label = tk.Label(
            footer, text="", bg=SIDEBAR, fg=MUTED, font=self.f_sub, anchor="w"
        )
        self.memory_label.pack(fill="x")
        tk.Label(
            footer, text=f"local · {config.CHAT_MODEL}", bg=SIDEBAR, fg=DIM,
            font=self.f_sub, anchor="w",
        ).pack(fill="x", pady=(2, 0))

    def _build_main(self):
        main = tk.Frame(self.root, bg=BG)
        main.pack(side="left", fill="both", expand=True)

        # Top bar: current chat title and the research actions.
        top = tk.Frame(main, bg=BG, height=58)
        top.pack(fill="x")
        top.pack_propagate(False)
        self.title_label = tk.Label(
            top, text="New chat", bg=BG, fg=TEXT, font=self.f_title, anchor="w"
        )
        self.title_label.pack(side="left", padx=24)
        actions = tk.Frame(top, bg=BG)
        actions.pack(side="right", padx=18)
        for label, cmd in (
            ("Research…", self._on_learn),
            ("Queue…", self._on_curious),
            ("Memory", self._on_memory),
        ):
            self._ghost_button(actions, label, cmd).pack(side="left", padx=4)
        tk.Frame(main, bg=LINE, height=1).pack(fill="x")

        # Fixed-height widgets must be packed before the transcript, which
        # expands to fill whatever is left -- otherwise they get squeezed out.
        self._build_statusbar(main)
        self._build_composer(main)
        self._build_transcript(main)

    def _ghost_button(self, parent, label, command):
        return tk.Button(
            parent, text=label, command=command,
            bg=SURFACE, fg=MUTED,
            activebackground=SURFACE_HI, activeforeground=TEXT,
            relief="flat", font=self.f_sub,
            padx=13, pady=7, cursor="hand2", highlightthickness=0, bd=0,
        )

    def _build_transcript(self, parent):
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(fill="both", expand=True)

        self.text = tk.Text(
            wrap, bg=BG, fg=TEXT, font=self.f_body, wrap="word", relief="flat",
            highlightthickness=0, padx=26, pady=18, spacing1=2, spacing3=4,
            cursor="arrow", insertbackground=BG,
        )
        self.text.pack(side="left", fill="both", expand=True)

        scroll = tk.Scrollbar(
            wrap, command=self.text.yview, bg=BG, troughcolor=BG,
            activebackground=DIM, relief="flat", bd=0, width=10,
        )
        scroll.pack(side="right", fill="y")
        self.text.configure(yscrollcommand=scroll.set)

        # Text can't do rounded corners, so bubbles are background colour plus
        # generous margins and spacing.
        self.text.tag_configure(
            "user", background=USER_BUBBLE, foreground=TEXT,
            lmargin1=150, lmargin2=150, rmargin=8,
            spacing1=7, spacing3=7, borderwidth=10, relief="flat", justify="right",
        )
        self.text.tag_configure(
            "assistant", background=SURFACE, foreground=TEXT,
            lmargin1=8, lmargin2=8, rmargin=150,
            spacing1=7, spacing3=7, borderwidth=10, relief="flat",
        )
        self.text.tag_configure(
            "who_user", foreground=ACCENT, font=self.f_name,
            lmargin1=150, lmargin2=150, rmargin=8, spacing1=14, justify="right",
        )
        self.text.tag_configure(
            "who_assistant", foreground=VIOLET, font=self.f_name,
            lmargin1=8, lmargin2=8, spacing1=14,
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

    def _build_composer(self, parent):
        bar = tk.Frame(parent, bg=BG)
        bar.pack(fill="x", side="bottom", padx=22, pady=(0, 4))

        shell = tk.Frame(bar, bg=SURFACE_HI)
        shell.pack(fill="x", pady=8)

        # The hint is a separate label rather than pre-filled text, so it can
        # never be mistaken for real input and sent as a message.
        self.entry_var = tk.StringVar()
        self.entry = tk.Entry(
            shell, textvariable=self.entry_var, bg=SURFACE_HI, fg=TEXT,
            insertbackground=ACCENT, relief="flat", font=self.f_body,
            highlightthickness=0, bd=0,
        )
        self.entry.pack(side="left", fill="x", expand=True, padx=16, pady=13)
        self.entry.bind("<Return>", lambda _e: self._on_send())

        self.hint = tk.Label(
            shell, text=self.PLACEHOLDER, bg=SURFACE_HI, fg=DIM, font=self.f_body
        )
        self.hint.bind("<Button-1>", lambda _e: self.entry.focus_set())
        self.entry_var.trace_add("write", lambda *_: self._sync_hint())
        self._sync_hint()

        self.send_btn = tk.Button(
            shell, text="Send", command=self._on_send,
            bg=ACCENT, fg="#08122B",
            activebackground=VIOLET, activeforeground="#08122B",
            relief="flat", font=self.f_name, padx=20, pady=8,
            cursor="hand2", highlightthickness=0, bd=0,
        )
        self.send_btn.pack(side="right", padx=8, pady=6)

    def _build_statusbar(self, parent):
        bar = tk.Frame(parent, bg=BG, height=24)
        bar.pack(fill="x", side="bottom")
        self.status = tk.Label(
            bar, text="Ready", bg=BG, fg=DIM, font=self.f_sub, anchor="w"
        )
        self.status.pack(side="left", padx=26, pady=(0, 8))

    def _sync_hint(self):
        """Show the hint only while the box is genuinely empty."""
        if self.entry_var.get():
            self.hint.place_forget()
        else:
            self.hint.place(in_=self.entry, relx=0.0, rely=0.5, anchor="w", x=1)

    # --- conversation list ----------------------------------------------

    def _render_chat_list(self, chats):
        for child in self.chat_list.winfo_children():
            child.destroy()
        self._chat_rows.clear()

        for chat in chats:
            selected = chat.id == self.conversation_id
            bg = SIDEBAR_ACTIVE if selected else SIDEBAR
            row = tk.Frame(self.chat_list, bg=bg)
            row.pack(fill="x", pady=1, padx=4)

            # A visible delete control: right-click menus aren't discoverable,
            # and on a Chromebook trackpad they need a two-finger tap.
            close = tk.Label(
                row, text="✕", bg=bg, fg=DIM, font=self.f_sub, cursor="hand2",
                padx=8,
            )
            close.pack(side="right", fill="y")
            close.bind("<Button-1>", lambda _e, c=chat: self._on_delete(c))
            close.bind("<Enter>", lambda e: e.widget.configure(fg="#FF8A8A"))
            close.bind("<Leave>", lambda e: e.widget.configure(fg=DIM))

            body = tk.Frame(row, bg=bg)
            body.pack(side="left", fill="x", expand=True)
            title = tk.Label(
                body, text=chat.title, bg=bg, fg=TEXT if selected else MUTED,
                font=self.f_row, anchor="w", justify="left",
            )
            title.pack(fill="x", padx=10, pady=(7, 0))
            when = tk.Label(
                body, text=relative_time(chat.updated_at), bg=bg, fg=DIM,
                font=self.f_sub, anchor="w",
            )
            when.pack(fill="x", padx=10, pady=(0, 7))

            clickable = (row, body, title, when)
            for widget in clickable:
                widget.bind("<Button-1>", lambda _e, i=chat.id: self._on_open_chat(i))
                widget.configure(cursor="hand2")
            # Right-click anywhere on the row for rename/delete as well.
            for widget in clickable + (close,):
                widget.bind("<Button-3>", lambda e, c=chat: self._chat_menu(e, c))

            if not selected:
                tinted = clickable + (close,)
                for widget in tinted:
                    widget.bind("<Enter>", lambda _e, w=tinted: self._hover(w, True))
                    widget.bind("<Leave>", lambda _e, w=tinted: self._hover(w, False))
            self._chat_rows[chat.id] = {"row": row, "widgets": clickable}

    def _hover(self, widgets, entering):
        colour = SIDEBAR_HOVER if entering else SIDEBAR
        for widget in widgets:
            try:
                widget.configure(bg=colour)
            except tk.TclError:
                pass  # row was rebuilt while the pointer was over it

    def _chat_menu(self, event, chat):
        menu = tk.Menu(self.root, tearoff=0, bg=SURFACE, fg=TEXT,
                       activebackground=SURFACE_HI, activeforeground=TEXT, bd=0)
        menu.add_command(label="Rename…", command=lambda: self._on_rename(chat))
        menu.add_command(label="Delete", command=lambda: self._on_delete(chat))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # --- actions ---------------------------------------------------------

    def _set_busy(self, busy, label="Thinking"):
        self.busy = busy
        self.send_btn.configure(
            state="disabled" if busy else "normal", bg=DIM if busy else ACCENT
        )
        if busy:
            self._spinner_label = label
            self._spin()
        else:
            self.status.configure(text="Ready")

    def _spin(self):
        if not self.busy:
            return
        self.status.configure(text=f"{self._spinner_label}{'.' * (self._spinner_step % 4)}")
        self._spinner_step += 1
        self.root.after(400, self._spin)

    def _append(self, body, tag, who=None, who_tag=None):
        # A blank line inside a bubble would inherit the bubble's background
        # and its full above/below spacing, leaving a tall empty band. Each
        # paragraph becomes its own logical line instead, so the tag's own
        # spacing provides the separation.
        body = "\n".join(p.strip() for p in str(body).split("\n") if p.strip())
        self.text.configure(state="normal")
        if who:
            self.text.insert("end", f"{who}\n", who_tag)
        self.text.insert("end", f"{body}\n", tag)
        self.text.configure(state="disabled")
        self.text.see("end")

    def _clear_transcript(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def _show_empty_state(self):
        self._append(
            "I run entirely on this Chromebook and remember what I learn. Ask me "
            "anything — if I don't know, I'll research it myself and keep the notes.",
            "assistant", who=config.ASSISTANT_NAME, who_tag="who_assistant",
        )

    def _on_send(self):
        if self.busy or self.conversation_id is None:
            return
        message = self.entry_var.get().strip()
        if not message:
            return
        self.entry_var.set("")
        self._append(message, "user", who="You", who_tag="who_user")
        self._set_busy(True, "Thinking")
        self.requests.put(
            Request(kind="chat", payload=message, conversation_id=self.conversation_id)
        )

    def _on_new_chat(self):
        if self.busy:
            return
        self.requests.put(Request(kind="new_chat"))

    def _on_open_chat(self, conversation_id):
        if self.busy or conversation_id == self.conversation_id:
            return
        self.requests.put(
            Request(kind="open_chat", conversation_id=conversation_id)
        )

    def _on_delete(self, chat):
        if self.busy:
            return
        if messagebox.askyesno(
            "Delete chat", f"Delete “{chat.title}”?\n\n"
            "Notes Cortana learned stay in her memory; only this conversation "
            "is removed.", parent=self.root,
        ):
            self.requests.put(
                Request(kind="delete_chat", conversation_id=chat.id)
            )

    def _on_rename(self, chat):
        new_title = simpledialog.askstring(
            "Rename chat", "New name:", initialvalue=chat.title, parent=self.root
        )
        if new_title:
            self.requests.put(
                Request(kind="rename_chat", payload=new_title.strip(),
                        conversation_id=chat.id)
            )

    def _on_learn(self):
        if self.busy:
            return
        topic = simpledialog.askstring(
            "Research now", "What should I research?", parent=self.root
        )
        if not topic:
            return
        self._append(f"Researching “{topic}” …", "notice")
        self._set_busy(True, "Researching")
        self.requests.put(Request(kind="learn", payload=topic))

    def _on_curious(self):
        topic = simpledialog.askstring(
            "Queue for later", "What should I look into later?", parent=self.root
        )
        if topic:
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

    # --- worker plumbing -------------------------------------------------

    def _drain_responses(self):
        try:
            while True:
                self._handle(self.responses.get_nowait())
        except queue.Empty:
            pass
        self.root.after(60, self._drain_responses)

    def _handle(self, response: Response):
        if response.kind == "stats":
            self._note_count = response.note_count
            self._pending = response.pending
            notes = "note" if response.note_count == 1 else "notes"
            self.memory_label.configure(
                text=f"{response.note_count} {notes} learned · "
                     f"{response.pending} queued"
            )
            return

        if response.kind == "opened":
            self.conversation_id = response.conversation_id
            self._clear_transcript()
            if response.messages:
                for message in response.messages:
                    if message.role == "user":
                        self._append(message.content, "user",
                                     who="You", who_tag="who_user")
                    else:
                        if message.researched:
                            self._append(
                                f"↗ researched “{message.researched}”", "research"
                            )
                        self._append(message.content, "assistant",
                                     who=config.ASSISTANT_NAME,
                                     who_tag="who_assistant")
            else:
                self._show_empty_state()
            self._render_chat_list(response.chats)
            self._update_title(response.chats)
            self._set_busy(False)
            return

        if response.kind == "chats":
            self._render_chat_list(response.chats)
            self._update_title(response.chats)
            return

        if response.kind == "chat":
            if response.researched:
                self._append(
                    f"↗ researched “{response.researched}” · "
                    f"{response.notes_added} note(s) saved", "research",
                )
            self._append(response.text, "assistant",
                         who=config.ASSISTANT_NAME, who_tag="who_assistant")
            self._render_chat_list(response.chats)
            self._update_title(response.chats)

        elif response.kind == "notice":
            self._append(response.text, "notice")

        elif response.kind in ("error", "fatal"):
            self._append(
                response.error if response.kind == "error"
                else f"Couldn't start: {response.error}", "error",
            )

        if response.clears_busy:
            self._set_busy(False)

    def _update_title(self, chats):
        for chat in chats:
            if chat.id == self.conversation_id:
                self.title_label.configure(text=chat.title)
                return
        self.title_label.configure(text="New chat")


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

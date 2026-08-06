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
from typing import Any, Dict, List, Optional

from . import config, dialogs, theme
from .transcript import Transcript
from .agent import Agent
from .conversations import ConversationStore, Message
from .llm_client import OllamaError
from .memory import Memory
from .topics import TopicQueue

SIDEBAR_WIDTH = 252
ICON_PATH = Path(__file__).resolve().parent.parent / "assets" / "cortana.png"

# ChromeOS matches a window to its launcher entry by WM_CLASS. Tk sets that
# to "Tk" unless told otherwise, which is why the shelf showed a generic icon
# instead of Cortana's -- this has to match StartupWMClass in the .desktop.
WM_CLASS = "Cortana"

SUGGESTIONS = [
    "What can you do?",
    "Explain how you remember things",
    "What's new in ChromeOS?",
]


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

        current = chats.most_recent()
        current_id = current.id if current else chats.create()
        self.responses.put(
            Response(kind="opened", conversation_id=current_id,
                     messages=chats.messages(current_id), chats=chats.list())
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
                Response(kind="chat", text=result.answer,
                         researched=result.researched_query,
                         notes_added=result.notes_added, conversation_id=cid,
                         chats=chats.list(), clears_busy=True)
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
                Response(kind="stats", note_count=memory.count(),
                         pending=topics.pending_count())
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
        self._icon_large = None
        self.conversation_id: Optional[int] = None
        self._chat_rows: Dict[int, Dict[str, Any]] = {}
        self._note_count = 0
        self._pending = 0
        self._has_messages = False

        root.title(config.ASSISTANT_NAME)
        root.configure(bg=theme.BG)
        root.geometry("1060x720")
        root.minsize(700, 480)

        self.f_brand = theme.pick(root, 16, "bold")
        self.f_title = theme.pick(root, 14, "bold")
        self.f_hero = theme.pick(root, 26, "bold")
        self.f_sub = theme.pick(root, 9)
        self.f_body = theme.pick(root, 12)
        self.f_name = theme.pick(root, 10, "bold")
        self.f_row = theme.pick(root, 10)
        self.f_mono = theme.pick(root, 9, mono=True)

        self._load_icon()
        self._build_sidebar()
        self._build_main()

        self._bind_wheel()
        Worker(self.requests, self.responses).start()
        self.root.after(60, self._drain_responses)
        self.entry.focus_set()

    def _bind_wheel(self):
        """Send wheel events to whichever scrollable area the pointer is over."""
        def target(event):
            widget = self.root.winfo_containing(event.x_root, event.y_root)
            while widget is not None:
                if widget is self._chat_canvas:
                    return self._chat_canvas
                if widget is self.transcript_wrap.canvas or \
                        widget is self.transcript_wrap.inner:
                    return self.transcript_wrap.canvas
                if widget is self.transcript_wrap:
                    return self.transcript_wrap.canvas
                widget = getattr(widget, "master", None)
            return None

        def scroll(event, direction):
            canvas = target(event)
            if canvas is not None:
                canvas.yview_scroll(direction, "units")

        self.root.bind_all("<Button-4>", lambda e: scroll(e, -1))
        self.root.bind_all("<Button-5>", lambda e: scroll(e, 1))
        self.root.bind_all(
            "<MouseWheel>", lambda e: scroll(e, -1 if e.delta > 0 else 1))

    # --- chrome ---------------------------------------------------------

    def _load_icon(self):
        if not ICON_PATH.exists():
            return
        try:
            full = tk.PhotoImage(file=str(ICON_PATH))
            self.root.iconphoto(True, full)
            self._icon_image = full.subsample(16, 16)  # 512 -> 32px
            self._icon_large = full.subsample(8, 8)  # 512 -> 64px
        except tk.TclError:
            self._icon_image = None

    def _build_sidebar(self):
        bar = tk.Frame(self.root, bg=theme.SIDEBAR, width=SIDEBAR_WIDTH)
        bar.pack(side="left", fill="y")
        bar.pack_propagate(False)

        brand = tk.Frame(bar, bg=theme.SIDEBAR)
        brand.pack(fill="x", padx=18, pady=(18, 14))
        if self._icon_image is not None:
            tk.Label(brand, image=self._icon_image, bg=theme.SIDEBAR).pack(side="left")
        tk.Label(
            brand, text=config.ASSISTANT_NAME, bg=theme.SIDEBAR, fg=theme.TEXT,
            font=self.f_brand,
        ).pack(side="left", padx=10)

        theme.flat_button(
            bar, "  +   New chat", self._on_new_chat, font=self.f_name,
            bg=theme.SIDEBAR_ACTIVE, fg=theme.TEXT, hover=theme.USER_BUBBLE,
            padx=14, pady=11, anchor="w",
        ).pack(fill="x", padx=12, pady=(0, 14))

        tk.Label(
            bar, text="CHATS", bg=theme.SIDEBAR, fg=theme.DIM, font=self.f_sub,
            anchor="w",
        ).pack(fill="x", padx=20, pady=(0, 4))

        holder = tk.Frame(bar, bg=theme.SIDEBAR)
        holder.pack(fill="both", expand=True, padx=6)
        self._chat_canvas = tk.Canvas(holder, bg=theme.SIDEBAR,
                                      highlightthickness=0, bd=0)
        self._chat_canvas.pack(side="left", fill="both", expand=True)
        self.chat_list = tk.Frame(self._chat_canvas, bg=theme.SIDEBAR)
        self._chat_window = self._chat_canvas.create_window(
            (0, 0), window=self.chat_list, anchor="nw"
        )
        self.chat_list.bind(
            "<Configure>",
            lambda _e: self._chat_canvas.configure(
                scrollregion=self._chat_canvas.bbox("all")),
        )
        self._chat_canvas.bind(
            "<Configure>",
            lambda e: self._chat_canvas.itemconfigure(self._chat_window, width=e.width),
        )
        # Wheel events are routed by pointer position in _bind_wheel; binding
        # them per-canvas with bind_all would let whichever bound last win.

        footer = tk.Frame(bar, bg=theme.SIDEBAR)
        footer.pack(fill="x", side="bottom", pady=14, padx=18)
        tk.Frame(footer, bg=theme.LINE, height=1).pack(fill="x", pady=(0, 10))
        self.memory_label = tk.Label(
            footer, text="", bg=theme.SIDEBAR, fg=theme.MUTED, font=self.f_sub,
            anchor="w",
        )
        self.memory_label.pack(fill="x")
        tk.Label(
            footer, text=f"local · {config.CHAT_MODEL}", bg=theme.SIDEBAR,
            fg=theme.DIM, font=self.f_sub, anchor="w",
        ).pack(fill="x", pady=(2, 0))

    def _build_main(self):
        main = tk.Frame(self.root, bg=theme.BG)
        main.pack(side="left", fill="both", expand=True)

        top = tk.Frame(main, bg=theme.BG, height=60)
        top.pack(fill="x")
        top.pack_propagate(False)
        self.title_label = tk.Label(
            top, text="New chat", bg=theme.BG, fg=theme.TEXT, font=self.f_title,
            anchor="w",
        )
        self.title_label.pack(side="left", padx=26)
        actions = tk.Frame(top, bg=theme.BG)
        actions.pack(side="right", padx=20)
        for label, cmd in (("Research", self._on_learn),
                           ("Queue", self._on_curious),
                           ("Memory", self._on_memory)):
            theme.PillButton(
                actions, label, cmd, font=self.f_sub, bg=theme.SURFACE,
                fg=theme.MUTED, hover=theme.RAISED, padx=16, pady=8,
            ).pack(side="left", padx=4)
        tk.Frame(main, bg=theme.LINE, height=1).pack(fill="x")

        # Fixed-height widgets must be packed before the transcript, which
        # expands to fill whatever is left -- otherwise they get squeezed out.
        self._build_statusbar(main)
        self._build_composer(main)

        self._content = tk.Frame(main, bg=theme.BG)
        self._content.pack(fill="both", expand=True)
        self._build_welcome(self._content)
        self._build_transcript(self._content)

    # --- welcome view ----------------------------------------------------

    def _build_welcome(self, parent):
        self.welcome = tk.Frame(parent, bg=theme.BG)
        inner = tk.Frame(self.welcome, bg=theme.BG)
        inner.place(relx=0.5, rely=0.42, anchor="center")

        if self._icon_large is not None:
            tk.Label(inner, image=self._icon_large, bg=theme.BG).pack(pady=(0, 18))
        tk.Label(
            inner, text="How can I help?", bg=theme.BG, fg=theme.TEXT,
            font=self.f_hero,
        ).pack()
        tk.Label(
            inner,
            text="I run entirely on this Chromebook and remember what I learn.",
            bg=theme.BG, fg=theme.MUTED, font=self.f_body,
        ).pack(pady=(10, 22))

        chips = tk.Frame(inner, bg=theme.BG)
        chips.pack()
        for text in SUGGESTIONS:
            theme.PillButton(
                chips, text, lambda t=text: self._use_suggestion(t),
                font=self.f_row, bg=theme.SURFACE, fg=theme.MUTED,
                hover=theme.RAISED, padx=18, pady=10,
            ).pack(side="left", padx=5)

    def _use_suggestion(self, text):
        self.entry_var.set(text)
        self.entry.focus_set()
        self.entry.icursor("end")

    def _show_welcome(self, show):
        if show:
            self.transcript_wrap.pack_forget()
            self.welcome.pack(fill="both", expand=True)
        else:
            self.welcome.pack_forget()
            self.transcript_wrap.pack(fill="both", expand=True)

    # --- transcript -------------------------------------------------------

    def _build_transcript(self, parent):
        self.transcript_wrap = Transcript(
            parent,
            fonts={"body": self.f_body, "name": self.f_name, "sub": self.f_sub,
                   "mono": self.f_mono, "row": self.f_row},
        )

    def _build_composer(self, parent):
        bar = tk.Frame(parent, bg=theme.BG)
        bar.pack(fill="x", side="bottom", padx=24, pady=(0, 6))

        panel = theme.RoundedPanel(bar, fill=theme.SURFACE_HI, radius=22)
        panel.pack(fill="x", pady=8)
        shell = panel.body

        # The hint is a separate label rather than pre-filled text, so it can
        # never be mistaken for real input and sent as a message.
        self.entry_var = tk.StringVar()
        self.entry = tk.Entry(
            shell, textvariable=self.entry_var, bg=theme.SURFACE_HI,
            fg=theme.TEXT, insertbackground=theme.ACCENT, relief="flat",
            font=self.f_body, highlightthickness=0, bd=0,
        )
        self.entry.pack(side="left", fill="x", expand=True, padx=16, pady=14)
        self.entry.bind("<Return>", lambda _e: self._on_send())

        self.hint = tk.Label(shell, text=self.PLACEHOLDER, bg=theme.SURFACE_HI,
                             fg=theme.DIM, font=self.f_body)
        self.hint.bind("<Button-1>", lambda _e: self.entry.focus_set())
        self.entry_var.trace_add("write", lambda *_: self._sync_hint())
        self._sync_hint()

        self.send_btn = theme.PillButton(
            shell, "Send", self._on_send, font=self.f_name, bg=theme.ACCENT,
            fg=theme.ON_ACCENT, hover=theme.VIOLET, padx=22, pady=9,
            surface=theme.SURFACE_HI,
        )
        self.send_btn.pack(side="right", padx=(6, 8), pady=7)

    def _build_statusbar(self, parent):
        bar = tk.Frame(parent, bg=theme.BG, height=24)
        bar.pack(fill="x", side="bottom")
        self.status = tk.Label(bar, text="Ready", bg=theme.BG, fg=theme.DIM,
                               font=self.f_sub, anchor="w")
        self.status.pack(side="left", padx=28, pady=(0, 8))

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
            bg = theme.SIDEBAR_ACTIVE if selected else theme.SIDEBAR
            row = tk.Frame(self.chat_list, bg=bg)
            row.pack(fill="x", pady=1, padx=4)

            # A visible delete control: right-click menus aren't discoverable,
            # and on a Chromebook trackpad they need a two-finger tap.
            close = tk.Label(row, text="✕", bg=bg, fg=theme.DIM, font=self.f_sub,
                             cursor="hand2", padx=9)
            close.pack(side="right", fill="y")
            close.bind("<Button-1>", lambda _e, c=chat: self._on_delete(c))
            close.bind("<Enter>", lambda e: e.widget.configure(fg=theme.DANGER))
            close.bind("<Leave>", lambda e: e.widget.configure(fg=theme.DIM))

            body = tk.Frame(row, bg=bg)
            body.pack(side="left", fill="x", expand=True)
            title = tk.Label(body, text=self._elide(chat.title, 178), bg=bg,
                             fg=theme.TEXT if selected else theme.MUTED,
                             font=self.f_row, anchor="w", justify="left")
            title.pack(fill="x", padx=10, pady=(8, 0))
            when = tk.Label(body, text=relative_time(chat.updated_at), bg=bg,
                            fg=theme.DIM, font=self.f_sub, anchor="w")
            when.pack(fill="x", padx=10, pady=(0, 8))

            clickable = (row, body, title, when)
            for widget in clickable:
                widget.bind("<Button-1>", lambda _e, i=chat.id: self._on_open_chat(i))
                widget.configure(cursor="hand2")
            for widget in clickable + (close,):
                widget.bind("<Button-3>", lambda e, c=chat: self._chat_menu(e, c))

            if not selected:
                tinted = clickable + (close,)
                for widget in tinted:
                    widget.bind("<Enter>", lambda _e, w=tinted: self._hover(w, True))
                    widget.bind("<Leave>", lambda _e, w=tinted: self._hover(w, False))
            self._chat_rows[chat.id] = {"row": row, "widgets": clickable}

    def _elide(self, text, max_px):
        """Trim to fit the sidebar, ending in an ellipsis rather than a hard cut."""
        if self.f_row.measure(text) <= max_px:
            return text
        trimmed = text
        while trimmed and self.f_row.measure(trimmed + "…") > max_px:
            trimmed = trimmed[:-1]
        return (trimmed.rstrip() + "…") if trimmed else text[:1]

    def _hover(self, widgets, entering):
        colour = theme.SIDEBAR_HOVER if entering else theme.SIDEBAR
        for widget in widgets:
            try:
                widget.configure(bg=colour)
            except tk.TclError:
                pass  # row was rebuilt while the pointer was over it

    def _chat_menu(self, event, chat):
        menu = tk.Menu(self.root, tearoff=0, bg=theme.SURFACE, fg=theme.TEXT,
                       activebackground=theme.RAISED, activeforeground=theme.TEXT,
                       bd=0, font=self.f_row)
        menu.add_command(label="Rename…", command=lambda: self._on_rename(chat))
        menu.add_command(label="Delete", command=lambda: self._on_delete(chat))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # --- actions ---------------------------------------------------------

    def _set_busy(self, busy, label="Thinking"):
        self.busy = busy
        self.send_btn.set_enabled(not busy, disabled_bg=theme.RAISED,
                                  disabled_fg=theme.DIM)
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

    def _reveal(self):
        """Swap the welcome view out the first time anything is said."""
        if not self._has_messages:
            self._has_messages = True
            self._show_welcome(False)

    def _say(self, role, text, researched=""):
        # Paragraph breaks are kept: a bubble sized to its own text renders a
        # blank line as ordinary spacing, unlike the old full-width bands.
        text = str(text).strip()
        self._reveal()
        self.transcript_wrap.add_message(
            role, text, name="You" if role == "user" else config.ASSISTANT_NAME,
            researched=researched,
        )

    def _notice(self, text):
        self._reveal()
        self.transcript_wrap.add_notice(text)

    def _error(self, text):
        self._reveal()
        self.transcript_wrap.add_error(text)

    def _clear_transcript(self):
        self.transcript_wrap.clear()
        self._has_messages = False

    def _on_send(self):
        if self.busy or self.conversation_id is None:
            return
        message = self.entry_var.get().strip()
        if not message:
            return
        self.entry_var.set("")
        self._say("user", message)
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
        self.requests.put(Request(kind="open_chat", conversation_id=conversation_id))

    def _on_delete(self, chat):
        if self.busy:
            return
        if dialogs.confirm(
            self.root, "Delete chat",
            f"Delete “{chat.title}”?\n\nNotes Cortana learned stay in her "
            "memory — only this conversation is removed.",
        ):
            self.requests.put(Request(kind="delete_chat", conversation_id=chat.id))

    def _on_rename(self, chat):
        new_title = dialogs.ask_text(
            self.root, "Rename chat", "Name this conversation",
            initial=chat.title, ok_label="Rename",
        )
        if new_title:
            self.requests.put(Request(kind="rename_chat", payload=new_title,
                                      conversation_id=chat.id))

    def _on_learn(self):
        if self.busy:
            return
        topic = dialogs.ask_text(
            self.root, "Research", "What should I look up?",
            ok_label="Research",
            placeholder="I'll search the web and keep notes on what I find.",
        )
        if not topic:
            return
        self._notice(f"Researching “{topic}” …")
        self._set_busy(True, "Researching")
        self.requests.put(Request(kind="learn", payload=topic))

    def _on_curious(self):
        topic = dialogs.ask_text(
            self.root, "Queue for later", "What should I look into later?",
            ok_label="Queue",
            placeholder="Researched quietly in the background, on a timer.",
        )
        if topic:
            self.requests.put(Request(kind="curious", payload=topic))

    def _on_memory(self):
        notes = "note" if self._note_count == 1 else "notes"
        dialogs.show_info(
            self.root, "Memory",
            [("Learned", f"{self._note_count} {notes}"),
             ("Queued", f"{self._pending} topic(s)"),
             ("Chat model", config.CHAT_MODEL),
             ("Embeddings", config.EMBED_MODEL)],
            footer=f"Stored at {config.MEMORY_DB_PATH}\n\n"
                   "Memory is shared across every chat, so deleting a "
                   "conversation never makes her forget a fact.",
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
                     f"{response.pending} queued")
            return

        if response.kind == "opened":
            self.conversation_id = response.conversation_id
            self._clear_transcript()
            if response.messages:
                for message in response.messages:
                    self._say(message.role, message.content,
                              researched=message.researched)
            else:
                self._show_welcome(True)
            self._render_chat_list(response.chats)
            self._update_title(response.chats)
            self._set_busy(False)
            return

        if response.kind == "chats":
            self._render_chat_list(response.chats)
            self._update_title(response.chats)
            return

        if response.kind == "chat":
            self._reveal()
            if response.researched:
                self.transcript_wrap.add_research(
                    response.researched, response.notes_added)
            self._say("assistant", response.text)
            self._render_chat_list(response.chats)
            self._update_title(response.chats)

        elif response.kind == "notice":
            self._notice(response.text)

        elif response.kind in ("error", "fatal"):
            self._error(response.error if response.kind == "error"
                        else f"Couldn't start: {response.error}")

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
        # className sets WM_CLASS, which is how ChromeOS ties this window to
        # its launcher entry and shows the right icon in the shelf.
        root = tk.Tk(className=WM_CLASS)
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

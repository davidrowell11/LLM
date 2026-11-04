"""Tests for shared widget behaviour.

Needs both Tk and a display, so it skips on a headless machine rather than
failing there.
"""

import pytest

pytest.importorskip("tkinter", reason="python3-tk not installed")

import tkinter as tk  # noqa: E402

from llm_agent import theme  # noqa: E402


@pytest.fixture
def root():
    try:
        window = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available")
    # Deliberately not withdrawn: Tk won't deliver <Enter>/<Leave> to an
    # unmapped window, which would make every assertion below pass whether
    # or not the behaviour is right.
    window.update()
    yield window
    window.destroy()


def make(root, **kwargs):
    button = theme.flat_button(
        root, "Send", lambda: None, font=theme.pick(root, 10),
        bg=theme.ACCENT, hover=theme.VIOLET, **kwargs
    )
    button.pack()
    root.update()
    return button


def test_hover_lightens_an_enabled_button(root):
    button = make(root)
    button.event_generate("<Enter>")
    root.update()

    assert button.cget("bg") == theme.VIOLET


def test_leaving_restores_an_enabled_button(root):
    button = make(root)
    button.event_generate("<Enter>")
    button.event_generate("<Leave>")
    root.update()

    assert button.cget("bg") == theme.ACCENT


def test_disabled_button_ignores_hover(root):
    """Regression: hovering a disabled Send button repainted it."""
    button = make(root)
    button.configure(state="disabled", bg=theme.SURFACE_HI)

    button.event_generate("<Enter>")
    root.update()

    assert button.cget("bg") == theme.SURFACE_HI


def test_disabled_button_is_not_repainted_on_leave(root):
    """The worse half: leaving restored the *enabled* colour, so a button
    that was still busy looked clickable again."""
    button = make(root)
    button.configure(state="disabled", bg=theme.SURFACE_HI)

    button.event_generate("<Enter>")
    button.event_generate("<Leave>")
    root.update()

    assert button.cget("bg") == theme.SURFACE_HI
    assert button.cget("bg") != theme.ACCENT


def test_pick_font_falls_back_when_family_missing(root):
    font = theme.pick(root, 11)
    assert font.actual("size") == theme.font_size(11)


# --- macOS --------------------------------------------------------------
# These force the Mac code paths on any OS: what they check is the widget
# logic, which is the same Tk everywhere; only native Button drawing differs.


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr(theme, "IS_MAC", True)


def test_mac_button_is_a_label_so_its_colours_apply(root, mac):
    """A native Aqua tk.Button ignores bg, which made light text unreadable."""
    button = make(root)
    assert isinstance(button, tk.Label)
    assert button.cget("bg") == theme.ACCENT


def test_mac_button_hover_and_leave(root, mac):
    button = make(root)
    button.event_generate("<Enter>")
    root.update()
    assert button.cget("bg") == theme.VIOLET
    button.event_generate("<Leave>")
    root.update()
    assert button.cget("bg") == theme.ACCENT


def test_mac_button_click_runs_command(root, mac):
    clicks = []
    button = theme.flat_button(root, "Go", lambda: clicks.append(1),
                               font=theme.pick(root, 10))
    button.pack()
    root.update()
    button.event_generate("<ButtonRelease-1>")
    root.update()
    assert clicks == [1]


def test_mac_disabled_button_ignores_hover_and_clicks(root, mac):
    clicks = []
    button = theme.flat_button(root, "Go", lambda: clicks.append(1),
                               font=theme.pick(root, 10))
    button.pack()
    button.configure(state="disabled", bg=theme.SURFACE_HI)
    root.update()
    button.event_generate("<Enter>")
    button.event_generate("<ButtonRelease-1>")
    button.event_generate("<Leave>")
    root.update()
    assert clicks == []
    assert button.cget("bg") == theme.SURFACE_HI


def test_mac_context_click_uses_button_2_and_control_click(root, mac):
    widget = tk.Frame(root)
    theme.bind_context_click(widget, lambda e: None)
    assert "<Button-2>" in widget.bind()
    assert "<Control-Button-1>" in widget.bind()
    assert "<Button-3>" not in widget.bind()


def test_context_click_elsewhere_is_button_3(root, monkeypatch):
    monkeypatch.setattr(theme, "IS_MAC", False)
    widget = tk.Frame(root)
    theme.bind_context_click(widget, lambda e: None)
    assert widget.bind() == ("<Button-3>",)


def test_mac_fonts_are_scaled_up(monkeypatch):
    monkeypatch.setattr(theme, "IS_MAC", True)
    assert theme.font_size(12) == 16
    monkeypatch.setattr(theme, "IS_MAC", False)
    assert theme.font_size(12) == 12

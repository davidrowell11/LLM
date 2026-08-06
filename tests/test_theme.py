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
    assert font.actual("size") == 11

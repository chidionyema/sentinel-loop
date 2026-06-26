"""WI-1 + WI-2 — Persistent nav bar + Home button on every screen.

WI-1: _reply_keyboard_markup() must exist and return a valid Telegram
ReplyKeyboardMarkup with persistent nav buttons.

WI-2: Every screen (slash command + callback) must include a Home button
in its inline keyboard — no dead-end screens.
"""

import pytest


# ═══════════════════════════════════════════════════════════════════════
#  WI-1 — Reply keyboard
# ═══════════════════════════════════════════════════════════════════════

def test_reply_keyboard_markup_defined_and_valid():
    """_reply_keyboard_markup() must exist and return proper Telegram structure."""
    from sentinel.cockpit.menu import _reply_keyboard_markup

    kb = _reply_keyboard_markup()
    assert isinstance(kb, dict), f"Expected dict, got {type(kb)}"
    assert "keyboard" in kb, f"No 'keyboard' key in: {kb}"
    assert kb.get("resize_keyboard") is True, "resize_keyboard must be True"
    assert kb.get("is_persistent") is True, "is_persistent must be True"

    # Flatten all button texts
    all_buttons = []
    for row in kb["keyboard"]:
        for btn in row:
            all_buttons.append(btn if isinstance(btn, str) else btn.get("text", ""))

    # Must contain core nav buttons
    assert any("Home" in b for b in all_buttons), f"Missing Home button in: {all_buttons}"
    assert any("Estate" in b for b in all_buttons), f"Missing Estate button in: {all_buttons}"
    assert any("Tasks" in b for b in all_buttons), f"Missing Tasks button in: {all_buttons}"
    assert any("Project" in b or "🛰" in b for b in all_buttons), f"Missing Projects button in: {all_buttons}"


def test_nav_button_labels_have_handlers():
    """Each nav button label must map to a known handler (no dead taps)."""
    from sentinel.cockpit.menu import _reply_keyboard_markup, _NAV_BUTTON_MAP

    kb = _reply_keyboard_markup()
    all_labels = []
    for row in kb["keyboard"]:
        for btn in row:
            all_labels.append(btn if isinstance(btn, str) else btn.get("text", ""))

    for label in all_labels:
        assert label in _NAV_BUTTON_MAP, (
            f"Nav button '{label}' has no handler in _NAV_BUTTON_MAP"
        )


# ═══════════════════════════════════════════════════════════════════════
#  WI-2 — No dead-end screens
# ═══════════════════════════════════════════════════════════════════════

# List of (builder_name, builder_callable) that represent every screen
# reachable via slash command or callback. Each must return a keyboard.
SCREENS = []


def _collect_screens():
    """Build the list of screen builders lazily."""
    global SCREENS
    if SCREENS:
        return SCREENS

    from sentinel.cockpit.menu import (
        view_dashboard, view_daemon, view_killed, view_investigate,
        view_search, view_heartbeat, view_schedule, view_alerts, view_log,
        view_projects,
    )

    SCREENS = [
        ("view_dashboard", lambda: view_dashboard()),
        ("view_daemon", lambda: view_daemon()),
        ("view_killed", lambda: view_killed()),
        ("view_investigate", lambda: view_investigate()),
        ("view_search", lambda: view_search()),
        ("view_heartbeat", lambda: view_heartbeat()),
        ("view_schedule", lambda: view_schedule()),
        ("view_alerts", lambda: view_alerts()),
        ("view_log_0", lambda: view_log(page=0)),
        ("view_projects", lambda: view_projects()),
    ]
    return SCREENS


@pytest.mark.parametrize("name,builder", _collect_screens())
def test_screen_has_home_button(name, builder):
    """Every screen must return a keyboard with a Home action."""
    result = builder()
    if isinstance(result, tuple):
        text, kb = result
    else:
        text, kb = result, {}

    assert kb, f"{name}: returned no keyboard (dead end)"
    assert "inline_keyboard" in kb, f"{name}: missing inline_keyboard"

    # Find any Home button in the keyboard
    found_home = False
    for row in kb["inline_keyboard"]:
        for btn in row:
            cb = btn.get("callback_data", "")
            txt = btn.get("text", "")
            if "Home" in txt or "🏠" in txt or cb == "nv:dash:" or cb == "nv:back":
                found_home = True
                break
        if found_home:
            break

    assert found_home, (
        f"{name}: no Home button found. keyboard={kb}"
    )

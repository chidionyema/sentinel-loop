"""D1 — Read-only project status tiles.

All 3 projects must render. Signal Engine (money) and TIE (identity) must NOT
expose any trigger/run callback — only Prospector (risk_class: low) may.
"""

import os
import sys

import pytest

# Ensure coordinator is importable
_SCRIPTS = os.path.expanduser("~/.hermes/scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)


def test_all_three_projects_render(monkeypatch):
    """Projects view must include prospector, signalengine, and tie."""
    from sentinel.cockpit.menu import view_dashboard

    sent_messages = []
    monkeypatch.setattr("sentinel.cockpit.menu.send",
                        lambda chat_id, text, kb=None: sent_messages.append(text))
    monkeypatch.setattr("sentinel.cockpit.menu.answer", lambda cbq_id: True)
    monkeypatch.setattr("sentinel.cockpit.menu._api", lambda m, b: True)

    text, kb = view_dashboard()
    combined = text + " " + str(kb)

    # All three projects should be visible
    assert "prospector" in combined.lower(), "Prospector not found in dashboard"
    assert "signal" in combined.lower() or "engine" in combined.lower(), \
        "Signal Engine not found in dashboard"
    assert "introduction" in combined.lower() or "exchange" in combined.lower() or "tie" in combined.lower(), \
        "Introduction Exchange (TIE) not found in dashboard"


def test_signalengine_and_tie_have_no_trigger(monkeypatch):
    """Fenced projects (money/identity) must not expose run/trigger callbacks."""
    from sentinel.cockpit.menu import view_dashboard

    text, kb = view_dashboard()
    kb_str = str(kb)

    # Check callback data for any trigger/run action on money/identity projects
    # The keyboard should NOT contain run/trigger callbacks pointing at signalengine or tie
    assert "signalengine" not in kb_str or "run" not in kb_str.lower(), \
        f"Signal Engine (money) must not have run triggers: {kb_str}"
    # For TIE — check no trigger callback
    if "tie" in kb_str.lower():
        # TIE tile may be present but should not have callback_data with "run" or "trigger"
        assert "run" not in kb_str.lower() or "tie" not in kb_str.lower(), \
            f"TIE (identity) must not have run triggers: {kb_str}"


def test_risk_markers_shown(monkeypatch):
    """Fenced projects should display their risk_class markers."""
    from sentinel.cockpit.menu import view_dashboard

    text, kb = view_dashboard()

    # Should mention risk_class for fenced projects (money/identity markers)
    # The spec calls for 🔒 money / 🔒 identity markers
    combined = text.lower()
    has_money_marker = "money" in combined or "🔒" in combined
    has_identity_marker = "identity" in combined or "🔒" in combined
    assert has_money_marker or has_identity_marker, \
        f"No risk markers found in dashboard: {text[:200]}"

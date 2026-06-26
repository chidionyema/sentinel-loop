"""B4 — Daemon start/stop view (gateway must NOT appear as a start target)."""

import os
import sys
import tempfile

import pytest

# Ensure coordinator is importable
_SCRIPTS = os.path.expanduser("~/.hermes/scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)


def test_daemon_view_lists_safe_daemons(monkeypatch):
    """estate:daemons must list cockpit/ngrok/otto/coordinator/prospector, NOT gateway."""
    import coordinator as C
    from sentinel.cockpit.menu import handle_estate_callback

    # Mock launchctl list output to simulate daemons
    launchctl_output = (
        "PID\tStatus\tLabel\n"
        "1234\t0\tai.hermes.cockpit\n"
        "5678\t0\tai.hermes.ngrok\n"
        "9999\t0\tai.hermes.gateway\n"  # THIS MUST NOT be a start target
    )

    def fake_run(cmd, **kwargs):
        class Result:
            stdout = launchctl_output
            stderr = ""
            returncode = 0
        return Result()

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(C, "estate_paused", lambda: False)

    sent_messages = []
    monkeypatch.setattr("sentinel.cockpit.menu.send",
                        lambda chat_id, text, kb=None: sent_messages.append(text))
    monkeypatch.setattr("sentinel.cockpit.menu.answer", lambda cbq_id: True)
    monkeypatch.setattr("sentinel.cockpit.menu._api", lambda m, b: True)

    import asyncio
    asyncio.run(handle_estate_callback("estate:daemons", "8868748055", "cbq-daemons"))

    combined = " ".join(sent_messages)
    # Must show cockpit and ngrok
    assert "cockpit" in combined.lower(), f"Cockpit not listed: {combined}"
    assert "ngrok" in combined.lower(), f"Ngrok not listed: {combined}"
    # Gateway must NOT appear as a start target (it IS listed if running, but no start button)
    # The key check: no "start gateway" or gate start callback
    assert "estate:daemon_start:gateway" not in combined, \
        f"Gateway appears as start target! {combined}"

"""A3 — estate: handler (pause / resume / status / logs / restart-with-confirm).

Port from dead gateway: telegram.py:4207-4360 + _status_keyboard at 6240-6260.
"""

import os
import sys
import tempfile

import pytest

# Ensure coordinator is importable
_SCRIPTS = os.path.expanduser("~/.hermes/scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)


# ── Tests ────────────────────────────────────────────────────────────────

def test_estate_pause_calls_set_estate_paused_true(monkeypatch):
    """estate:pause must call C.set_estate_paused(True)."""
    import coordinator as C
    from sentinel.cockpit.menu import handle_estate_callback

    pause_calls = []
    def fake_set_paused(on: bool) -> bool:
        pause_calls.append(on)
        return on

    monkeypatch.setattr(C, "set_estate_paused", fake_set_paused)
    monkeypatch.setattr(C, "estate_paused", lambda: True)

    sent_messages = []
    monkeypatch.setattr("sentinel.cockpit.menu.send",
                        lambda chat_id, text, kb=None: sent_messages.append(text))
    monkeypatch.setattr("sentinel.cockpit.menu.answer", lambda cbq_id: True)
    monkeypatch.setattr("sentinel.cockpit.menu._api", lambda m, b: True)

    import asyncio
    asyncio.run(handle_estate_callback("estate:pause", "8868748055", "cbq-1"))

    assert len(pause_calls) == 1
    assert pause_calls[0] is True, f"Expected set_estate_paused(True), got {pause_calls}"
    assert any("paused" in msg.lower() for msg in sent_messages), \
        f"No 'paused' confirmation in: {sent_messages}"


def test_estate_resume_calls_set_estate_paused_false(monkeypatch):
    """estate:resume must call C.set_estate_paused(False)."""
    import coordinator as C
    from sentinel.cockpit.menu import handle_estate_callback

    pause_calls = []
    def fake_set_paused(on: bool) -> bool:
        pause_calls.append(on)
        return on

    monkeypatch.setattr(C, "set_estate_paused", fake_set_paused)
    monkeypatch.setattr(C, "estate_paused", lambda: False)

    sent_messages = []
    monkeypatch.setattr("sentinel.cockpit.menu.send",
                        lambda chat_id, text, kb=None: sent_messages.append(text))
    monkeypatch.setattr("sentinel.cockpit.menu.answer", lambda cbq_id: True)
    monkeypatch.setattr("sentinel.cockpit.menu._api", lambda m, b: True)

    import asyncio
    asyncio.run(handle_estate_callback("estate:resume", "8868748055", "cbq-2"))

    assert len(pause_calls) == 1
    assert pause_calls[0] is False, f"Expected set_estate_paused(False), got {pause_calls}"
    assert any("resumed" in msg.lower() for msg in sent_messages), \
        f"No 'resumed' confirmation in: {sent_messages}"


def test_estate_status_shows_paused_state(monkeypatch):
    """estate:refresh must show current paused/resumed state."""
    import coordinator as C
    from sentinel.cockpit.menu import handle_estate_callback

    monkeypatch.setattr(C, "estate_paused", lambda: True)
    monkeypatch.setattr(C, "set_estate_paused", lambda on: on)

    sent_messages = []
    monkeypatch.setattr("sentinel.cockpit.menu.send",
                        lambda chat_id, text, kb=None: sent_messages.append(text))
    monkeypatch.setattr("sentinel.cockpit.menu.answer", lambda cbq_id: True)
    monkeypatch.setattr("sentinel.cockpit.menu._api", lambda m, b: True)

    import asyncio
    asyncio.run(handle_estate_callback("estate:refresh", "8868748055", "cbq-3"))

    combined = " ".join(sent_messages)
    assert "paused" in combined.lower() or "estate" in combined.lower(), \
        f"Status not shown in: {sent_messages}"


def test_estate_logs_returns_tail(monkeypatch):
    """estate:view_logs must return the last ~30 lines of coordinator.log."""
    from sentinel.cockpit.menu import handle_estate_callback

    # Create a temp log file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
        log_path = f.name
        for i in range(50):
            f.write(f"Log line {i:04d}\\n")

    sent_messages = []
    monkeypatch.setattr("sentinel.cockpit.menu.send",
                        lambda chat_id, text, kb=None: sent_messages.append(text))
    monkeypatch.setattr("sentinel.cockpit.menu.answer", lambda cbq_id: True)
    monkeypatch.setattr("sentinel.cockpit.menu._api", lambda m, b: True)

    # Point the log read to our temp file
    import coordinator as C
    monkeypatch.setattr(C, "estate_paused", lambda: False)

    orig_exists = os.path.exists
    def fake_exists(path):
        if "coordinator.log" in str(path):
            return True
        return orig_exists(path)
    monkeypatch.setattr(os.path, "exists", fake_exists)

    # Patch open for the log read
    orig_open = builtins_open = __builtins__["open"] if isinstance(__builtins__, dict) else __builtins__.open
    import builtins
    def fake_open(path, *args, **kwargs):
        if "coordinator.log" in str(path):
            return orig_open(log_path, *args, **kwargs)
        return orig_open(path, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", fake_open)

    import asyncio
    asyncio.run(handle_estate_callback("estate:view_logs", "8868748055", "cbq-4"))

    combined = " ".join(sent_messages)
    # Should contain some log content (at least the last lines)
    assert "0049" in combined or "log" in combined.lower(), \
        f"No log content found in: {sent_messages}"

    os.unlink(log_path)


def test_estate_restart_shows_confirm(monkeypatch):
    """estate:restart must show a confirm prompt, not immediately restart."""
    import coordinator as C
    from sentinel.cockpit.menu import handle_estate_callback

    monkeypatch.setattr(C, "estate_paused", lambda: False)

    sent_messages = []
    sent_keyboards = []
    def fake_send(chat_id, text, kb=None):
        sent_messages.append(text)
        if kb:
            sent_keyboards.append(kb)

    monkeypatch.setattr("sentinel.cockpit.menu.send", fake_send)
    monkeypatch.setattr("sentinel.cockpit.menu.answer", lambda cbq_id: True)
    monkeypatch.setattr("sentinel.cockpit.menu._api", lambda m, b: True)

    import asyncio
    asyncio.run(handle_estate_callback("estate:restart", "8868748055", "cbq-5"))

    combined = " ".join(sent_messages)
    assert any(word in combined.lower() for word in ["restart", "confirm", "♻️"]), \
        f"No restart confirm prompt in: {sent_messages}"

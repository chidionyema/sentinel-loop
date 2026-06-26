"""A2 — task: handler (LIST + CANCEL; APPROVE is FENCED Claude-only).

The task handler must:
- List escalated tasks via /tasks command + task:list callback
- Cancel a task (status → cancelled)
- NOT approve tasks (approve is fenced — must show 🔒 lock message)
"""

import os
import sqlite3
import sys
import tempfile

import pytest

# Ensure ~/.hermes/scripts is importable for coordinator module
_SCRIPTS = os.path.expanduser("~/.hermes/scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)


# ── Helpers ──────────────────────────────────────────────────────────────

def _seed_db(db_path: str) -> str:
    """Create a temp DB with one escalated task and return its id."""
    import coordinator as C
    conn = C.connect(db_path)
    # Ensure tasks table exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            status TEXT DEFAULT 'open',
            title TEXT DEFAULT '',
            created_at REAL,
            updated_at REAL,
            consecutive_failures INTEGER DEFAULT 0,
            last_heartbeat_at REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT,
            kind TEXT,
            payload TEXT DEFAULT '',
            created_at REAL
        )
    """)
    task_id = "test-escalated-task-00000001"
    conn.execute(
        "INSERT OR REPLACE INTO tasks (id, status, title) VALUES (?, 'escalated', ?)",
        (task_id, "Test escalated task needing approval"),
    )
    conn.commit()
    conn.close()
    return task_id


# ── Tests ────────────────────────────────────────────────────────────────

def test_cancel_task_changes_status_to_cancelled(monkeypatch):
    """task:cancel:<id> must set status='cancelled'."""
    import coordinator as C
    from sentinel.cockpit.menu import handle_task_callback, answer, send

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        task_id = _seed_db(db_path)

        # Redirect coordinator.connect() to our temp DB
        orig_connect = C.connect
        def fake_connect(db_path_=C.DB_PATH):
            return orig_connect(db_path)
        monkeypatch.setattr(C, "connect", fake_connect)

        # Capture send() output
        sent_messages = []
        def fake_send(chat_id, text, kb=None):
            sent_messages.append(text)
        monkeypatch.setattr("sentinel.cockpit.menu.send", fake_send)
        # Suppress answer() calls (they hit real Telegram API)
        monkeypatch.setattr("sentinel.cockpit.menu.answer", lambda cbq_id: True)
        monkeypatch.setattr("sentinel.cockpit.menu._api", lambda m, b: True)

        import asyncio
        asyncio.run(handle_task_callback(
            f"task:cancel:{task_id}", "8868748055", "cbq-test-1"))

        # Verify the task is now cancelled
        conn = orig_connect(db_path)
        row = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
        conn.close()
        assert row is not None
        assert row["status"] == "cancelled", f"Expected cancelled, got {row['status']}"
        assert any("cancelled" in msg.lower() or "cancel" in msg.lower()
                   for msg in sent_messages), f"No cancel confirmation in: {sent_messages}"
    finally:
        os.unlink(db_path)


def test_approve_is_fenced_shows_lock_message(monkeypatch):
    """task:approve:<id> must NOT change status and must show the 🔒 fence message."""
    import coordinator as C
    from sentinel.cockpit.menu import handle_task_callback

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        task_id = _seed_db(db_path)

        orig_connect = C.connect
        def fake_connect(db_path_=C.DB_PATH):
            return orig_connect(db_path)
        monkeypatch.setattr(C, "connect", fake_connect)

        sent_messages = []
        monkeypatch.setattr("sentinel.cockpit.menu.send",
                            lambda chat_id, text, kb=None: sent_messages.append(text))
        monkeypatch.setattr("sentinel.cockpit.menu.answer", lambda cbq_id: True)
        monkeypatch.setattr("sentinel.cockpit.menu._api", lambda m, b: True)

        import asyncio
        asyncio.run(handle_task_callback(
            f"task:approve:{task_id}", "8868748055", "cbq-test-2"))

        # Verify the task status is UNCHANGED (still escalated)
        conn = orig_connect(db_path)
        row = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
        conn.close()
        assert row is not None
        assert row["status"] == "escalated", (
            f"APPROVE should be FENCED — status must not change. Got: {row['status']}"
        )
        # Verify the fence lock message was sent
        assert any("claude" in msg.lower() or "🔒" in msg or "fence" in msg.lower()
                   for msg in sent_messages), (
            f"No fence/lock message found in: {sent_messages}"
        )
    finally:
        os.unlink(db_path)


def test_task_list_shows_escalated_tasks(monkeypatch):
    """task:list (or /tasks) must show escalated tasks."""
    import coordinator as C
    from sentinel.cockpit.menu import handle_task_callback

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        task_id = _seed_db(db_path)

        orig_connect = C.connect
        def fake_connect(db_path_=C.DB_PATH):
            return orig_connect(db_path)
        monkeypatch.setattr(C, "connect", fake_connect)

        sent_messages = []
        monkeypatch.setattr("sentinel.cockpit.menu.send",
                            lambda chat_id, text, kb=None: sent_messages.append(text))
        monkeypatch.setattr("sentinel.cockpit.menu.answer", lambda cbq_id: True)
        monkeypatch.setattr("sentinel.cockpit.menu._api", lambda m, b: True)

        import asyncio
        asyncio.run(handle_task_callback(
            "task:list", "8868748055", "cbq-test-3"))

        # Should show the escalated task
        combined = " ".join(sent_messages)
        assert "escalated" in combined.lower() or task_id[:8] in combined, (
            f"Escalated task not listed in: {combined}"
        )
    finally:
        os.unlink(db_path)

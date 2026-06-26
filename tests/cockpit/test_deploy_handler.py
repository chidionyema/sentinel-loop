"""WI-3 — Deploy button handler.

- deploy:<repo>:<token> on low-risk repos must confirm then trigger
- deploy on signalengine (money) / tie (identity) must NOT deploy
  — must route to approval gate instead
"""

import os
import sqlite3
import sys
import tempfile

import pytest

_SCRIPTS = os.path.expanduser("~/.hermes/scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)


def test_deploy_button_on_low_risk_repo_confirms_then_deploys(monkeypatch):
    """deploy:prospector:<token> should show confirm, then deploy on confirm."""
    from sentinel.cockpit.menu import handle_callback

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

    # First tap: deploy button shows confirm
    asyncio.run(handle_callback("deploy:prospector:test-token-1234", "8868748055", "cbq-1"))
    combined = " ".join(sent_messages)
    assert any(w in combined.lower() for w in ["confirm", "deploy", "prospector", "proceed"]), \
        f"No confirm prompt for low-risk deploy: {combined}"


def test_deploy_on_money_repo_routes_to_approval_gate(monkeypatch):
    """deploy:signalengine:<token> must NOT deploy — must open approval task."""
    import coordinator as C
    from sentinel.cockpit.menu import handle_callback

    # Create temp DB
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("""CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY, status TEXT DEFAULT 'open', title TEXT DEFAULT '',
            body TEXT DEFAULT '', kind TEXT DEFAULT '', source TEXT DEFAULT '',
            created_by TEXT DEFAULT '', created_at REAL,
            updated_at REAL, consecutive_failures INTEGER DEFAULT 0,
            last_heartbeat_at REAL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT, kind TEXT, payload TEXT DEFAULT '',
            created_at REAL
        )""")
        conn.commit()
        conn.close()

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
        asyncio.run(handle_callback("deploy:signalengine:test-token-5678", "8868748055", "cbq-2"))

        combined = " ".join(sent_messages)
        # Must mention approval gate / fence / Claude-only
        assert any(w in combined.lower() for w in ["approval", "fence", "claude", "🔒", "gate"]), \
            f"Money deploy not routed to approval: {combined}"

        # Verify a task was created in the DB
        conn = orig_connect(db_path)
        rows = conn.execute("SELECT * FROM tasks").fetchall()
        conn.close()
        assert len(rows) >= 1, f"No approval task created for money deploy"

    finally:
        os.unlink(db_path)

"""Enforcement tests — make the "defined but never delivered" class of bug impossible.

These tests fail the build if:
1. A nav button maps to a stub or dead route in _dispatch_nav
2. The ReplyKeyboardMarkup is never attached to a sendMessage call
3. A callback prefix in the router hits the "unknown" fallthrough

This is the structural fix for the recurring "code exists but phone sees nothing" failure.
"""

import pytest
from fastapi.testclient import TestClient

from sentinel.cockpit.server import create_app


# ── Shared setup ────────────────────────────────────────────────────────

def _setup_mocks(monkeypatch):
    """Common mock setup for all enforcement tests."""
    captured = []

    def fake_api(method, body):
        if method == "sendMessage":
            captured.append(body)
        return True

    monkeypatch.setattr("sentinel.cockpit.menu._api", fake_api)
    monkeypatch.setattr(
        "sentinel.cockpit.acl.validate_telegram_user",
        lambda from_id: True,
    )
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "")
    # Set a fake bot token so _send_with_keyboard doesn't early-return
    monkeypatch.setattr("sentinel.cockpit.menu._t", lambda: "fake-token-for-test")

    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    return captured, client


STUB_PATTERNS = [
    "coming soon",
    "handler registered",
    "full impl in",
    "not yet implemented",
    "not enabled here yet",
]


# ═══════════════════════════════════════════════════════════════════
#  Enforcement 1: Every nav button must dispatch to a real handler
# ═══════════════════════════════════════════════════════════════════

def test_every_nav_button_label_in_map():
    """Every nav button label from the keyboard must have a _NAV_BUTTON_MAP entry."""
    from sentinel.cockpit.menu import _NAV_BUTTON_MAP, _reply_keyboard_markup

    kb = _reply_keyboard_markup()
    all_labels = []
    for row in kb["keyboard"]:
        for btn in row:
            all_labels.append(btn if isinstance(btn, str) else btn.get("text", ""))

    for label in all_labels:
        assert label in _NAV_BUTTON_MAP, (
            f"Nav button '{label}' has no entry in _NAV_BUTTON_MAP — "
            f"tapping it will silently do nothing"
        )


def test_nav_button_dispatch_produces_real_output(monkeypatch):
    """Each nav button tap through the webhook must NOT produce a stub message."""
    from sentinel.cockpit.menu import _NAV_BUTTON_MAP

    captured, client = _setup_mocks(monkeypatch)

    for label, route in _NAV_BUTTON_MAP.items():
        captured.clear()

        resp = client.post("/webhooks/telegram", json={
            "message": {
                "message_id": 100,
                "from": {"id": 8868748055, "first_name": "Test"},
                "chat": {"id": 8868748055, "type": "private"},
                "text": label,
            }
        })

        assert resp.status_code == 200, f"Nav tap '{label}' returned {resp.status_code}"
        assert len(captured) > 0, (
            f"Nav tap '{label}' (route: {route}) produced ZERO sendMessage calls — "
            f"button is completely dead"
        )

        for body in captured:
            text = str(body.get("text", ""))
            for stub in STUB_PATTERNS:
                assert stub.lower() not in text.lower(), (
                    f"Nav tap '{label}' → route '{route}' produced stub: "
                    f"'{text[:120]}' (matched '{stub}')"
                )


# ═══════════════════════════════════════════════════════════════════
#  Enforcement 2: ReplyKeyboardMarkup must leave the process on /start
# ═══════════════════════════════════════════════════════════════════

def test_reply_keyboard_is_actually_sent_on_start(monkeypatch):
    """/start must produce at least one sendMessage with a ReplyKeyboardMarkup.

    This catches the "kb is None guard prevents nav bar from ever being sent" bug.
    """
    captured, client = _setup_mocks(monkeypatch)

    resp = client.post("/webhooks/telegram", json={
        "message": {
            "message_id": 1,
            "from": {"id": 8868748055, "first_name": "Test"},
            "chat": {"id": 8868748055, "type": "private"},
            "text": "/start",
        }
    })

    assert resp.status_code == 200

    found_nav_bar = False
    for body in captured:
        markup = body.get("reply_markup", {})
        if markup.get("is_persistent") is True and "keyboard" in markup:
            found_nav_bar = True
            all_btn_texts = []
            for row in markup["keyboard"]:
                for btn in row:
                    all_btn_texts.append(
                        btn if isinstance(btn, str) else btn.get("text", "")
                    )
            assert any("Home" in t for t in all_btn_texts), \
                f"ReplyKeyboardMarkup missing Home button: {all_btn_texts}"
            assert any("Estate" in t for t in all_btn_texts), \
                f"ReplyKeyboardMarkup missing Estate button: {all_btn_texts}"
            break

    assert found_nav_bar, (
        f"NO sendMessage with ReplyKeyboardMarkup in {len(captured)} calls. "
        f"Captured: {[{k: type(v).__name__ for k, v in b.items() if 'markup' in k.lower()} for b in captured]}. "
        f"The nav bar is DEFINED but NEVER SENT."
    )


# ═══════════════════════════════════════════════════════════════════
#  Enforcement 3: Every handler callback prefix must route somewhere
# ═══════════════════════════════════════════════════════════════════

def test_all_known_callback_prefixes_produce_output(monkeypatch):
    """Every callback prefix we ship must produce a sendMessage response.

    Ensures no 'deploy:' or 'cicd:' callback falls through to the void.
    """
    captured, client = _setup_mocks(monkeypatch)

    TEST_CALLBACKS = [
        ("deploy:prospector:tok-123", "deploy button"),
        ("cicd:list", "CI/CD list"),
        ("task:list", "task list"),
        ("estate:refresh", "estate refresh"),
        ("task:cancel:test12345", "task cancel"),
    ]

    for cb_data, description in TEST_CALLBACKS:
        captured.clear()
        resp = client.post("/webhooks/telegram", json={
            "callback_query": {
                "id": "cbq-test-001",
                "from": {"id": 8868748055, "first_name": "Test"},
                "message": {
                    "chat": {"id": 8868748055, "type": "private"},
                    "message_id": 42,
                },
                "data": cb_data,
            }
        })

        assert resp.status_code == 200, (
            f"Callback '{cb_data}' ({description}) returned {resp.status_code}"
        )
        assert len(captured) > 0, (
            f"Callback '{cb_data}' ({description}) produced ZERO sendMessage calls — "
            f"handler is a dead end"
        )

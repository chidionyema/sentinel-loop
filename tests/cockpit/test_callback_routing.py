"""A1 — Verify that task:, estate:, and update_prompt: callbacks are routed correctly.

Before the fix, the callback router in server.py:538-579 dispatched nv:/ac:/d*
prefixes to menu.handle_callback but silently dropped task:/estate:/update_prompt:
— those buttons had no handler and did nothing on the phone.
"""

import pytest
from fastapi.testclient import TestClient

from sentinel.cockpit.server import create_app


@pytest.fixture
def client(monkeypatch):
    """Create a TestClient with ACL bypassed for the test user."""
    # Bypass secret-token check
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "")
    # Bypass ACL — allow our test user
    monkeypatch.setattr(
        "sentinel.cockpit.acl.validate_telegram_user",
        lambda from_id: True,
    )
    app = create_app()
    return TestClient(app, raise_server_exceptions=False)


# ── Helpers ──────────────────────────────────────────────────────────────

def _fake_update(data: str, from_id: int = 8868748055) -> dict:
    """Build a minimal Telegram webhook JSON payload for a callback_query."""
    return {
        "callback_query": {
            "id": "test-cbq-999",
            "from": {"id": from_id, "first_name": "Test"},
            "message": {"chat": {"id": from_id, "type": "private"}, "message_id": 42},
            "data": data,
        }
    }


# ── Tests ────────────────────────────────────────────────────────────────

def test_estate_callback_routed(client, monkeypatch):
    """estate:refresh must reach the estate handler, not be silently dropped."""
    called_with = {}

    async def fake_handle_estate(data, chat_id, cbq_id):
        called_with["data"] = data
        called_with["chat_id"] = chat_id

    monkeypatch.setattr(
        "sentinel.cockpit.menu.handle_estate_callback",
        fake_handle_estate,
    )

    resp = client.post("/webhooks/telegram", json=_fake_update("estate:refresh"))
    assert resp.status_code == 200
    assert called_with.get("data") == "estate:refresh"
    assert called_with.get("chat_id") == "8868748055"


def test_task_callback_routed(client, monkeypatch):
    """task:cancel:<id> must reach the task handler."""
    called_with = {}

    async def fake_handle_task(data, chat_id, cbq_id):
        called_with["data"] = data
        called_with["chat_id"] = chat_id

    monkeypatch.setattr(
        "sentinel.cockpit.menu.handle_task_callback",
        fake_handle_task,
    )

    resp = client.post("/webhooks/telegram", json=_fake_update("task:cancel:abc12345"))
    assert resp.status_code == 200
    assert called_with.get("data") == "task:cancel:abc12345"


def test_prompt_callback_routed(client, monkeypatch):
    """update_prompt:y must reach the prompt handler."""
    called_with = {}

    async def fake_handle_prompt(data, chat_id, cbq_id):
        called_with["data"] = data
        called_with["chat_id"] = chat_id

    monkeypatch.setattr(
        "sentinel.cockpit.menu.handle_prompt_callback",
        fake_handle_prompt,
    )

    resp = client.post("/webhooks/telegram", json=_fake_update("update_prompt:y"))
    assert resp.status_code == 200
    assert called_with.get("data") == "update_prompt:y"


def test_answer_callback_query_sent_for_estate(client, monkeypatch):
    """The phone spinner must clear: answerCallbackQuery must be called."""
    api_calls = []

    def fake_api(method: str, body: dict) -> bool:
        api_calls.append((method, body))
        return True

    monkeypatch.setattr("sentinel.cockpit.menu._api", fake_api)

    async def fake_handle_estate(data, chat_id, cbq_id):
        pass  # no-op — just verify answerCallbackQuery was already sent

    monkeypatch.setattr(
        "sentinel.cockpit.menu.handle_estate_callback",
        fake_handle_estate,
    )

    resp = client.post("/webhooks/telegram", json=_fake_update("estate:pause"))
    assert resp.status_code == 200
    answers = [c for c in api_calls if c[0] == "answerCallbackQuery"]
    assert len(answers) >= 1, "answerCallbackQuery was never called"
    assert answers[0][1].get("callback_query_id") == "test-cbq-999"

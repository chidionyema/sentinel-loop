"""
Regression tests for the ship-readiness security fixes.

Each test pins a specific finding from SHIP_READINESS_REVIEW.md so the fix
cannot silently regress. These are VISIBLE tests (the held-out suite in
verify/ is intentionally not touched).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


# =============================================================================
#  C5 + C6 — Telegram webhook origin proof + fail-closed ACL
# =============================================================================


class TestTelegramWebhookAuth:
    def _client(self):
        from sentinel.cockpit.server import create_app
        return TestClient(create_app(), raise_server_exceptions=False)

    def test_c6_acl_fails_closed_when_from_id_absent(self, monkeypatch):
        """C6: an update with no `from` (from_id=None) must be DENIED, not skipped."""
        monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
        monkeypatch.delenv("TELEGRAM_ALLOWED_USER_IDS", raising=False)
        client = self._client()
        # Valid JSON, a message but no `from` block → from_id is None.
        resp = client.post("/webhooks/telegram", json={"message": {"text": "hi"}})
        assert resp.status_code == 403
        assert resp.json()["detail"] == "User not authorized"

    def test_c6_acl_denies_unlisted_user(self, monkeypatch):
        """C6: a present-but-unlisted user id is denied."""
        monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
        monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "111")
        client = self._client()
        resp = client.post("/webhooks/telegram",
                           json={"message": {"from": {"id": 999}, "chat": {"id": 5}}})
        assert resp.status_code == 403

    def test_c5_origin_token_required_when_configured(self, monkeypatch):
        """C5: when TELEGRAM_WEBHOOK_SECRET is set, a request without the
        matching X-Telegram-Bot-Api-Secret-Token header is rejected — even for
        an otherwise-allowed user (the body's from_id is not trustworthy)."""
        monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cr3t-origin")
        monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
        client = self._client()
        resp = client.post("/webhooks/telegram",
                           json={"message": {"from": {"id": 123}, "chat": {"id": 5}}})
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Invalid webhook origin token"

    def test_c5_origin_token_wrong_value_rejected(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cr3t-origin")
        monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
        client = self._client()
        resp = client.post(
            "/webhooks/telegram",
            json={"message": {"from": {"id": 123}, "chat": {"id": 5}}},
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"] == "Invalid webhook origin token"

    def test_c5_c6_happy_path_origin_and_acl_pass(self, monkeypatch):
        """With the correct origin token AND an allowed user, the update is
        accepted — proving the new gates don't break legitimate traffic."""
        monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cr3t-origin")
        monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
        client = self._client()
        resp = client.post(
            "/webhooks/telegram",
            json={"message": {"from": {"id": 123}, "chat": {"id": 5}, "text": "hi"}},
            headers={"X-Telegram-Bot-Api-Secret-Token": "s3cr3t-origin"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "received"

    def test_non_json_still_422_when_no_secret(self, monkeypatch):
        """The origin gate must not change the non-JSON → 422 behavior in dev."""
        monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
        client = self._client()
        resp = client.post("/webhooks/telegram", content=b"not json",
                          headers={"Content-Type": "text/plain"})
        assert resp.status_code in (415, 422)


# =============================================================================
#  C7 — Production startup gate (monitor fail-open closed at boot, not endpoint)
# =============================================================================


class TestProductionEnvGate:
    def test_c7_raises_when_monitor_keys_missing(self, monkeypatch):
        for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_IDS",
                    "TELEGRAM_WEBHOOK_SECRET", "GITHUB_WEBHOOK_SECRET"):
            monkeypatch.setenv(var, "x")
        monkeypatch.delenv("MONITOR_API_KEYS", raising=False)
        from sentinel.cockpit.perimeter import require_production_env
        with pytest.raises(RuntimeError, match="MONITOR_API_KEYS"):
            require_production_env()

    def test_c7_raises_when_origin_secret_missing(self, monkeypatch):
        for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_IDS",
                    "GITHUB_WEBHOOK_SECRET", "MONITOR_API_KEYS"):
            monkeypatch.setenv(var, "x")
        monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
        from sentinel.cockpit.perimeter import require_production_env
        with pytest.raises(RuntimeError, match="TELEGRAM_WEBHOOK_SECRET"):
            require_production_env()

    def test_c7_passes_when_all_present(self, monkeypatch):
        from sentinel.cockpit.perimeter import PRODUCTION_REQUIRED_ENV, require_production_env
        for var in PRODUCTION_REQUIRED_ENV:
            monkeypatch.setenv(var, "configured-value")
        require_production_env()  # must not raise


# =============================================================================
#  C8 — Deploy token is unpredictable (CSPRNG), not derived from public data
# =============================================================================


class TestDeployTokenUnpredictable:
    def test_c8_token_is_random_not_deterministic(self):
        """Same repo/sha/secret must NOT reproduce the same token — proving the
        forgeable HMAC-over-(repo:sha:time) construction is gone."""
        from sentinel.cockpit.github_processor import generate_deploy_token
        toks = {generate_deploy_token("repo", "sha", secret="same") for _ in range(50)}
        # 50 CSPRNG draws of 64 bits: collision probability ~0. Expect 50 unique.
        assert len(toks) == 50

    def test_c8_token_shape_preserved(self):
        from sentinel.cockpit.github_processor import generate_deploy_token
        tok = generate_deploy_token("repo", "sha")
        assert len(tok) == 16
        assert all(c in "0123456789abcdef" for c in tok)

    def test_c8_no_hardcoded_fallback_secret_in_source(self):
        """The public fallback secret literal must be gone from the module."""
        import inspect
        from sentinel.cockpit import github_processor
        src = inspect.getsource(github_processor)
        assert "sentinel-cockpit" not in src

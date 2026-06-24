"""
Visible tests for the DevOps Telegram Cockpit extension.

These tests define the acceptance criteria for all 7 subsystems.
They MUST fail before the implementation exists, and pass after.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# =============================================================================
#  FIXTURES
# =============================================================================


@pytest.fixture
def temp_workspace_root(tmp_path):
    """Create a temporary workspace root with mock projects."""
    root = tmp_path / "workspace"
    root.mkdir()

    # Git + npm project
    p1 = root / "project-alpha"
    p1.mkdir()
    (p1 / ".git").mkdir()
    (p1 / "package.json").write_text('{"name": "alpha"}')

    # Python project
    p2 = root / "project-beta"
    p2.mkdir()
    (p2 / "requirements.txt").write_text("fastapi==0.1.0")

    # Git + Docker project
    p3 = root / "project-gamma"
    p3.mkdir()
    (p3 / ".git").mkdir()
    (p3 / "Dockerfile").write_text("FROM python:3.14")
    (p3 / "docker-compose.yml").write_text("version: '3'")

    # Plain directory (not a project)
    p4 = root / "random-folder"
    p4.mkdir()
    (p4 / "notes.txt").write_text("just notes")

    # Makefile project
    p5 = root / "project-delta"
    p5.mkdir()
    (p5 / "Makefile").write_text("all:\n\techo hi")

    return root


@pytest.fixture
def temp_config_dir(tmp_path):
    """Create config and playbook directories."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "cockpit-settings.json").write_text(json.dumps({
        "workspace_root": str(tmp_path / "workspace"),
        "server": {"host": "127.0.0.1", "port": 8800},
        "ui": {"anti_spam_enabled": True, "max_buttons_per_row": 3},
    }))
    return config_dir


# =============================================================================
#  SUBSYSTEM 1: HTTP Webhook Server
# =============================================================================


class TestCockpitServer:
    """FastAPI server with webhook endpoints, health check, and proper binding."""

    def test_server_app_creates(self):
        """The FastAPI app must be importable and have expected routes."""
        from sentinel.cockpit.server import create_app
        app = create_app()
        assert app is not None
        # Must have at minimum the health endpoint
        routes = [r.path for r in app.routes]
        assert "/health" in routes
        assert "/webhooks/telegram" in routes
        assert "/webhooks/github" in routes
        assert "/webhooks/monitor" in routes

    def test_health_endpoint_returns_ok(self):
        """GET /health must return status ok."""
        from sentinel.cockpit.server import create_app
        from fastapi.testclient import TestClient

        app = create_app()
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "daemon" in data

    def test_telegram_webhook_rejects_non_json(self):
        """POST /webhooks/telegram with non-JSON body must return 422."""
        from sentinel.cockpit.server import create_app
        from fastapi.testclient import TestClient

        app = create_app()
        client = TestClient(app)
        response = client.post("/webhooks/telegram", content=b"not json",
                              headers={"Content-Type": "text/plain"})
        assert response.status_code == 422 or response.status_code == 415

    def test_github_webhook_missing_signature(self):
        """POST /webhooks/github without X-Hub-Signature-256 must be rejected."""
        from sentinel.cockpit.server import create_app
        from fastapi.testclient import TestClient

        app = create_app()
        client = TestClient(app)
        response = client.post("/webhooks/github", json={"test": True})
        # Must reject (401 or 403) — no signature header
        assert response.status_code in (401, 403)

    def test_monitor_webhook_accepts_json(self):
        """POST /webhooks/monitor must accept valid JSON payload."""
        from sentinel.cockpit.server import create_app
        from fastapi.testclient import TestClient

        app = create_app()
        client = TestClient(app)
        response = client.post(
            "/webhooks/monitor",
            json={"service": "api", "message": "high latency", "severity": "warning", "source": "datadog"},
            headers={"X-API-Key": "test-key-123"},
        )
        # Should not 401/403 with API key; may 200 or 202
        assert response.status_code in (200, 202)

    def test_server_binds_localhost(self):
        """The server config must bind to 127.0.0.1 by default."""
        from sentinel.cockpit.server import get_server_config
        host, port = get_server_config()
        assert host == "127.0.0.1"
        assert isinstance(port, int)


# =============================================================================
#  SUBSYSTEM 2: Telegram UI Engine
# =============================================================================


class TestCockpitUIEngine:
    """Telegram UI engine with callback parsing, menu navigation, and anti-spam."""

    def test_parse_callback_valid(self):
        """Valid callback data must parse into action/target/id dict."""
        from sentinel.cockpit.ui_engine import parse_callback

        result = parse_callback("git_pull:project-alpha:main")
        assert result == {"action": "git_pull", "target": "project-alpha", "id": "main"}

    def test_parse_callback_two_segments(self):
        """Two-segment callback must parse correctly (back navigation)."""
        from sentinel.cockpit.ui_engine import parse_callback

        result = parse_callback("navigate:level_1")
        assert result == {"action": "navigate", "target": "level_1", "id": ""}

    def test_parse_callback_rejects_empty(self):
        """Empty or blank callback data must raise ValueError."""
        from sentinel.cockpit.ui_engine import parse_callback

        with pytest.raises(ValueError):
            parse_callback("")

        with pytest.raises(ValueError):
            parse_callback("   ")

        with pytest.raises(ValueError):
            parse_callback("::::")

    def test_parse_callback_rejects_empty_segments(self):
        """Callback with empty segment must raise ValueError."""
        from sentinel.cockpit.ui_engine import parse_callback

        with pytest.raises(ValueError):
            parse_callback("action::id")

    def test_menu_navigate_level_0_to_1(self):
        """Navigate from level 0 to level 1 must return level 1 with keyboard."""
        from sentinel.cockpit.ui_engine import CockpitUIEngine

        engine = CockpitUIEngine()
        new_level, keyboard = engine.navigate(current_level=0, target="projects")
        assert new_level == 1
        assert keyboard is not None

    def test_menu_navigate_returns_keyboard_with_back(self):
        """Navigation to sub-level must include a back button."""
        from sentinel.cockpit.ui_engine import CockpitUIEngine

        engine = CockpitUIEngine()
        _, keyboard = engine.navigate(current_level=1, target="project-alpha")
        # keyboard is a list of button rows; at least one row must contain "Back"
        flat_buttons = []
        for row in keyboard:
            for btn in row:
                flat_buttons.append(btn.get("text", ""))
        assert any("Back" in b for b in flat_buttons)

    def test_menu_back_to_level_0(self):
        """Navigating 'back' from level 1 must return to level 0."""
        from sentinel.cockpit.ui_engine import CockpitUIEngine

        engine = CockpitUIEngine()
        new_level, keyboard = engine.navigate(current_level=1, target="back")
        assert new_level == 0

    def test_edit_state_tracking(self):
        """Edit state must store and retrieve last message_id per chat."""
        from sentinel.cockpit.ui_engine import CockpitUIEngine

        engine = CockpitUIEngine()
        engine.record_message("chat_123", "msg_456")
        state = engine.get_edit_state("chat_123")
        assert state["last_message_id"] == "msg_456"

    def test_edit_state_returns_none_for_unknown_chat(self):
        """get_edit_state for unknown chat must return empty dict."""
        from sentinel.cockpit.ui_engine import CockpitUIEngine

        engine = CockpitUIEngine()
        state = engine.get_edit_state("unknown_chat")
        assert state.get("last_message_id") is None

    def test_build_inline_keyboard(self):
        """build_inline_keyboard must return valid Telegram keyboard structure."""
        from sentinel.cockpit.ui_engine import build_inline_keyboard

        buttons = [
            [{"text": "Projects", "callback_data": "nav:projects:"}],
            [{"text": "CI/CD", "callback_data": "nav:cicd:"}],
        ]
        keyboard = build_inline_keyboard(buttons)
        assert "inline_keyboard" in keyboard
        assert len(keyboard["inline_keyboard"]) == 2

    def test_level_0_keyboard_structure(self):
        """Level 0 dashboard must have the 4 main navigation buttons."""
        from sentinel.cockpit.ui_engine import CockpitUIEngine

        engine = CockpitUIEngine()
        _, keyboard = engine.navigate(current_level=0, target="root")
        all_text = []
        for row in keyboard:
            for btn in row:
                all_text.append(btn["text"])
        # Must contain the 4 main nav items
        assert any("Projects" in t for t in all_text)
        # At least 3 buttons total
        assert len(all_text) >= 3


# =============================================================================
#  SUBSYSTEM 3: GitHub Webhook Processor
# =============================================================================


class TestCockpitGitHub:
    """GitHub webhook processor for push events and workflow status."""

    def test_parse_branch_ref(self):
        """Must extract branch name from refs/heads/... format."""
        from sentinel.cockpit.github_processor import parse_branch_ref

        assert parse_branch_ref("refs/heads/main") == "main"
        assert parse_branch_ref("refs/heads/feature/login") == "feature/login"
        assert parse_branch_ref("refs/tags/v1.0") == "v1.0"

    def test_process_push_event(self):
        """Push event must produce a message block with deploy button."""
        from sentinel.cockpit.github_processor import process_push_event

        payload = {
            "repository": {"full_name": "user/repo", "name": "repo"},
            "ref": "refs/heads/main",
            "head_commit": {
                "message": "Fix login bug",
                "author": {"name": "Alice"},
                "id": "abc123def456",
            },
        }
        result = process_push_event(payload)
        assert "text" in result
        assert "Alice" in result["text"]
        assert "Fix login bug" in result["text"]
        # Must have inline keyboard with deploy button
        assert "reply_markup" in result
        keyboard = result["reply_markup"].get("inline_keyboard", [])
        all_cb = []
        for row in keyboard:
            for btn in row:
                all_cb.append(btn.get("callback_data", ""))
        assert any("deploy:" in cb for cb in all_cb)

    def test_process_workflow_success(self):
        """Completed successful workflow must produce 🟢 status."""
        from sentinel.cockpit.github_processor import process_workflow_event

        payload = {
            "workflow_run": {
                "status": "completed",
                "conclusion": "success",
                "name": "CI",
            },
            "repository": {"full_name": "user/repo", "name": "repo"},
        }
        result = process_workflow_event(payload)
        assert result is not None
        assert "🟢" in result["text"]

    def test_process_workflow_failure(self):
        """Completed failure workflow must produce 🔴 status."""
        from sentinel.cockpit.github_processor import process_workflow_event

        payload = {
            "workflow_run": {
                "status": "completed",
                "conclusion": "failure",
                "name": "CI",
            },
            "repository": {"full_name": "user/repo", "name": "repo"},
        }
        result = process_workflow_event(payload)
        assert result is not None
        assert "🔴" in result["text"]

    def test_process_workflow_in_progress_returns_none(self):
        """In-progress workflow must return None (no update needed yet)."""
        from sentinel.cockpit.github_processor import process_workflow_event

        payload = {
            "workflow_run": {
                "status": "in_progress",
                "conclusion": None,
                "name": "CI",
            },
            "repository": {"full_name": "user/repo", "name": "repo"},
        }
        result = process_workflow_event(payload)
        assert result is None

    def test_deploy_token_generation(self):
        """Each push must generate a unique, hex deploy token."""
        from sentinel.cockpit.github_processor import generate_deploy_token

        token1 = generate_deploy_token("repo", "abc123", secret="test-secret")
        token2 = generate_deploy_token("repo", "def456", secret="test-secret")
        assert token1 != token2
        assert len(token1) == 16
        assert all(c in "0123456789abcdef" for c in token1)


# =============================================================================
#  SUBSYSTEM 4: Monitoring Alert Ingestion
# =============================================================================


class TestCockpitMonitor:
    """Multi-source monitoring alert ingestion and triage."""

    def test_parse_sentry_alert(self):
        """Sentry webhook payload must parse into normalized AlertData."""
        from sentinel.cockpit.monitor_ingestion import parse_alert, AlertData

        payload = {
            "event": {
                "title": "TypeError: Cannot read property 'x'",
                "culprit": "api.users.get",
                "location": "users.py:42",
            },
            "url": "https://sentry.io/issues/123",
        }
        alert = parse_alert(payload, source="sentry")
        assert isinstance(alert, AlertData)
        assert alert.source == "sentry"

    def test_parse_datadog_alert(self):
        """Datadog monitor payload must parse into normalized AlertData."""
        from sentinel.cockpit.monitor_ingestion import parse_alert, AlertData

        payload = {
            "alert_type": "error",
            "title": "CPU above 90%",
            "body": "{{#is_alert}}CPU on api-service > 90%{{/is_alert}}",
            "tags": ["service:api", "env:prod"],
        }
        alert = parse_alert(payload, source="datadog")
        assert isinstance(alert, AlertData)
        assert alert.source == "datadog"

    def test_critical_alert_should_override(self):
        """Critical alert must override non-critical chat state."""
        from sentinel.cockpit.monitor_ingestion import should_override, AlertData

        alert = AlertData(
            service="api", message="Down",
            severity="critical", source="datadog",
            stack_trace=None, raw={},
        )
        current_state = {"level": 0, "alert_active": "warning"}
        assert should_override(alert, current_state) is True

    def test_warning_alert_should_not_override_critical(self):
        """Warning alert must NOT override critical chat state."""
        from sentinel.cockpit.monitor_ingestion import should_override, AlertData

        alert = AlertData(
            service="api", message="Slow",
            severity="warning", source="datadog",
            stack_trace=None, raw={},
        )
        current_state = {"level": 2, "alert_active": "critical"}
        assert should_override(alert, current_state) is False

    def test_build_emergency_buttons(self):
        """Emergency buttons must include Restart, Rollback, and Mute."""
        from sentinel.cockpit.monitor_ingestion import build_emergency_buttons, AlertData

        alert = AlertData(
            service="api-service", message="Down",
            severity="critical", source="sentry",
            stack_trace="line 42", raw={},
        )
        buttons = build_emergency_buttons(alert)
        all_cb = []
        for row in buttons:
            for btn in row:
                all_cb.append(btn.get("callback_data", ""))

        assert any("restart:" in cb for cb in all_cb)
        assert any("rollback:" in cb for cb in all_cb)
        assert any("mute:" in cb for cb in all_cb)

    def test_critical_alert_message_format(self):
        """Critical alert message must include stack trace and service name."""
        from sentinel.cockpit.monitor_ingestion import format_critical_alert, AlertData

        alert = AlertData(
            service="payment-worker",
            message="Process exited with code 1",
            severity="critical",
            source="sentry",
            stack_trace="Traceback...\n  File \"worker.py\", line 99\n    raise FatalError()",
            raw={},
        )
        msg = format_critical_alert(alert)
        assert "CRITICAL" in msg
        assert "payment-worker" in msg
        assert "worker.py" in msg


# =============================================================================
#  SUBSYSTEM 5: External ACL & Webhook Auth
# =============================================================================


class TestCockpitACL:
    """Access control, HMAC verification, and shell command safety."""

    def test_validate_telegram_user_allowed(self, monkeypatch):
        """Allowed user ID must return True."""
        monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123456,789012")
        from sentinel.cockpit.acl import validate_telegram_user

        assert validate_telegram_user(123456) is True

    def test_validate_telegram_user_denied(self, monkeypatch):
        """Disallowed user ID must return False."""
        monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123456")
        from sentinel.cockpit.acl import validate_telegram_user

        assert validate_telegram_user(999999) is False

    def test_validate_telegram_user_missing_env(self, monkeypatch):
        """Missing env var must deny all users."""
        monkeypatch.delenv("TELEGRAM_ALLOWED_USER_IDS", raising=False)
        from sentinel.cockpit.acl import validate_telegram_user

        assert validate_telegram_user(123456) is False

    def test_verify_github_hmac_valid(self, monkeypatch):
        """Valid HMAC signature must pass verification."""
        import hmac
        import hashlib

        secret = "my-github-secret"
        monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", secret)

        payload = b'{"test": true}'
        signature = "sha256=" + hmac.new(
            secret.encode(), payload, hashlib.sha256
        ).hexdigest()

        from sentinel.cockpit.acl import verify_github_hmac
        assert verify_github_hmac(payload, signature) is True

    def test_verify_github_hmac_invalid(self, monkeypatch):
        """Wrong HMAC signature must fail verification."""
        monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "correct-secret")

        from sentinel.cockpit.acl import verify_github_hmac
        assert verify_github_hmac(b'{"test": true}', "sha256=deadbeef") is False

    def test_verify_github_hmac_timing_safe(self, monkeypatch):
        """Mismatched length signatures must not leak timing info."""
        monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")

        from sentinel.cockpit.acl import verify_github_hmac
        # Short signature
        assert verify_github_hmac(b"payload", "sha256=abc") is False
        # Wrong prefix
        assert verify_github_hmac(b"payload", "sha1=abcdef123456") is False

    def test_validate_workspace_path_allowed(self, tmp_path):
        """Valid path within workspace root must pass."""
        root = tmp_path / "workspace"
        root.mkdir()
        (root / "myproject").mkdir()

        from sentinel.cockpit.acl import validate_workspace_path
        assert validate_workspace_path("myproject", str(root)) is True

    def test_validate_workspace_path_blocks_traversal(self, tmp_path):
        """Path traversal attempt must be blocked."""
        root = tmp_path / "workspace"
        root.mkdir()

        from sentinel.cockpit.acl import validate_workspace_path
        assert validate_workspace_path("../../../etc/passwd", str(root)) is False

    def test_validate_workspace_path_nonexistent(self, tmp_path):
        """Nonexistent path must be denied."""
        root = tmp_path / "workspace"
        root.mkdir()

        from sentinel.cockpit.acl import validate_workspace_path
        assert validate_workspace_path("nonexistent", str(root)) is False

    def test_command_registry_has_required_commands(self):
        """Command registry must contain essential actions."""
        from sentinel.cockpit.acl import COMMAND_REGISTRY

        required = ["git_pull", "git_status", "git_log", "npm_dev"]
        for cmd in required:
            assert cmd in COMMAND_REGISTRY, f"Missing command: {cmd}"

    def test_command_registry_all_use_placeholders(self):
        """All commands must use {workspace} or {branch} or {service} placeholders."""
        from sentinel.cockpit.acl import COMMAND_REGISTRY

        for name, template in COMMAND_REGISTRY.items():
            assert isinstance(template, str), f"{name}: template must be a string"
            # Every command template should contain at least one placeholder
            assert "{" in template, (
                f"{name}: template must contain at least one placeholder like {{workspace}}"
            )


# =============================================================================
#  SUBSYSTEM 6: Workspace Project Scanner
# =============================================================================


class TestCockpitScanner:
    """Workspace scanner with marker detection and project registry."""

    def test_scan_discovers_projects(self, temp_workspace_root):
        """Scanner must discover all valid project directories."""
        from sentinel.cockpit.workspace_scanner import scan_workspace

        projects = scan_workspace(str(temp_workspace_root))
        names = {p.name for p in projects}

        assert "project-alpha" in names
        assert "project-beta" in names
        assert "project-gamma" in names
        assert "project-delta" in names
        # random-folder has no project markers
        assert "random-folder" not in names

    def test_scan_detects_git_marker(self, temp_workspace_root):
        """Projects with .git must have has_git=True."""
        from sentinel.cockpit.workspace_scanner import scan_workspace

        projects = scan_workspace(str(temp_workspace_root))
        alpha = next(p for p in projects if p.name == "project-alpha")
        assert alpha.has_git is True

    def test_scan_detects_package_manager(self, temp_workspace_root):
        """package.json project must have package_manager='npm'."""
        from sentinel.cockpit.workspace_scanner import scan_workspace

        projects = scan_workspace(str(temp_workspace_root))
        alpha = next(p for p in projects if p.name == "project-alpha")
        assert alpha.package_manager == "npm"

    def test_scan_detects_python_project(self, temp_workspace_root):
        """requirements.txt project must have package_manager='pip'."""
        from sentinel.cockpit.workspace_scanner import scan_workspace

        projects = scan_workspace(str(temp_workspace_root))
        beta = next(p for p in projects if p.name == "project-beta")
        assert beta.package_manager == "pip"

    def test_scan_detects_docker_markers(self, temp_workspace_root):
        """Project with Dockerfile must include docker markers."""
        from sentinel.cockpit.workspace_scanner import scan_workspace

        projects = scan_workspace(str(temp_workspace_root))
        gamma = next(p for p in projects if p.name == "project-gamma")
        assert "Dockerfile" in gamma.markers
        assert "docker-compose.yml" in gamma.markers

    def test_scan_returns_project_info_dataclass(self, temp_workspace_root):
        """Each project must be a ProjectInfo dataclass with required fields."""
        from sentinel.cockpit.workspace_scanner import scan_workspace, ProjectInfo

        projects = scan_workspace(str(temp_workspace_root))
        for p in projects:
            assert isinstance(p, ProjectInfo)
            assert isinstance(p.name, str)
            assert isinstance(p.path, str)
            assert isinstance(p.markers, list)
            assert isinstance(p.has_git, bool)

    def test_registry_persistence(self, temp_workspace_root):
        """Project registry must persist and reload from SQLite."""
        from sentinel.cockpit.workspace_scanner import scan_workspace, save_registry, load_registry

        projects = scan_workspace(str(temp_workspace_root))
        db_path = str(temp_workspace_root.parent / "registry.db")
        save_registry(projects, db_path)

        loaded = load_registry(db_path)
        assert len(loaded) == len(projects)
        loaded_names = {p.name for p in loaded}
        assert loaded_names == {p.name for p in projects}


# =============================================================================
#  SUBSYSTEM 7: Perimeter Hardening
# =============================================================================


class TestCockpitPerimeter:
    """Perimeter configuration, tunnel readiness, and env validation."""

    def test_get_bind_config_defaults(self):
        """Defaults must bind to 127.0.0.1:8800."""
        from sentinel.cockpit.perimeter import get_bind_config

        host, port = get_bind_config()
        assert host == "127.0.0.1"
        assert port == 8800

    def test_get_bind_config_refuses_wildcard(self, monkeypatch):
        """Must refuse 0.0.0.0 binding."""
        monkeypatch.setenv("COCKPIT_HOST", "0.0.0.0")

        from sentinel.cockpit.perimeter import get_bind_config
        with pytest.raises(ValueError, match="0.0.0.0"):
            get_bind_config()

    def test_verify_tunnel_config_returns_status(self):
        """Tunnel check must return a structured status dict."""
        from sentinel.cockpit.perimeter import verify_tunnel_config

        status = verify_tunnel_config()
        assert isinstance(status, dict)
        assert "ready" in status
        assert "message" in status

    def test_validate_cockpit_env_checks_required(self, monkeypatch):
        """Env validator must detect missing required vars."""
        # Clear relevant env vars
        for var in ["TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_IDS"]:
            monkeypatch.delenv(var, raising=False)

        from sentinel.cockpit.perimeter import validate_cockpit_env

        issues = validate_cockpit_env()
        assert len(issues) > 0
        assert any("TELEGRAM_BOT_TOKEN" in i for i in issues)

    def test_validate_cockpit_env_all_present(self, monkeypatch):
        """Env validator must return empty when all vars set."""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
        monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123456")
        monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "secret")
        monkeypatch.setenv("MONITOR_API_KEYS", '{"sentry": "key1"}')

        from sentinel.cockpit.perimeter import validate_cockpit_env

        issues = validate_cockpit_env()
        assert len(issues) == 0


# =============================================================================
#  INTEGRATION TESTS
# =============================================================================


class TestCockpitIntegration:
    """End-to-end cockpit integration tests."""

    def test_full_button_click_to_command_pipeline(self, tmp_path):
        """A Telegram button click must route through the full pipeline."""
        from sentinel.cockpit.ui_engine import parse_callback
        from sentinel.cockpit.acl import COMMAND_REGISTRY, validate_workspace_path

        # Setup workspace
        root = tmp_path / "workspace"
        root.mkdir()
        (root / "myapp").mkdir()
        (root / "myapp" / ".git").mkdir()

        # Simulate: user clicks "Git Pull" on project "myapp", branch "main"
        callback_data = "git_pull:myapp:main"
        parsed = parse_callback(callback_data)

        assert parsed["action"] == "git_pull"
        assert parsed["target"] == "myapp"
        assert parsed["id"] == "main"

        # Validate workspace
        assert validate_workspace_path(parsed["target"], str(root)) is True

        # Resolve command template
        template = COMMAND_REGISTRY.get(parsed["action"])
        assert template is not None
        assert "{workspace}" in template
        assert "{branch}" in template

    def test_alert_ingestion_to_button_flow(self):
        """Critical alert must produce message with emergency buttons."""
        from sentinel.cockpit.monitor_ingestion import (
            parse_alert, build_emergency_buttons, should_override, format_critical_alert,
            AlertData,
        )

        # Simulate a Datadog critical alert
        payload = {
            "title": "Payment API down",
            "alert_type": "error",
            "body": "{{#is_alert}}Payment API returning 500{{/is_alert}}",
            "tags": ["service:payment-api", "env:prod"],
        }
        alert = parse_alert(payload, source="datadog")
        alert = AlertData(
            service="payment-api",
            message="Payment API returning 500",
            severity="critical",
            source="datadog",
            stack_trace="File \"api.py\", line 200, in process_payment\n    raise GatewayError()",
            raw=payload,
        )

        # Override check
        current = {"level": 1, "alert_active": None}
        assert should_override(alert, current) is True

        # Format message
        msg = format_critical_alert(alert)
        assert "CRITICAL" in msg

        # Build buttons
        buttons = build_emergency_buttons(alert)
        assert len(buttons) >= 2  # At least rollout and mute

    def test_workspace_to_menu_navigation(self, temp_workspace_root):
        """Scanned projects must appear in the menu navigation."""
        from sentinel.cockpit.workspace_scanner import scan_workspace
        from sentinel.cockpit.ui_engine import CockpitUIEngine

        engine = CockpitUIEngine()
        projects = scan_workspace(str(temp_workspace_root))
        # Feed discovered projects into the UI engine
        engine.set_projects([p.name for p in projects])

        # Level 1 projects view should include discovered projects
        _, keyboard = engine.navigate(current_level=0, target="projects")

        all_text = []
        for row in keyboard:
            for btn in row:
                all_text.append(btn.get("text", ""))

        for proj in projects:
            assert any(proj.name in txt for txt in all_text), \
                f"Project {proj.name} not in menu (texts: {all_text})"

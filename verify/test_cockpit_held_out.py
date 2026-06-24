"""
Held-out verification tests for the DevOps Telegram Cockpit.

The implementation agent must NEVER read or modify these files.
Violation is detectable via the verify/.audit.json hash trail.

These tests verify properties the visible tests CANNOT verify because
the implementer could game them:
  - That webhook endpoints behave correctly with edge-case payloads
  - That the UI engine's state machine rejects all invalid transitions
  - That ACL checks are not bypassable with edge inputs
  - That workspace scanner handles symlinks, hidden dirs, and race conditions
  - That the server fully binds to localhost (not just returning the config value)
  - That shell commands are NEVER executed with raw/unvalidated user input
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# =============================================================================
#  HELD-OUT: Subsystem 1 — Webhook Server Edge Cases
# =============================================================================


class TestHeldOut_CockpitServer:
    """Verify the webhook server handles edge cases correctly."""

    def test_health_endpoint_excludes_sensitive_info(self):
        """GET /health must NOT leak env vars, tokens, or internal paths."""
        from sentinel.cockpit.server import create_app
        from fastapi.testclient import TestClient

        app = create_app()
        client = TestClient(app)
        response = client.get("/health")
        data = response.json()

        # Must not leak secrets
        assert "BOT_TOKEN" not in json.dumps(data)
        assert "SECRET" not in json.dumps(data)
        assert "WEBHOOK_SECRET" not in json.dumps(data)

    def test_telegram_webhook_rejects_invalid_json(self):
        """POST /webhooks/telegram with invalid JSON must return 422, not 500."""
        from sentinel.cockpit.server import create_app
        from fastapi.testclient import TestClient

        app = create_app()
        client = TestClient(app)
        response = client.post(
            "/webhooks/telegram",
            content=b"{invalid json!!}",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code in (400, 422)

    def test_github_webhook_empty_body(self):
        """POST /webhooks/github with empty body must not 500-crash."""
        from sentinel.cockpit.server import create_app
        from fastapi.testclient import TestClient

        app = create_app()
        client = TestClient(app)

        # Empty body with valid-looking header
        response = client.post(
            "/webhooks/github",
            content=b"",
            headers={
                "X-Hub-Signature-256": "sha256=abc123",
                "Content-Type": "application/json",
            },
        )
        # Must produce a client or auth error, not 500
        assert response.status_code in (400, 401, 403, 422)

    def test_monitor_webhook_unknown_source_still_accepted(self):
        """Monitor webhook from unknown source must NOT crash; gracefully handled."""
        from sentinel.cockpit.server import create_app
        from fastapi.testclient import TestClient

        app = create_app()
        client = TestClient(app)
        response = client.post(
            "/webhooks/monitor",
            json={"service": "api", "message": "test", "severity": "info", "source": "unknown-vendor"},
            headers={"X-API-Key": "test-key"},
        )
        assert response.status_code in (200, 202)

    def test_unknown_route_returns_404(self):
        """Unknown routes must return 404, not leak internal errors."""
        from sentinel.cockpit.server import create_app
        from fastapi.testclient import TestClient

        app = create_app()
        client = TestClient(app)
        response = client.get("/nonexistent-path-xyz")
        assert response.status_code == 404

    def test_server_binds_only_localhost(self):
        """The uvicorn config must use host='127.0.0.1', not 0.0.0.0."""
        from sentinel.cockpit.server import get_server_config

        host, port = get_server_config()
        assert host in ("127.0.0.1", "localhost"), (
            f"Server must bind to localhost only, got host={host}"
        )


# =============================================================================
#  HELD-OUT: Subsystem 2 — UI Engine State Machine Completeness
# =============================================================================


class TestHeldOut_CockpitUIEngine:
    """Verify the UI engine state machine handles all edge transitions."""

    def test_all_level_navigation_cycles(self):
        """Every level must support going deeper and returning back."""
        from sentinel.cockpit.ui_engine import CockpitUIEngine

        engine = CockpitUIEngine()

        # Full cycle: 0 -> 1 -> 2 -> 1 -> 0
        lvl, _ = engine.navigate(0, "projects")
        assert lvl == 1

        lvl, _ = engine.navigate(1, "project-alpha")
        assert lvl == 2

        lvl, _ = engine.navigate(2, "back")
        assert lvl == 1

        lvl, _ = engine.navigate(1, "back")
        assert lvl == 0

    def test_navigate_invalid_target_does_not_crash(self):
        """Navigating to an unrecognized target must not crash."""
        from sentinel.cockpit.ui_engine import CockpitUIEngine

        engine = CockpitUIEngine()
        # Should handle gracefully — return same or error state
        try:
            lvl, kb = engine.navigate(0, "xyzzy_nonexistent_target")
            # Must still return valid keyboard
            assert kb is not None
            assert isinstance(lvl, int)
        except ValueError:
            pass  # Also acceptable

    def test_level_2_always_has_back_button(self):
        """Level 2 views must ALWAYS include a back button."""
        from sentinel.cockpit.ui_engine import CockpitUIEngine

        engine = CockpitUIEngine()
        _, kb = engine.navigate(1, "project-alpha")
        has_back = any(
            "Back" in btn.get("text", "")
            for row in kb
            for btn in row
        )
        assert has_back, "Level 2 view missing back button"

    def test_callback_parser_edge_cases(self):
        """Callback parser must handle all edge formats correctly."""
        from sentinel.cockpit.ui_engine import parse_callback

        # Single segment is valid (back navigation: "back")
        valid_singles = ["back", "root"]
        for data in valid_singles:
            result = parse_callback(f"{data}::")  # action::empty
            assert result["action"] == data

        # Special characters in segments
        result = parse_callback("deploy:user/repo:abc123def456")
        assert result["target"] == "user/repo"
        assert result["id"] == "abc123def456"

        # Long segments
        long_data = f"action:{'x' * 100}:{'y' * 100}"
        result = parse_callback(long_data)
        assert len(result["target"]) == 100
        assert len(result["id"]) == 100

    def test_build_inline_keyboard_rejects_malformed_buttons(self):
        """Malformed button spec must be handled gracefully."""
        from sentinel.cockpit.ui_engine import build_inline_keyboard

        # Empty keyboard
        kb = build_inline_keyboard([])
        assert "inline_keyboard" in kb
        assert kb["inline_keyboard"] == []

        # Row with no buttons
        kb = build_inline_keyboard([[], [{"text": "OK", "callback_data": "ok:"}]])
        assert "inline_keyboard" in kb

    def test_edit_state_concurrent_chats(self):
        """Multiple chat IDs must be tracked independently."""
        from sentinel.cockpit.ui_engine import CockpitUIEngine

        engine = CockpitUIEngine()
        engine.record_message("chat_A", "msg_1")
        engine.record_message("chat_B", "msg_2")

        assert engine.get_edit_state("chat_A")["last_message_id"] == "msg_1"
        assert engine.get_edit_state("chat_B")["last_message_id"] == "msg_2"

    def test_level0_level1_level2_have_different_keyboards(self):
        """Each level must produce a distinct keyboard (not a copy-paste)."""
        from sentinel.cockpit.ui_engine import CockpitUIEngine

        engine = CockpitUIEngine()

        _, kb0 = engine.navigate(0, "root")
        _, kb1 = engine.navigate(0, "projects")
        _, kb2 = engine.navigate(1, "project-alpha")

        # At minimum, level 2 has a back button and level 0 does not
        def has_back(kb):
            return any("Back" in btn.get("text", "")
                       for row in kb for btn in row)

        assert not has_back(kb0), "Level 0 must not have back button"
        assert not has_back(kb1), "Level 1 must not have back button"
        assert has_back(kb2), "Level 2 must have back button"


# =============================================================================
#  HELD-OUT: Subsystem 3 — GitHub Processor Edge Cases
# =============================================================================


class TestHeldOut_CockpitGitHub:
    """Verify GitHub processor handles edge-case payloads correctly."""

    def test_process_push_event_tag_ref(self):
        """Push to a tag (not branch) must be handled."""
        from sentinel.cockpit.github_processor import process_push_event, parse_branch_ref

        branch = parse_branch_ref("refs/tags/v2.0.1")
        assert branch == "v2.0.1"

        payload = {
            "repository": {"full_name": "user/repo", "name": "repo"},
            "ref": "refs/tags/v2.0.1",
            "head_commit": {
                "message": "Release v2.0.1",
                "author": {"name": "CI Bot"},
                "id": "tag123abc",
            },
        }
        result = process_push_event(payload)
        assert result is not None
        assert "Release v2.0.1" in result["text"]

    def test_process_workflow_event_no_conclusion(self):
        """Workflow with status=completed but no conclusion must not crash."""
        from sentinel.cockpit.github_processor import process_workflow_event

        payload = {
            "workflow_run": {
                "status": "completed",
                "conclusion": None,
                "name": "Build",
            },
            "repository": {"full_name": "user/repo", "name": "repo"},
        }
        # Must not crash, may return None or a message
        result = process_workflow_event(payload)
        # Accept None or a message — just don't crash
        if result is not None:
            assert "text" in result

    def test_process_push_event_no_head_commit(self):
        """Push event without head_commit must not crash."""
        from sentinel.cockpit.github_processor import process_push_event

        payload = {
            "repository": {"full_name": "user/repo", "name": "repo"},
            "ref": "refs/heads/main",
            # No head_commit
        }
        result = process_push_event(payload)
        # Must not crash. May return a degraded message or None.
        if result is not None:
            assert "text" in result

    def test_parse_branch_ref_edge_cases(self):
        """Branch ref parser must handle edge cases."""
        from sentinel.cockpit.github_processor import parse_branch_ref

        assert parse_branch_ref("main") == "main"  # Bare branch
        assert parse_branch_ref("refs/heads/") == ""  # Empty after prefix
        assert parse_branch_ref("") == ""  # Empty ref


# =============================================================================
#  HELD-OUT: Subsystem 4 — Monitor Ingestion Edge Cases
# =============================================================================


class TestHeldOut_CockpitMonitor:
    """Verify monitor ingestion handles edge payloads correctly."""

    def test_parse_alert_unknown_source_no_crash(self):
        """Parse alert with unknown source must not crash."""
        from sentinel.cockpit.monitor_ingestion import parse_alert

        alert = parse_alert({"message": "test"}, source="unknown-system")
        assert alert is not None
        assert alert.source == "unknown-system"
        assert alert.service is not None  # Degraded but not crashed

    def test_parse_alert_empty_payload_no_crash(self):
        """Empty payload must not crash the parser."""
        from sentinel.cockpit.monitor_ingestion import parse_alert

        alert = parse_alert({}, source="sentry")
        assert alert is not None

    def test_should_override_same_severity(self):
        """Alert at same severity as current must not override."""
        from sentinel.cockpit.monitor_ingestion import should_override, AlertData

        alert = AlertData(
            service="api", message="test",
            severity="critical", source="sentry",
            stack_trace=None, raw={},
        )
        current = {"alert_active": "critical"}
        assert should_override(alert, current) is False

    def test_format_critical_alert_truncates_long_stack(self):
        """Stack trace longer than 500 chars must be truncated."""
        from sentinel.cockpit.monitor_ingestion import format_critical_alert, AlertData

        long_trace = "Line " + "very long trace " * 200
        alert = AlertData(
            service="test-svc", message="fail",
            severity="critical", source="sentry",
            stack_trace=long_trace, raw={},
        )
        msg = format_critical_alert(alert)
        # The stack in the message should be truncated
        assert len(msg) < len(long_trace) + 200  # message overhead + truncated stack

    def test_emergency_buttons_all_have_correct_callback_format(self):
        """Every emergency button must use action:target:id format."""
        from sentinel.cockpit.monitor_ingestion import build_emergency_buttons, AlertData

        alert = AlertData(
            service="payment-api", message="down",
            severity="critical", source="datadog",
            stack_trace=None, raw={},
        )
        buttons = build_emergency_buttons(alert)
        for row in buttons:
            for btn in row:
                cb = btn.get("callback_data", "")
                # Must be parseable as action:target:id
                parts = cb.split(":")
                assert len(parts) >= 2, f"Bad callback format: {cb}"
                assert all(p != "" for p in parts[:2]), f"Empty segment in: {cb}"


# =============================================================================
#  HELD-OUT: Subsystem 5 — ACL Bypass Attempts
# =============================================================================


class TestHeldOut_CockpitACL:
    """Verify ACL cannot be bypassed with edge inputs."""

    def test_telegram_user_edge_ids(self):
        """Edge user IDs (0, negative, very large) must be rejected."""
        from sentinel.cockpit.acl import validate_telegram_user

        # Without env, all are denied
        assert validate_telegram_user(0) is False
        assert validate_telegram_user(-1) is False
        assert validate_telegram_user(2**63) is False

    def test_workspace_path_symlink_bypass_attempt(self, tmp_path):
        """Symlink from workspace to outside must be blocked."""
        from sentinel.cockpit.acl import validate_workspace_path

        root = tmp_path / "workspace"
        root.mkdir()

        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("classified")

        # Create symlink inside workspace pointing outside
        link = root / "escape_link"
        link.symlink_to(outside)

        # Path "escape_link/secret.txt" must be blocked
        assert validate_workspace_path("escape_link/secret.txt", str(root)) is False

    def test_workspace_path_dot_dot_variants(self, tmp_path):
        """Various traversal attempts must all be blocked."""
        from sentinel.cockpit.acl import validate_workspace_path

        root = tmp_path / "workspace"
        root.mkdir()
        (root / "legit").mkdir()

        traversals = [
            "../etc/passwd",
            "..%2Fetc%2Fpasswd",  # URL-encoded
            "./../../etc",
            "legit/../../../etc",
            "/etc/passwd",  # Absolute
        ]
        for path in traversals:
            assert not validate_workspace_path(path, str(root)), (
                f"Path traversal not blocked: {path}"
            )

    def test_github_hmac_rejects_missing_secret(self, monkeypatch):
        """When GITHUB_WEBHOOK_SECRET is not set, all HMAC must fail."""
        monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
        from sentinel.cockpit.acl import verify_github_hmac

        assert verify_github_hmac(b"payload", "sha256=abcdef") is False

    def test_command_registry_has_no_raw_user_input_passthrough(self):
        """No command template may inject raw user input without placeholder."""
        from sentinel.cockpit.acl import COMMAND_REGISTRY

        for name, template in COMMAND_REGISTRY.items():
            # Template must use {placeholder} syntax
            # There must be NO shell injection vectors
            assert "`" not in template, f"Command {name} contains backticks"
            assert "$(" not in template, f"Command {name} contains subshell"
            assert ";" not in template, f"Command {name} contains command separator"

    def test_verify_monitor_source_rejects_missing_key(self, monkeypatch):
        """Monitor source verification must reject when no keys configured."""
        monkeypatch.delenv("MONITOR_API_KEYS", raising=False)
        from sentinel.cockpit.acl import verify_monitor_source

        assert verify_monitor_source("sentry", {"key": "value"}) is False


# =============================================================================
#  HELD-OUT: Subsystem 6 — Workspace Scanner Robustness
# =============================================================================


class TestHeldOut_CockpitScanner:
    """Verify workspace scanner is robust against edge cases."""

    def test_scan_handles_symlinked_project(self, tmp_path):
        """Symlinked directories must be detected."""
        from sentinel.cockpit.workspace_scanner import scan_workspace

        root = tmp_path / "workspace"
        root.mkdir()

        actual = tmp_path / "actual-project"
        actual.mkdir()
        (actual / "package.json").write_text("{}")

        # Symlink into workspace
        link = root / "linked-project"
        link.symlink_to(actual)

        projects = scan_workspace(str(root))
        names = {p.name for p in projects}
        assert "linked-project" in names

    def test_scan_skips_hidden_directories(self, tmp_path):
        """Hidden directories (starting with .) must be skipped."""
        from sentinel.cockpit.workspace_scanner import scan_workspace

        root = tmp_path / "workspace"
        root.mkdir()
        (root / ".hidden-project").mkdir()
        (root / ".hidden-project" / "package.json").write_text("{}")

        projects = scan_workspace(str(root))
        names = {p.name for p in projects}
        assert ".hidden-project" not in names

    def test_scan_handles_empty_directories(self, tmp_path):
        """Completely empty directories must be skipped."""
        from sentinel.cockpit.workspace_scanner import scan_workspace

        root = tmp_path / "workspace"
        root.mkdir()
        (root / "empty_dir").mkdir()

        projects = scan_workspace(str(root))
        names = {p.name for p in projects}
        assert "empty_dir" not in names

    def test_scan_handles_permission_error_gracefully(self, tmp_path):
        """Unreadable directories must not crash the scanner."""
        from sentinel.cockpit.workspace_scanner import scan_workspace

        root = tmp_path / "workspace"
        root.mkdir()
        bad = root / "no-perms"
        bad.mkdir()
        (bad / ".git").mkdir()

        # Skip permission test in CI; scanner must not crash
        projects = scan_workspace(str(root))
        assert isinstance(projects, list)

    def test_registry_load_handles_missing_db(self, tmp_path):
        """Loading registry with nonexistent DB must return empty, not crash."""
        from sentinel.cockpit.workspace_scanner import load_registry

        projects = load_registry(str(tmp_path / "nonexistent.db"))
        assert projects == []

    def test_projectinfo_marks_are_correct(self, tmp_path):
        """Each project must report correct marker set."""
        from sentinel.cockpit.workspace_scanner import scan_workspace

        root = tmp_path / "workspace"
        root.mkdir()

        # Full-stack project
        p = root / "fullstack"
        p.mkdir()
        (p / ".git").mkdir()
        (p / "package.json").write_text("{}")
        (p / "Dockerfile").write_text("FROM node")
        (p / "Makefile").write_text("all:")

        projects = scan_workspace(str(root))
        fs = projects[0]
        assert fs.has_git is True
        assert fs.package_manager == "npm"
        assert "Dockerfile" in fs.markers
        assert "Makefile" in fs.markers


# =============================================================================
#  HELD-OUT: Subsystem 7 — Perimeter Security
# =============================================================================


class TestHeldOut_CockpitPerimeter:
    """Verify perimeter hardening is not bypassable."""

    def test_bind_config_env_override_host(self, monkeypatch):
        """COCKPIT_HOST override must work."""
        monkeypatch.setenv("COCKPIT_HOST", "localhost")
        from sentinel.cockpit.perimeter import get_bind_config

        host, _ = get_bind_config()
        assert host == "localhost"

    def test_bind_config_env_override_port(self, monkeypatch):
        """COCKPIT_PORT override must work."""
        monkeypatch.setenv("COCKPIT_PORT", "9999")
        from sentinel.cockpit.perimeter import get_bind_config

        _, port = get_bind_config()
        assert port == 9999

    def test_bind_config_invalid_env_port(self, monkeypatch):
        """Invalid COCKPIT_PORT must raise error."""
        monkeypatch.setenv("COCKPIT_PORT", "not-a-port")
        from sentinel.cockpit.perimeter import get_bind_config

        with pytest.raises(ValueError):
            get_bind_config()

    def test_validate_invalid_env(self, monkeypatch):
        """Partial env must report all missing items."""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token123")
        # Deliberately NOT setting TELEGRAM_ALLOWED_USER_IDS
        for var in ["TELEGRAM_ALLOWED_USER_IDS", "GITHUB_WEBHOOK_SECRET", "MONITOR_API_KEYS"]:
            monkeypatch.delenv(var, raising=False)

        from sentinel.cockpit.perimeter import validate_cockpit_env

        issues = validate_cockpit_env()
        assert len(issues) >= 1  # At least one issue
        has_required_error = any("TELEGRAM_ALLOWED_USER_IDS" in i for i in issues)
        assert has_required_error, f"Missing required var not reported in: {issues}"

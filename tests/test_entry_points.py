"""Tests for C3 entry points — coordinator and watchdog daemon loops."""

from __future__ import annotations

import os
import pytest
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
#  Coordinator run() — finite-iteration testability
# ---------------------------------------------------------------------------


def test_coordinator_run_finite_iterations():
    """run() with iterations=3 completes 3 ticks and returns 3."""
    from sentinel.coordinator import HermesCoordinator, run

    coordinator = HermesCoordinator(db_path=":memory:", sentry=None)
    coordinator.initialize()

    ticks = run(coordinator, db_path=":memory:", iterations=3, sleeper=lambda _: None)
    assert ticks == 3


def test_coordinator_run_budget_exceeded_stops_advancing():
    """When sentry budget is exceeded, the coordinator logs but keeps ticking
    (it doesn't create/transition tasks — it just skips the work block)."""
    from sentinel.layers.fiscal_sentry import FiscalSentry
    from sentinel.coordinator import HermesCoordinator, run

    # Sentry with tiny budget already exceeded at init
    sentry = FiscalSentry(time_budget_seconds=120, token_budget=100)
    sentry.record_token_usage(95)  # 95/100 — 95% ≥ 90% → exceeded
    assert sentry.is_budget_exceeded()

    coordinator = HermesCoordinator(db_path=":memory:", sentry=sentry)
    coordinator.initialize()

    ticks = run(coordinator, db_path=":memory:", iterations=2, sleeper=lambda _: None)
    assert ticks == 2  # Loop keeps running, just doesn't do work


def test_coordinator_main_missing_budget_exits(monkeypatch):
    """main() exits with code 1 when HERMES_TOKEN_BUDGET is unset (C9)."""
    monkeypatch.delenv("HERMES_TOKEN_BUDGET", raising=False)

    from sentinel.coordinator import main

    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1


def test_coordinator_main_with_budget_starts_loop(monkeypatch, tmp_path):
    """main() with HERMES_TOKEN_BUDGET set starts the loop (finite via
    monkeypatched run to avoid infinite sleep)."""
    monkeypatch.setenv("HERMES_TOKEN_BUDGET", "50000")
    monkeypatch.setenv("KANBAN_DB_PATH", str(tmp_path / "kanban.db"))

    # Replace run() with a finite one
    called = []
    import sentinel.coordinator as mod

    def fake_run(coordinator, db_path, iterations=None, sleeper=None,
                 heartbeat_interval=60):
        called.append(True)
        return 1

    monkeypatch.setattr(mod, "run", fake_run)

    mod.main()
    assert called == [True]


# ---------------------------------------------------------------------------
#  Watchdog run() — finite-iteration testability
# ---------------------------------------------------------------------------


def test_watchdog_run_finite_iterations():
    """run() with iterations=2 completes 2 ticks and returns 2."""
    from sentinel.watchdog import HermesWatchdog, run

    watchdog = HermesWatchdog(poll_interval=5, sentry=None)
    ticks = run(watchdog, iterations=2, sleeper=lambda _: None)
    assert ticks == 2


def test_watchdog_run_with_sentry_polls_processes():
    """Watchdog tick calls health_check_all which polls sentry processes."""
    from sentinel.layers.fiscal_sentry import FiscalSentry
    from sentinel.watchdog import HermesWatchdog, run

    sentry = FiscalSentry(time_budget_seconds=120, token_budget=1000)
    watchdog = HermesWatchdog(poll_interval=1, sentry=sentry)

    ticks = run(watchdog, iterations=3, sleeper=lambda _: None, poll_interval=0.01)
    assert ticks == 3
    # No processes to poll, but the tick completed without error
    assert watchdog.active_process_count() == 0


def test_watchdog_main_missing_budget_exits(monkeypatch):
    """main() exits with code 1 when HERMES_TOKEN_BUDGET is unset (C9)."""
    monkeypatch.delenv("HERMES_TOKEN_BUDGET", raising=False)

    from sentinel.watchdog import main

    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1


def test_watchdog_main_with_budget_starts_loop(monkeypatch):
    """main() with HERMES_TOKEN_BUDGET set starts the loop."""
    monkeypatch.setenv("HERMES_TOKEN_BUDGET", "50000")

    called = []
    import sentinel.watchdog as mod

    def fake_run(watchdog, iterations=None, sleeper=None, poll_interval=5):
        called.append(True)
        return 1

    monkeypatch.setattr(mod, "run", fake_run)

    mod.main()
    assert called == [True]


# ---------------------------------------------------------------------------
#  H2 — sandbox path validation
# ---------------------------------------------------------------------------


def test_sandbox_bootstrap_rejects_nonexistent_target(tmp_path):
    """H2: bootstrap validates target_repo_path exists and is a git repo."""
    from sentinel.layers.sandbox_core import SandboxCore

    sb = SandboxCore()
    nonexistent = str(tmp_path / "no-such-repo")
    sandbox_dest = str(tmp_path / "worktrees" / "task-1")

    result = sb.bootstrap("task-1", nonexistent, sandbox_dest)
    assert result.success is False
    assert "not found" in result.error.lower() or "no such" in result.error.lower()


def test_sandbox_bootstrap_rejects_non_git_dir(tmp_path):
    """H2: bootstrap requires .git in target."""
    from sentinel.layers.sandbox_core import SandboxCore

    sb = SandboxCore()
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    sandbox_dest = str(tmp_path / "worktrees" / "task-2")

    result = sb.bootstrap("task-2", str(not_a_repo), sandbox_dest)
    assert result.success is False
    assert "git" in result.error.lower()

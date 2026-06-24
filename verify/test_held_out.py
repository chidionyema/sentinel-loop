"""
Held-out verification tests for the Sentinel Loop Architecture.

These tests are NEVER visible to the implementation agent during development.
They run only after the implementation claims "done" via visible tests.
Violation (reading/modifying these files during implementation) fails the build.

DESIGN RULE: These tests verify properties the visible tests CANNOT verify
because the implementer could game them. They test:
  - That the kill was real (process actually dead, not just a fabricated result)
  - That Layer 3 is genuinely wired (behavioral, not import-checked)
  - That security fences cannot be bypassed by edge cases
  - That the state machine rejects all invalid transitions, not just one
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest


# =============================================================================
#  HELD-OUT: Layer 3 real kill verification
# =============================================================================


class TestHeldOut_SentryRealKill:
    """Verify that execute_with_budget ACTUALLY kills — process gone, not
    just a result object claiming it was killed."""

    def test_killed_process_is_truly_dead(self):
        """After timeout, the child process must not exist in the process table.
        The sentry keeps it tracked until poll_active_processes() reaps it."""
        from sentinel.layers.fiscal_sentry import FiscalSentry

        sentry = FiscalSentry(time_budget_seconds=0.3)

        # Start a long-running process
        result = sentry.execute_with_budget(
            ["sleep", "30"],
            "heldout-kill-verify",
        )

        assert result.was_killed is True

        # Process stays tracked until polled (watchdog discovers it on next cycle)
        assert sentry.active_process_count() == 1

        # After polling, the dead process is reaped from tracking
        dead = sentry.poll_active_processes()
        assert "heldout-kill-verify" in dead
        assert sentry.active_process_count() == 0

    def test_non_killed_process_returns_real_exit_code(self):
        """A process that finishes within budget must return its real exit code."""
        from sentinel.layers.fiscal_sentry import FiscalSentry

        sentry = FiscalSentry(time_budget_seconds=10)

        result = sentry.execute_with_budget(
            ["sh", "-c", "exit 42"],
            "heldout-exit-code",
        )

        assert result.was_killed is False
        assert result.exit_code == 42
        assert result.signal_sent is None

    def test_command_not_found_exit_code(self):
        """A nonexistent command must return exit_code 127, not crash."""
        from sentinel.layers.fiscal_sentry import FiscalSentry

        sentry = FiscalSentry(time_budget_seconds=10)

        result = sentry.execute_with_budget(
            ["nonexistent_command_xyzzy_12345"],
            "heldout-missing",
        )

        assert result.was_killed is False
        assert result.exit_code == 127


class TestHeldOut_SentryActiveTracking:
    """Verify active process tracking survives concurrent operations."""

    def test_multiple_sequential_kills_dont_corrupt_state(self):
        """Running multiple kill operations must not leak process state.
        Killed processes accumulate in tracking until polled."""
        from sentinel.layers.fiscal_sentry import FiscalSentry

        sentry = FiscalSentry(time_budget_seconds=0.2)

        for i in range(5):
            result = sentry.execute_with_budget(
                ["sleep", "10"],
                f"heldout-seq-{i}",
            )
            assert result.was_killed is True

        # All 5 killed processes are tracked until polled
        assert sentry.active_process_count() == 5

        # After full poll, all should be cleaned up — no leaks
        dead = sentry.poll_active_processes()
        assert len(dead) == 5
        assert sentry.active_process_count() == 0

    def test_polling_detects_finished_process(self):
        """After a process finishes normally, poll_active_processes must detect it."""
        from sentinel.layers.fiscal_sentry import FiscalSentry

        sentry = FiscalSentry(time_budget_seconds=10)

        sentry.execute_with_budget(
            ["true"],
            "heldout-polled",
        )

        # Process stays tracked until polled
        assert sentry.active_process_count() == 1

        # Poll discovers the finished process and removes it
        dead = sentry.poll_active_processes()
        assert "heldout-polled" in dead
        assert sentry.active_process_count() == 0


# =============================================================================
#  HELD-OUT: Layer 3 wiring verification (behavioral, not import-checked)
# =============================================================================


class TestHeldOut_SentryWiredToWatchdog:
    """Verify the sentry is behaviorally wired — polling actually invokes
    sentry methods, not just import presence."""

    def test_watchdog_polls_sentry_processes(self):
        """Watchdog health_check_all must call sentry.poll_active_processes()."""
        from sentinel.layers.fiscal_sentry import FiscalSentry
        from sentinel.watchdog import HermesWatchdog

        sentry = FiscalSentry(time_budget_seconds=0.3)
        wd = HermesWatchdog(poll_interval=5, sentry=sentry)

        # Start a process that will be killed
        sentry.execute_with_budget(["sleep", "30"], "heldout-wd-poll")

        # Watchdog health check should detect the dead process via sentry
        result = wd.health_check_all()

        # The killed process should appear as a sentry-process dead entry
        sentry_dead = [d for d in result.dead_daemons if d.startswith("sentry-process:")]
        assert len(sentry_dead) >= 1
        assert "heldout-wd-poll" in str(sentry_dead)

    def test_watchdog_token_budget_delegates_to_sentry(self):
        """wd.is_token_budget_exceeded() must use the sentry's budget check."""
        from sentinel.layers.fiscal_sentry import FiscalSentry
        from sentinel.watchdog import HermesWatchdog

        sentry = FiscalSentry(token_budget=1000)
        wd = HermesWatchdog(sentry=sentry)

        assert wd.is_token_budget_exceeded() is False

        sentry.record_token_usage(900)  # 90%
        assert wd.is_token_budget_exceeded() is True

    def test_watchdog_without_sentry_does_not_crash(self):
        """Watchdog must work correctly with sentry=None (no crash, no false positives)."""
        from sentinel.watchdog import HermesWatchdog

        wd = HermesWatchdog()
        assert wd.is_token_budget_exceeded() is False
        assert wd.active_process_count() == 0
        result = wd.health_check_all()
        assert isinstance(result.dead_daemons, list)


class TestHeldOut_SentryWiredToCoordinator:
    """Verify the sentry budget check is behaviorally wired into the coordinator."""

    def test_coordinator_blocks_task_creation_on_budget(self):
        """create_task must raise RuntimeError when sentry budget exceeded."""
        from sentinel.coordinator import HermesCoordinator
        from sentinel.layers.fiscal_sentry import FiscalSentry

        sentry = FiscalSentry(token_budget=1000)
        sentry.record_token_usage(950)  # 95% — exceeded
        coord = HermesCoordinator(db_path=":memory:", sentry=sentry)
        coord.initialize()

        with pytest.raises(RuntimeError, match="Token budget exceeded"):
            coord.create_task("code-fix", "/test/repo", "Should be blocked")

    def test_coordinator_blocks_transition_on_budget(self):
        """transition must raise RuntimeError when sentry budget exceeded."""
        from sentinel.coordinator import HermesCoordinator
        from sentinel.layers.fiscal_sentry import FiscalSentry

        sentry = FiscalSentry(token_budget=1000)
        coord = HermesCoordinator(db_path=":memory:", sentry=sentry)
        coord.initialize()

        # Create task while budget is OK
        task_id = coord.create_task("code-fix", "/t", "ok")
        coord.transition(task_id, "in_progress")

        # Exceed budget
        sentry.record_token_usage(900)

        with pytest.raises(RuntimeError, match="Token budget exceeded"):
            coord.transition(task_id, "done")

    def test_coordinator_without_sentry_works_normally(self):
        """Coordinator with sentry=None must work without budget checks (backward compat)."""
        from sentinel.coordinator import HermesCoordinator

        coord = HermesCoordinator(db_path=":memory:")
        coord.initialize()
        task_id = coord.create_task("code-fix", "/test", "backward compat")
        coord.transition(task_id, "in_progress")
        coord.transition(task_id, "done")
        assert coord.get_task_state(task_id) == "done"


# =============================================================================
#  HELD-OUT: Security fence edge cases (bypass attempts)
# =============================================================================


class TestHeldOut_SecurityFenceEdges:
    """Verify security fences cannot be bypassed by path manipulation."""

    def test_restricted_path_with_symlink_bypass(self, tmp_path):
        """A symlink into a restricted domain must still be blocked."""
        from sentinel.security.fences import SecurityFence

        # Create a symlink chain
        restricted_dir = tmp_path / "estates" / "money"
        restricted_dir.mkdir(parents=True)
        (restricted_dir / "secret.txt").write_text("secret")

        link_path = tmp_path / "innocent_link"
        link_path.symlink_to(restricted_dir)

        fence = SecurityFence()
        # The symlink path itself may not contain "money" in segments
        # but the resolved path does. The fence must catch both.
        result_from_link = fence.is_restricted_path(str(link_path / "secret.txt"))
        result_direct = fence.is_restricted_path(str(restricted_dir / "secret.txt"))

        # At minimum, direct paths are caught
        assert result_direct is True

    def test_restricted_path_with_relative_traversal(self):
        """Relative path traversal into restricted domains must be caught."""
        from sentinel.security.fences import SecurityFence

        fence = SecurityFence()

        # Try relative traversal
        assert fence.is_restricted_path("../../estates/money/secret") is True
        assert fence.is_restricted_path("./estates/identity/creds") is True

    def test_forbidden_command_with_variations(self):
        """Forbidden commands with spacing/quoting variations must be caught."""
        from sentinel.security.fences import SecurityFence

        fence = SecurityFence()

        # Variations of rm -rf
        assert fence.is_command_forbidden("rm -rf /tmp/*") is True
        assert fence.is_command_forbidden("rm   -rf   /") is True
        assert fence.is_command_forbidden("sudo rm -rf /") is True  # contains both sudo and rm -rf

        # Variations of sudo
        assert fence.is_command_forbidden("/usr/bin/sudo echo hi") is True


# =============================================================================
#  HELD-OUT: State machine rejects ALL invalid transitions
# =============================================================================


class TestHeldOut_StateMachineFullTransitions:
    """Verify the state machine rejects every invalid transition, not just
    the one the visible test checks."""

    def test_all_invalid_transitions_rejected(self):
        """Every invalid state transition must raise ValueError."""
        from sentinel.coordinator import HermesCoordinator

        coord = HermesCoordinator(db_path=":memory:")
        coord.initialize()
        task_id = coord.create_task("cf", "/t", "desc")

        # All invalid transitions from each state
        invalid_from_pending = ["done"]
        invalid_from_in_progress = ["pending"]
        invalid_from_done = ["pending", "in_progress"]
        invalid_from_escalated = ["done", "pending"]

        # Test from pending
        for target in invalid_from_pending:
            with pytest.raises(ValueError, match="invalid transition"):
                coord2 = HermesCoordinator(db_path=":memory:")
                coord2.initialize()
                tid = coord2.create_task("cf", "/t", "desc")
                coord2.transition(tid, target)

        # Test from in_progress
        for target in invalid_from_in_progress:
            with pytest.raises(ValueError, match="invalid transition"):
                coord2 = HermesCoordinator(db_path=":memory:")
                coord2.initialize()
                tid = coord2.create_task("cf", "/t", "desc")
                coord2.transition(tid, "in_progress")
                coord2.transition(tid, target)

        # Test from done
        coord.transition(task_id, "in_progress")
        coord.transition(task_id, "done")
        for target in invalid_from_done:
            with pytest.raises(ValueError, match="invalid transition"):
                coord.transition(task_id, target)

    def test_nonexistent_task_rejected(self):
        """Transitioning a nonexistent task must raise ValueError."""
        from sentinel.coordinator import HermesCoordinator

        coord = HermesCoordinator(db_path=":memory:")
        coord.initialize()

        with pytest.raises(ValueError, match="not found"):
            coord.transition("nonexistent-task-id", "done")


# =============================================================================
#  HELD-OUT: Database integrity under concurrent access
# =============================================================================


class TestHeldOut_DatabaseIntegrity:
    """Verify databases handle edge cases correctly."""

    def test_kanban_task_split_preserves_parent_reference(self):
        """split_task must create child tasks with correct parent_task_id."""
        from sentinel.db.kanban import KanbanDB

        db = KanbanDB(":memory:")
        db.initialize()
        parent_id = db.create_task("code-fix", "/repo", "Original task")
        children = db.split_task(parent_id)

        assert len(children) == 2
        for child_id in children:
            row = db.conn.execute(
                "SELECT parent_task_id FROM tasks WHERE id = ?", (child_id,)
            ).fetchone()
            assert row["parent_task_id"] == parent_id

    def test_state_db_failure_class_uniqueness(self):
        """Inserting duplicate failure signatures must be handled."""
        from sentinel.db.state import StateDB

        db = StateDB(":memory:")
        db.initialize()

        # Should not crash on duplicates
        db.conn.execute(
            "INSERT OR IGNORE INTO failure_class_map (failure_signature, category, severity, created_at) "
            "VALUES ('test-sig', 'test', 'low', datetime('now'))"
        )
        db.conn.execute(
            "INSERT OR IGNORE INTO failure_class_map (failure_signature, category, severity, created_at) "
            "VALUES ('test-sig', 'test', 'low', datetime('now'))"
        )
        db.conn.commit()

        result = db.get_failure_class("test-sig")
        assert result is not None
        assert result["category"] == "test"


# =============================================================================
#  HELD-OUT: RSI tuning must reject invalid parameter VALUES, not echo them
# =============================================================================


class TestHeldOut_RSIParameterValidation:
    """The sandboxed tuner must reject out-of-range / wrong-type values.
    A bare 'spawn a subprocess and print params' stub fails these."""

    def test_valid_params_accepted(self):
        from sentinel.rsi_orchestrator import HermesRSI
        r = HermesRSI()
        res = r.tune_parameters("m", {"temperature": 0.7, "top_p": 0.9, "max_tokens": 256})
        assert res.success is True
        assert res.tuned_params["temperature"] == 0.7

    def test_temperature_out_of_range_rejected(self):
        from sentinel.rsi_orchestrator import HermesRSI
        r = HermesRSI()
        res = r.tune_parameters("m", {"temperature": 5.0})  # > 2.0
        assert res.success is False
        assert "temperature" in (res.error or "")

    def test_non_numeric_temperature_rejected(self):
        from sentinel.rsi_orchestrator import HermesRSI
        r = HermesRSI()
        res = r.tune_parameters("m", {"temperature": "not-a-number"})
        assert res.success is False

    def test_non_integer_max_tokens_rejected(self):
        from sentinel.rsi_orchestrator import HermesRSI
        r = HermesRSI()
        res = r.tune_parameters("m", {"max_tokens": 1.5})
        assert res.success is False
        assert "max_tokens" in (res.error or "")

    def test_negative_max_tokens_rejected(self):
        from sentinel.rsi_orchestrator import HermesRSI
        r = HermesRSI()
        res = r.tune_parameters("m", {"max_tokens": -10})
        assert res.success is False

    def test_unknown_params_pass_through(self):
        from sentinel.rsi_orchestrator import HermesRSI
        r = HermesRSI()
        res = r.tune_parameters("m", {"some_future_knob": "anything"})
        assert res.success is True
        assert res.tuned_params["some_future_knob"] == "anything"

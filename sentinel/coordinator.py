"""Hermes Coordinator - Single-writer lane parsing kanban.db and advancing task state transitions.

Wired to FiscalSentry for token budget enforcement before advancing tasks.

Entry point (C3): ``python -m sentinel.coordinator`` runs a long-lived tick
loop with a real (non-None) token_budget.  The loop currently performs
heartbeat + budget-gate only — honest gap: KanbanDB does not yet expose a
``pending_tasks()`` scan method, so there is no task-advancement cycle.
When that method is added, the loop body grows one line to call it.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from sentinel.db.kanban import KanbanDB

if TYPE_CHECKING:
    from sentinel.layers.playbook_registry import PlaybookRegistry
    from sentinel.layers.fiscal_sentry import FiscalSentry


@dataclass
class ValidationResult:
    state: str
    failure_signature: str | None = None


class HermesCoordinator:
    """Single-writer lane parsing kanban.db and advancing task state transitions."""

    DAEMON_NAME = "ai.hermes.coordinator"
    is_db_writer = True

    def __init__(self, db_path: str = ":memory:", sentry: "FiscalSentry | None" = None):
        self.db = KanbanDB(db_path)
        self._initialized = False
        self._sentry = sentry

    def initialize(self) -> None:
        self.db.initialize()
        self._initialized = True

    def wire_sentry(self, sentry: "FiscalSentry") -> None:
        """Wire the FiscalSentry for token budget enforcement."""
        self._sentry = sentry

    def create_task(self, task_type: str, repo_path: str, description: str) -> str:
        # Check token budget before creating new work
        if self._sentry and self._sentry.is_budget_exceeded():
            raise RuntimeError(
                f"Token budget exceeded ({self._sentry.tokens_used}/{self._sentry.token_budget}). "
                "Cannot create new tasks."
            )
        return self.db.create_task(task_type, repo_path, description)

    def transition(self, task_id: str, to_state: str, reason: str = "") -> None:
        # Check token budget before advancing state
        if self._sentry and self._sentry.is_budget_exceeded():
            raise RuntimeError(
                f"Token budget exceeded ({self._sentry.tokens_used}/{self._sentry.token_budget}). "
                "Cannot transition tasks."
            )
        self.db.transition(task_id, to_state, reason)

    def get_task_state(self, task_id: str) -> str:
        return self.db.get_task_state(task_id)

    def validate_and_advance(self, task_id: str, playbooks_path: str) -> ValidationResult:
        """Validate task against playbook registry and advance state."""
        from sentinel.layers.playbook_registry import PlaybookRegistry

        # Check token budget before validation
        if self._sentry and self._sentry.is_budget_exceeded():
            self.transition(task_id, "escalated", "token-budget-exceeded")
            return ValidationResult(
                state="escalated",
                failure_signature="token-budget-exceeded",
            )

        task_type = self._get_task_type(task_id)
        registry = PlaybookRegistry(playbooks_path=playbooks_path)
        result = registry.validate_task_playbook(task_type, {"task_id": task_id})

        if not result.is_valid and result.escalated:
            self.transition(task_id, "escalated", result.failure_signature)
            return ValidationResult(
                state="escalated",
                failure_signature=result.failure_signature,
            )

        self.transition(task_id, "in_progress", "playbook validated")
        return ValidationResult(state="in_progress")

    def _get_task_type(self, task_id: str) -> str:
        row = self.db.conn.execute("SELECT task_type FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise ValueError(f"Task not found: {task_id}")
        return row["task_type"]


# ---------------------------------------------------------------------------
#  Daemon entry point (C3)
# ---------------------------------------------------------------------------


def run(
    coordinator: HermesCoordinator,
    db_path: str = ":memory:",
    iterations: int | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    heartbeat_interval: float = 60.0,
) -> int:
    """Run the coordinator tick loop. Returns the number of ticks completed.

    Each tick: check sentry budget, emit heartbeat, then sleep.

    Args:
        coordinator: A wired HermesCoordinator instance.
        db_path: Path to kanban.db (for logging).
        iterations: Finite number of ticks for testing; None = forever.
        sleeper: Injectable sleep (tests use a no-op or short sleep).
        heartbeat_interval: Seconds between ticks.
    """
    tick = 0
    while iterations is None or tick < iterations:
        tick += 1

        if coordinator._sentry and coordinator._sentry.is_budget_exceeded():
            print(
                f"[coordinator] tick={tick} BUDGET-EXCEEDED "
                f"({coordinator._sentry.tokens_used}/{coordinator._sentry.token_budget})",
                file=sys.stderr,
            )
            sleeper(heartbeat_interval)
            continue

        # ── heartbeat ──────────────────────────────────────────────
        # HONEST GAP: no task-scan method exists on KanbanDB yet.
        # When it does, add one line here:
        #   for task in coordinator.db.pending_tasks():
        #       coordinator.validate_and_advance(task.id, playbooks_path)
        print(f"[coordinator] tick={tick} heartbeat db={db_path}")
        sys.stdout.flush()

        sleeper(heartbeat_interval)

    return tick


def main() -> None:
    """Production entry point — reads config from environment.

    Required env vars:
        KANBAN_DB_PATH      — path to kanban.db (default ~/.hermes/kanban.db)
        HERMES_TOKEN_BUDGET — integer token budget for FiscalSentry
    """
    from sentinel.layers.fiscal_sentry import FiscalSentry

    db_path = os.environ.get(
        "KANBAN_DB_PATH",
        os.path.expanduser("~/.hermes/kanban.db"),
    )

    token_budget_str = os.environ.get("HERMES_TOKEN_BUDGET", "")
    if not token_budget_str:
        print(
            "FATAL: HERMES_TOKEN_BUDGET not set. "
            "The coordinator MUST have an explicit token budget (C9).",
            file=sys.stderr,
        )
        sys.exit(1)
    token_budget = int(token_budget_str)

    sentry = FiscalSentry(
        time_budget_seconds=120,
        token_budget=token_budget,
    )

    coordinator = HermesCoordinator(db_path=db_path, sentry=sentry)
    coordinator.initialize()

    print(f"[coordinator] starting — db={db_path} budget={token_budget}")
    run(coordinator, db_path=db_path)


if __name__ == "__main__":
    main()

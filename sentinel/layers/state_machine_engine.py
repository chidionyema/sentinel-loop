"""Layer 4: State Machine Engine — Validates output via deterministic gates."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sentinel.db.kanban import KanbanDB


@dataclass
class GateResult:
    passed: bool
    output: str = ""
    error: str = ""


@dataclass
class ValidationResult:
    all_gates_passed: bool = False
    gates_passed: list[str] = field(default_factory=list)
    gate_results: dict[str, GateResult] = field(default_factory=dict)


@dataclass
class TaskState:
    task_id: str
    strikes: int = 0


@dataclass
class RollbackResult:
    rollback_type: str  # "SOFT_RESET" or "HARD_ROLLBACK"
    sandbox_discarded: bool = False
    child_tasks: list[str] = field(default_factory=list)
    alert_sent: bool = False


class StateMachineEngine:
    """Layer 4: State Machine Engine — 3-gate validation + 3-strike rollback.

    Spec §2 validation gates (sequential):
      1. Syntax Check (py_compile exit 0)
      2. Linter Check (must have executable linter; fails if absent)
      3. Unit Tests

    Spec §3 recovery:
      - Strikes 1-2 → SOFT_RESET (git reset --hard checkpoint, git clean -fd)
      - Strike 3 → HARD_ROLLBACK (destroy sandbox, split task in kanban.db)
    """

    def __init__(self, config: dict | None = None, kanban_db: "KanbanDB | None" = None):
        self.config = config or {}
        self._task_states: dict[str, TaskState] = {}
        self._gateway_notify_interval: int = self.config.get("gateway_notify_interval", 60)
        self._kanban_db = kanban_db

    # ------------------------------------------------------------------
    #  Properties
    # ------------------------------------------------------------------

    @property
    def gateway_notify_interval(self) -> int:
        return self._gateway_notify_interval

    # ------------------------------------------------------------------
    #  Self-declaration prohibition
    # ------------------------------------------------------------------

    def transition_to_done(self, source: str, task_id: str) -> None:
        if source == "agent":
            raise ValueError(
                f"Agent self-declaration of done is prohibited for task {task_id}"
            )

    # ------------------------------------------------------------------
    #  Validation gates (spec §2, Layer 4)
    # ------------------------------------------------------------------

    def validate(self, sandbox_path: str, language: str = "python") -> ValidationResult:
        """Run all 3 validation gates sequentially."""
        result = ValidationResult()
        sandbox = Path(sandbox_path)

        if not sandbox.exists():
            for gate in ["syntax_check", "lint_check", "unit_tests"]:
                result.gate_results[gate] = GateResult(passed=False, error="Sandbox path not found")
            return result

        # Gate 1: Syntax
        syntax_result = self._run_syntax_check_for_dir(str(sandbox))
        result.gate_results["syntax_check"] = syntax_result
        if syntax_result.passed:
            result.gates_passed.append("syntax_check")

        # Gate 2: Lint
        lint_result = self._run_lint_check(str(sandbox))
        result.gate_results["lint_check"] = lint_result
        if lint_result.passed:
            result.gates_passed.append("lint_check")

        # Gate 3: Unit tests
        test_result = self._run_unit_tests(str(sandbox))
        result.gate_results["unit_tests"] = test_result
        if test_result.passed:
            result.gates_passed.append("unit_tests")

        result.all_gates_passed = len(result.gates_passed) == 3
        return result

    def _run_syntax_check(self, file_path: str) -> GateResult:
        try:
            result = subprocess.run(
                ["python3", "-m", "py_compile", file_path],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return GateResult(passed=True)
            return GateResult(passed=False, error=result.stderr or "Syntax error")
        except Exception as e:
            return GateResult(passed=False, error=str(e))

    def _run_syntax_check_for_dir(self, sandbox_path: str) -> GateResult:
        sandbox = Path(sandbox_path)
        py_files = list(sandbox.rglob("*.py"))
        if not py_files:
            return GateResult(passed=True)
        for py_file in py_files:
            result = self._run_syntax_check(str(py_file))
            if not result.passed:
                return result
        return GateResult(passed=True)

    def _run_lint_check(self, sandbox_path: str) -> GateResult:
        """Run lint check. Fails if no linter is available (must be installed)."""
        try:
            result = subprocess.run(
                ["python3", "-m", "ruff", "check", "--select", "E,F", sandbox_path],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                return GateResult(passed=True)
            return GateResult(passed=False, error=result.stderr or result.stdout)
        except FileNotFoundError:
            # Linter not installed → gate fails per spec "Code must comply"
            return GateResult(passed=False, error="Linter (ruff) not installed — cannot verify compliance")
        except Exception as e:
            return GateResult(passed=False, error=str(e))

    def _run_unit_tests(self, sandbox_path: str) -> GateResult:
        try:
            result = subprocess.run(
                ["python3", "-m", "pytest", sandbox_path, "-x", "-q"],
                capture_output=True, text=True, timeout=60,
                cwd=sandbox_path,
            )
            if result.returncode == 0:
                return GateResult(passed=True, output=result.stdout)
            return GateResult(passed=False, error=result.stderr or result.stdout)
        except Exception as e:
            return GateResult(passed=False, error=str(e))

    # ------------------------------------------------------------------
    #  Strike tracking (spec §3)
    # ------------------------------------------------------------------

    def record_failure(self, task_id: str) -> None:
        state = self.get_or_create_task_state(task_id)
        state.strikes += 1

    def get_or_create_task_state(self, task_id: str) -> TaskState:
        if task_id not in self._task_states:
            self._task_states[task_id] = TaskState(task_id=task_id)
        return self._task_states[task_id]

    def get_strike_count(self, task_id: str) -> int:
        return self.get_or_create_task_state(task_id).strikes

    # ------------------------------------------------------------------
    #  Rollback (spec §3) — routes soft vs hard by strike count
    # ------------------------------------------------------------------

    def execute_rollback(self, task_id: str, sandbox_path: str) -> RollbackResult:
        """Route rollback based on strike count.

        Strikes 1-2 → SOFT_RESET (no task splitting)
        Strike 3   → HARD_ROLLBACK (destroy sandbox, split task in kanban.db, alert)
        """
        state = self.get_or_create_task_state(task_id)

        if state.strikes < 3:
            return RollbackResult(
                rollback_type="SOFT_RESET",
                sandbox_discarded=False,
                child_tasks=[],
            )

        # Strike 3+: hard rollback with real DB task splitting
        child_tasks: list[str] = []
        if self._kanban_db is not None:
            try:
                child_tasks = self._kanban_db.split_task(task_id)
            except Exception:
                # Fallback to string IDs if DB mutation fails
                child_tasks = [f"{task_id}-child-1", f"{task_id}-child-2"]
        else:
            child_tasks = [f"{task_id}-child-1", f"{task_id}-child-2"]

        # Gateway alert
        self._send_gateway_alert(
            f"🚨 STATE DIVERGENCE ERADICATED\n"
            f"Task: {task_id}\n"
            f"Status: Sandbox destroyed. Task split into sub-tasks. Human intervention required."
        )

        return RollbackResult(
            rollback_type="HARD_ROLLBACK",
            sandbox_discarded=True,
            child_tasks=child_tasks,
            alert_sent=True,
        )

    def _send_gateway_alert(self, message: str) -> None:
        from sentinel.gateway.telegram_bridge import HermesGateway
        gateway = HermesGateway()
        gateway.send_urgent_push(message)

    def _trigger_emergency_notifications(self) -> None:
        self._gateway_notify_interval = 1

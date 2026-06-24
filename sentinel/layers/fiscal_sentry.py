"""Layer 3: Fiscal & Health Sentry - Real-time token, time, and cost limitations.

Enforces:
  - Max 120s execution time per process via SIGKILL to process group
  - 90% token budget halt
  - 5-second polling interval
"""

from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass


@dataclass
class ExecutionResult:
    """Result of a budgeted subprocess execution. Plain dataclass, not Exception."""
    was_killed: bool = False
    signal_sent: int | None = None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""


@dataclass
class ActiveProcess:
    """An actively monitored subprocess."""
    name: str
    pid: int
    pgid: int
    proc: subprocess.Popen


class FiscalSentry:
    """Layer 3: Fiscal & Health Sentry enforces time budget and token limits.

    Runs commands via subprocess.Popen(start_new_session=True).
    On timeout: os.killpg(pgid, SIGKILL) then proc.wait() for real reaping.
    """

    def __init__(self, time_budget_seconds: float = 120,
                 token_budget: int | None = None,
                 cost_per_1k: float = 0.002,
                 polling_interval: int = 5):
        self.time_budget_seconds = time_budget_seconds
        self.token_budget = token_budget
        self.cost_per_1k = cost_per_1k
        self.polling_interval = polling_interval
        self._tokens_used: int = 0
        self._active_processes: dict[str, ActiveProcess] = {}

    # ------------------------------------------------------------------
    #  Subprocess execution with real SIGKILL enforcement
    # ------------------------------------------------------------------

    def execute_with_budget(self, argv: list[str], process_name: str,
                            cwd: str | None = None,
                            env: dict | None = None) -> ExecutionResult:
        """Run a command via subprocess with a strict time budget.

        On timeout, sends SIGKILL to the entire process group and reaps.
        Returns an ExecutionResult with real was_killed / signal_sent / exit_code.
        """
        try:
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                cwd=cwd,
                env=env,
            )

            # Track for external polling (watchdog)
            pgid = os.getpgid(proc.pid)
            self._active_processes[process_name] = ActiveProcess(
                name=process_name,
                pid=proc.pid,
                pgid=pgid,
                proc=proc,
            )

            try:
                stdout, stderr = proc.communicate(timeout=self.time_budget_seconds)
                return ExecutionResult(
                    was_killed=False,
                    exit_code=proc.returncode,
                    stdout=stdout.decode("utf-8", errors="replace") if stdout else "",
                    stderr=stderr.decode("utf-8", errors="replace") if stderr else "",
                )
            except subprocess.TimeoutExpired:
                # Real SIGKILL to the process group
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass  # Already dead
                except PermissionError:
                    pass  # Can't kill (test env?)

                # Reap the process
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()

                # Keep in _active_processes so watchdog.poll_active_processes()
                # discovers the dead process on next health check cycle.
                return ExecutionResult(
                    was_killed=True,
                    signal_sent=signal.SIGKILL,
                    exit_code=-9,
                )

        except FileNotFoundError:
            return ExecutionResult(
                was_killed=False,
                exit_code=127,
                stderr=f"Command not found: {argv[0]}",
            )
        except Exception as e:
            return ExecutionResult(
                was_killed=False,
                exit_code=-1,
                stderr=str(e),
            )

    # ------------------------------------------------------------------
    #  Token budget enforcement
    # ------------------------------------------------------------------

    def record_token_usage(self, tokens: int) -> None:
        self._tokens_used += tokens

    def is_budget_exceeded(self) -> bool:
        """True when token usage >= 90% of allocated budget."""
        if self.token_budget is None:
            return False
        return self._tokens_used >= int(self.token_budget * 0.9)

    @property
    def tokens_used(self) -> int:
        return self._tokens_used

    @property
    def budget_remaining(self) -> int:
        if self.token_budget is None:
            return -1
        return max(0, self.token_budget - self._tokens_used)

    # ------------------------------------------------------------------
    #  Active process polling (called by watchdog every 5 seconds)
    # ------------------------------------------------------------------

    def poll_active_processes(self) -> list[str]:
        """Poll all tracked processes. Returns names of any that have died."""
        dead: list[str] = []
        for name, ap in list(self._active_processes.items()):
            poll_result = ap.proc.poll()
            if poll_result is not None:
                dead.append(name)
                self._active_processes.pop(name, None)
        return dead

    def active_process_count(self) -> int:
        return len(self._active_processes)

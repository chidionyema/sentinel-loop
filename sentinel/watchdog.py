"""Hermes Watchdog - Health checking, execution budget policing, process reaping, and hard rollbacks.

Wired to FiscalSentry for:
  - 5-second polling of active subprocesses
  - Token budget enforcement via sentry coordination

Entry point (C3): ``python -m sentinel.watchdog`` runs a long-lived tick
loop doing health_check_all() + sentry poll + heartbeat.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from sentinel.layers.fiscal_sentry import FiscalSentry


@dataclass
class HealthCheckResult:
    dead_daemons: list[str] = field(default_factory=list)
    recycle_triggered: bool = False
    is_alive: bool = True


class HermesWatchdog:
    """Health checking, execution budget policing, process reaping, and hard rollbacks."""

    DAEMON_NAME = "ai.hermes.watchdog"

    def __init__(self, poll_interval: int = 5, sentry: "FiscalSentry | None" = None):
        self.poll_interval = poll_interval
        self._daemon_pids: dict[str, int] = {}
        self._sentry = sentry

    @property
    def sentry(self) -> "FiscalSentry | None":
        return self._sentry

    def wire_sentry(self, sentry: "FiscalSentry") -> None:
        """Wire the FiscalSentry into the watchdog for budget policing and process polling."""
        self._sentry = sentry

    def register_daemon_pid(self, daemon_name: str, pid: int) -> None:
        self._daemon_pids[daemon_name] = pid

    def check_daemon_health(self, daemon_name: str) -> HealthCheckResult:
        """Check if a specific daemon is alive by PID."""
        result = HealthCheckResult()
        pid = self._daemon_pids.get(daemon_name)

        if pid is None:
            result.dead_daemons.append(daemon_name)
            result.recycle_triggered = True
            result.is_alive = False
            return result

        try:
            os.kill(pid, 0)
            result.is_alive = True
        except (OSError, ProcessLookupError):
            result.dead_daemons.append(daemon_name)
            result.recycle_triggered = True
            result.is_alive = False

        return result

    def health_check_all(self) -> HealthCheckResult:
        """Check health of all registered daemons."""
        result = HealthCheckResult()
        for daemon_name in self._daemon_pids:
            check = self.check_daemon_health(daemon_name)
            result.dead_daemons.extend(check.dead_daemons)
            if check.recycle_triggered:
                result.recycle_triggered = True

        # ---- Layer 3 integration: poll active sentry processes ----
        if self._sentry is not None:
            dead = self._sentry.poll_active_processes()
            for name in dead:
                result.dead_daemons.append(f"sentry-process:{name}")
                result.recycle_triggered = True

        return result

    def is_token_budget_exceeded(self) -> bool:
        """Check if the sentry's token budget has been exceeded."""
        if self._sentry is None:
            return False
        return self._sentry.is_budget_exceeded()

    def active_process_count(self) -> int:
        """Number of processes tracked by the sentry."""
        if self._sentry is None:
            return 0
        return self._sentry.active_process_count()


# ---------------------------------------------------------------------------
#  Daemon entry point (C3)
# ---------------------------------------------------------------------------


def run(
    watchdog: HermesWatchdog,
    iterations: int | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    poll_interval: float = 5.0,
) -> int:
    """Run the watchdog tick loop. Returns the number of ticks completed.

    Each tick: health_check_all() + heartbeat.

    Args:
        watchdog: A wired HermesWatchdog instance.
        iterations: Finite number of ticks for testing; None = forever.
        sleeper: Injectable sleep (tests use a no-op or short sleep).
        poll_interval: Seconds between ticks.
    """
    tick = 0
    while iterations is None or tick < iterations:
        tick += 1

        result = watchdog.health_check_all()

        # ── heartbeat ──────────────────────────────────────────────
        status = "ALIVE" if result.is_alive else "DEAD-DAEMONS"
        dead = ",".join(result.dead_daemons) if result.dead_daemons else "none"
        print(
            f"[watchdog] tick={tick} status={status} dead=[{dead}] "
            f"active-procs={watchdog.active_process_count()}"
        )
        sys.stdout.flush()

        sleeper(poll_interval)

    return tick


def main() -> None:
    """Production entry point — reads config from environment.

    Required env vars:
        HERMES_TOKEN_BUDGET — integer token budget for FiscalSentry (closes C9)
    """
    from sentinel.layers.fiscal_sentry import FiscalSentry

    token_budget_str = os.environ.get("HERMES_TOKEN_BUDGET", "")
    if not token_budget_str:
        print(
            "FATAL: HERMES_TOKEN_BUDGET not set. "
            "The watchdog MUST have an explicit token budget (C9).",
            file=sys.stderr,
        )
        sys.exit(1)
    token_budget = int(token_budget_str)

    sentry = FiscalSentry(
        time_budget_seconds=120,
        token_budget=token_budget,
    )

    watchdog = HermesWatchdog(poll_interval=5, sentry=sentry)

    print(f"[watchdog] starting — budget={token_budget} poll={watchdog.poll_interval}s")
    run(watchdog, poll_interval=watchdog.poll_interval)


if __name__ == "__main__":
    main()

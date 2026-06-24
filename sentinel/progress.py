"""Hermes Progress - Telemetry tracking, token consumption monitoring (loop-cost), and structural audits."""

from __future__ import annotations

from dataclasses import dataclass, field

from sentinel.db.coordinator import CoordinatorDB


@dataclass
class AuditResult:
    tables_intact: bool = True
    orphaned_tasks: list[str] = field(default_factory=list)


class HermesProgress:
    """Telemetry tracking, token consumption monitoring (loop-cost), and structural audits."""

    DAEMON_NAME = "ai.hermes.progress"

    def __init__(self, db_path: str = ":memory:", cost_per_1k: float = 0.002):
        self.db = CoordinatorDB(db_path)
        self.cost_per_1k = cost_per_1k
        self._token_usage: dict[str, int] = {}

    def initialize(self) -> None:
        self.db.initialize()

    def record_token_usage(self, task_id: str, tokens_used: int) -> None:
        if task_id not in self._token_usage:
            self._token_usage[task_id] = 0
        self._token_usage[task_id] += tokens_used
        self.db.record_telemetry(
            daemon_name=self.DAEMON_NAME,
            metric=f"tokens.{task_id}",
            value=float(tokens_used),
        )

    def get_cumulative_tokens(self, task_id: str) -> int:
        return self._token_usage.get(task_id, 0)

    def calculate_cost(self, task_id: str) -> float:
        tokens = self.get_cumulative_tokens(task_id)
        return (tokens / 1000.0) * self.cost_per_1k

    def run_structural_audit(self) -> AuditResult:
        """Perform a structural audit of telemetry data."""
        result = AuditResult(tables_intact=True, orphaned_tasks=[])

        # Check that expected tables exist
        cursor = self.db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing_tables = {row["name"] for row in cursor.fetchall()}
        expected = {"telemetry_logs", "execution_traces", "daemon_pids", "active_timestamps"}
        if not expected.issubset(existing_tables):
            result.tables_intact = False

        return result

    def close(self) -> None:
        self.db.close()

"""Coordinator database - Telemetry logs, execution traces, daemon PIDs, and timestamps."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


SCHEMA_COORDINATOR = {
    "telemetry_logs": """
        CREATE TABLE IF NOT EXISTS telemetry_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            daemon_name TEXT NOT NULL,
            metric TEXT NOT NULL,
            value REAL NOT NULL,
            timestamp TEXT NOT NULL
        )
    """,
    "execution_traces": """
        CREATE TABLE IF NOT EXISTS execution_traces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            action TEXT NOT NULL,
            duration_ms REAL,
            exit_code INTEGER,
            timestamp TEXT NOT NULL
        )
    """,
    "daemon_pids": """
        CREATE TABLE IF NOT EXISTS daemon_pids (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            daemon_name TEXT NOT NULL UNIQUE,
            pid INTEGER NOT NULL,
            last_seen TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'running'
        )
    """,
    "active_timestamps": """
        CREATE TABLE IF NOT EXISTS active_timestamps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            daemon_name TEXT NOT NULL,
            event TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    """,
}


class CoordinatorDB:
    """Low-level telemetry, execution traces, daemon PIDs, and active timestamps."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None

    def initialize(self) -> None:
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        for name, ddl in SCHEMA_COORDINATOR.items():
            self.conn.execute(ddl)
        self.conn.commit()

    def list_tables(self) -> list[str]:
        cursor = self.conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        return [row["name"] for row in cursor.fetchall()]

    def record_pid(self, daemon_name: str, pid: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT OR REPLACE INTO daemon_pids (daemon_name, pid, last_seen, status) VALUES (?, ?, ?, 'running')",
            (daemon_name, pid, now),
        )
        self.conn.commit()

    def get_pid(self, daemon_name: str) -> int | None:
        row = self.conn.execute("SELECT pid FROM daemon_pids WHERE daemon_name = ?", (daemon_name,)).fetchone()
        return row["pid"] if row else None

    def record_telemetry(self, daemon_name: str, metric: str, value: float) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT INTO telemetry_logs (daemon_name, metric, value, timestamp) VALUES (?, ?, ?, ?)",
            (daemon_name, metric, value, now),
        )
        self.conn.commit()

    def record_trace(self, task_id: str, action: str, duration_ms: float | None = None,
                     exit_code: int | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT INTO execution_traces (task_id, action, duration_ms, exit_code, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (task_id, action, duration_ms, exit_code, now),
        )
        self.conn.commit()

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None

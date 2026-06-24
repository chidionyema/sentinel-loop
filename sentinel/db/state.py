"""State database - System-wide skill registries, failure-class mappings, and behavioral policies."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


SCHEMA_STATE = {
    "skill_registry": """
        CREATE TABLE IF NOT EXISTS skill_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            skill_name TEXT NOT NULL UNIQUE,
            version TEXT NOT NULL,
            playbook_path TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1
        )
    """,
    "failure_class_map": """
        CREATE TABLE IF NOT EXISTS failure_class_map (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            failure_signature TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'medium',
            created_at TEXT NOT NULL
        )
    """,
    "behavioral_policies": """
        CREATE TABLE IF NOT EXISTS behavioral_policies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            policy_name TEXT NOT NULL UNIQUE,
            rule TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT 'global',
            enabled INTEGER NOT NULL DEFAULT 1
        )
    """,
}


class StateDB:
    """System-wide skill registries, semantic failure-class mappings, and localized behavioral policies."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None

    def initialize(self) -> None:
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        for name, ddl in SCHEMA_STATE.items():
            self.conn.execute(ddl)
        self.conn.commit()

    def list_tables(self) -> list[str]:
        cursor = self.conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        return [row["name"] for row in cursor.fetchall()]

    def register_skill(self, skill_name: str, version: str, playbook_path: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO skill_registry (skill_name, version, playbook_path, enabled) "
            "VALUES (?, ?, ?, 1)",
            (skill_name, version, playbook_path),
        )
        self.conn.commit()

    def get_failure_class(self, signature: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM failure_class_map WHERE failure_signature = ?", (signature,)
        ).fetchone()
        return dict(row) if row else None

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None

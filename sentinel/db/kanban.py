"""Kanban database - State machine ledger for tasks and missions."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone


SCHEMA_KANBAN = {
    "tasks": """
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            task_type TEXT NOT NULL,
            repo_path TEXT NOT NULL,
            description TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending',
            strikes INTEGER NOT NULL DEFAULT 0,
            parent_task_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """,
    "missions": """
        CREATE TABLE IF NOT EXISTS missions (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL
        )
    """,
    "state_transitions": """
        CREATE TABLE IF NOT EXISTS state_transitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            from_state TEXT NOT NULL,
            to_state TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            reason TEXT,
            FOREIGN KEY (task_id) REFERENCES tasks(id)
        )
    """,
}

VALID_STATES = {"pending", "in_progress", "done", "escalated"}

VALID_TRANSITIONS = {
    "pending": {"in_progress", "escalated"},
    "in_progress": {"done", "escalated"},
    "escalated": {"in_progress"},
    "done": set(),
}


class KanbanDB:
    """Single-writer database for task state management."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None

    def initialize(self) -> None:
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        for name, ddl in SCHEMA_KANBAN.items():
            self.conn.execute(ddl)
        self.conn.commit()

    def list_tables(self) -> list[str]:
        cursor = self.conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        return [row["name"] for row in cursor.fetchall()]

    def create_task(self, task_type: str, repo_path: str, description: str, parent_task_id: str | None = None) -> str:
        task_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT INTO tasks (id, task_type, repo_path, description, state, strikes, parent_task_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'pending', 0, ?, ?, ?)",
            (task_id, task_type, repo_path, description, parent_task_id, now, now),
        )
        self.conn.execute(
            "INSERT INTO state_transitions (task_id, from_state, to_state, timestamp, reason) "
            "VALUES (?, 'none', 'pending', ?, 'Task created')",
            (task_id, now),
        )
        self.conn.commit()
        return task_id

    def transition(self, task_id: str, to_state: str, reason: str = "") -> None:
        if to_state not in VALID_STATES:
            raise ValueError(f"Invalid state: {to_state}")

        row = self.conn.execute("SELECT state FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise ValueError(f"Task not found: {task_id}")

        from_state = row["state"]
        if to_state not in VALID_TRANSITIONS.get(from_state, set()):
            raise ValueError(
                f"invalid transition: {from_state} -> {to_state} for task {task_id}"
            )

        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE tasks SET state = ?, updated_at = ? WHERE id = ?",
            (to_state, now, task_id),
        )
        self.conn.execute(
            "INSERT INTO state_transitions (task_id, from_state, to_state, timestamp, reason) "
            "VALUES (?, ?, ?, ?, ?)",
            (task_id, from_state, to_state, now, reason),
        )
        self.conn.commit()

    def get_task_state(self, task_id: str) -> str:
        row = self.conn.execute("SELECT state FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise ValueError(f"Task not found: {task_id}")
        return row["state"]

    def increment_strikes(self, task_id: str) -> int:
        self.conn.execute("UPDATE tasks SET strikes = strikes + 1, updated_at = ? WHERE id = ?",
                          (datetime.now(timezone.utc).isoformat(), task_id))
        self.conn.commit()
        row = self.conn.execute("SELECT strikes FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return row["strikes"] if row else 0

    def get_strikes(self, task_id: str) -> int:
        row = self.conn.execute("SELECT strikes FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return row["strikes"] if row else 0

    def split_task(self, task_id: str) -> list[str]:
        """Split a task into two child sub-tasks."""
        row = self.conn.execute(
            "SELECT task_type, repo_path, description FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Task not found: {task_id}")

        child1 = self.create_task(
            row["task_type"],
            row["repo_path"],
            f"[SPLIT from {task_id}] Part 1: {row['description']}",
            parent_task_id=task_id,
        )
        child2 = self.create_task(
            row["task_type"],
            row["repo_path"],
            f"[SPLIT from {task_id}] Part 2: {row['description']}",
            parent_task_id=task_id,
        )
        return [child1, child2]

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None

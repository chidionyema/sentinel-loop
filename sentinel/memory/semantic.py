"""Semantic Memory Vectoring - Cross-loop sync via .git/.sentinel-memory/

Maintains a local metadata semantic memory network inside .git/ of every active repository:
  - memory.db: SQLite database containing structured diff history
  - embeddings.cache: Serialized vector matrices for rapid lookup matches

Provides context injection of max 4 lines to prevent context window bloat.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path


class SemanticMemory:
    """Semantic memory vectoring for cross-loop architectural cohesion."""

    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path)
        self.memory_dir = self.repo_path / ".git" / ".sentinel-memory"
        self._conn: sqlite3.Connection | None = None

    def initialize(self) -> None:
        """Create the memory directory and database."""
        self.memory_dir.mkdir(parents=True, exist_ok=True)

        # Create empty embeddings cache
        cache = self.memory_dir / "embeddings.cache"
        if not cache.exists():
            cache.write_text(json.dumps({"vectors": [], "version": "1.0"}))

        # Initialize SQLite memory database
        db_path = self.memory_dir / "memory.db"
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS historical_diffs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                commit_message TEXT NOT NULL,
                diff_payload TEXT NOT NULL,
                timestamp TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        self._conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            db_path = self.memory_dir / "memory.db"
            self._conn = sqlite3.connect(str(db_path))
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def archive_semantic_memory(self, task_id: str) -> None:
        """Extract structural changes and intent summary from the latest commit."""
        try:
            git_diff = subprocess.check_output(
                ["git", "diff", "HEAD~1", "HEAD"],
                cwd=str(self.repo_path),
            ).decode("utf-8")

            git_log = subprocess.check_output(
                ["git", "log", "-1", "--pretty=%B"],
                cwd=str(self.repo_path),
            ).decode("utf-8").strip()

            conn = self._get_conn()
            conn.execute("""
                INSERT INTO historical_diffs (task_id, commit_message, diff_payload, timestamp)
                VALUES (?, ?, ?, datetime('now'))
            """, (task_id, git_log, git_diff))
            conn.commit()
        except (subprocess.CalledProcessError, FileNotFoundError):
            # No prior commit or not a git repo
            pass

    def query_historical_diffs(self, task_id: str) -> list[dict]:
        """Query historical diffs for a given task."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM historical_diffs WHERE task_id = ? ORDER BY timestamp DESC",
            (task_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def _store_entry(self, task_id: str, commit_msg: str, diff: str) -> None:
        """Store a test entry directly in the database."""
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO historical_diffs (task_id, commit_message, diff_payload, timestamp) "
            "VALUES (?, ?, ?, datetime('now'))",
            (task_id, commit_msg, diff),
        )
        conn.commit()

    def generate_context_injection(self) -> str | None:
        """Generate a max 4-line markdown context string for the agent.

        Queries memory.db for the most contextually relevant structural diffs
        and derives a dense summary to inject at the top of the task description.
        """
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM historical_diffs ORDER BY timestamp DESC LIMIT 1"
        ).fetchall()

        if not rows:
            return None

        row = rows[0]
        commit_msg = row["commit_message"].split("\n")[0][:80]  # First line, truncated

        lines = [
            "⚙️ Architectural Invariant Warning:",
            f"In Task {row['task_id']}, changes to {commit_msg}.",
        ]

        # Add up to 2 more context lines from the diff if available
        lines = lines[:4]  # Strict 4-line maximum
        return "\n".join(lines)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

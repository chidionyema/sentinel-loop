"""Subsystem 6: Workspace Project Scanner.

Scans a root directory for project markers and maintains a project registry.
"""

from __future__ import annotations

import json as _json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
#  ProjectInfo dataclass
# ---------------------------------------------------------------------------


@dataclass
class ProjectInfo:
    """Information about a discovered project."""
    name: str
    path: str
    markers: list[str] = field(default_factory=list)
    has_git: bool = False
    package_manager: str | None = None


# ---------------------------------------------------------------------------
#  Marker file definitions
# ---------------------------------------------------------------------------

_MARKER_CHECKS: dict[str, tuple[str | None, str]] = {
    # filename       -> (package_manager or None for tool marker, marker label)
    "package.json":      ("npm", "package.json"),
    "requirements.txt":  ("pip", "requirements.txt"),
    "pyproject.toml":    ("poetry", "pyproject.toml"),
    "Cargo.toml":        ("cargo", "Cargo.toml"),
    "Dockerfile":        (None, "Dockerfile"),
    "docker-compose.yml": (None, "docker-compose.yml"),
    "Makefile":          (None, "Makefile"),
}

# Package manager priority order when multiple are found
_PM_PRIORITY = ["npm", "poetry", "pip", "cargo"]


# ---------------------------------------------------------------------------
#  Registry SQLite schema
# ---------------------------------------------------------------------------

_REGISTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS project_registry (
    name TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    markers TEXT NOT NULL,
    has_git INTEGER NOT NULL DEFAULT 0,
    package_manager TEXT,
    last_scanned TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
#  Scanner
# ---------------------------------------------------------------------------


def scan_workspace(root_dir: str) -> list[ProjectInfo]:
    """Scan root_dir (one level) for project directories with marker files.

    Skips hidden directories (starting with '.') and directories with
    no recognized project markers.
    """
    root = Path(root_dir).expanduser().resolve()
    if not root.is_dir():
        return []

    projects: list[ProjectInfo] = []

    try:
        entries = sorted(root.iterdir())
    except (OSError, PermissionError):
        return []

    for entry in entries:
        if not entry.is_dir() and not entry.is_symlink():
            continue

        # Skip hidden directories
        if entry.name.startswith(".") and entry.name != ".":
            continue

        # Resolve symlinks for existence checks, but keep original name
        try:
            resolved = entry.resolve() if entry.is_symlink() else entry
        except OSError:
            continue

        if not resolved.is_dir():
            continue

        # Check for project markers
        markers: list[str] = []
        has_git = False
        package_manager: str | None = None

        # Check for .git
        git_path = resolved / ".git"
        if git_path.exists():
            has_git = True
            markers.append(".git")

        # Check for marker files
        for filename, (pm, marker_label) in _MARKER_CHECKS.items():
            if (resolved / filename).exists():
                markers.append(marker_label)
                if pm is not None and package_manager is None:
                    package_manager = pm

        # If multiple package managers found, use priority order
        if package_manager is None:
            # Check pyproject.toml for poetry vs pip fallback
            pyproject = resolved / "pyproject.toml"
            if pyproject.exists():
                markers.append("pyproject.toml")
                try:
                    content = pyproject.read_text()
                    if "[tool.poetry]" in content:
                        package_manager = "poetry"
                    elif "requirements.txt" not in markers:
                        package_manager = "pip"
                except OSError:
                    pass

        # Skip directories with no project markers
        if not markers:
            continue

        projects.append(ProjectInfo(
            name=entry.name,
            path=str(resolved),
            markers=sorted(set(markers)),
            has_git=has_git,
            package_manager=package_manager,
        ))

    return projects


# ---------------------------------------------------------------------------
#  Registry persistence
# ---------------------------------------------------------------------------


def save_registry(projects: list[ProjectInfo], db_path: str) -> None:
    """Persist the project registry to SQLite."""
    db_path_obj = Path(db_path)
    db_path_obj.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path_obj))
    conn.row_factory = sqlite3.Row
    conn.execute(_REGISTRY_SCHEMA)
    conn.commit()

    now = datetime.now(timezone.utc).isoformat()

    for proj in projects:
        conn.execute(
            "INSERT OR REPLACE INTO project_registry "
            "(name, path, markers, has_git, package_manager, last_scanned) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                proj.name,
                proj.path,
                _json.dumps(proj.markers),
                1 if proj.has_git else 0,
                proj.package_manager,
                now,
            ),
        )

    conn.commit()
    conn.close()


def load_registry(db_path: str) -> list[ProjectInfo]:
    """Load the project registry from SQLite. Returns empty list if DB missing."""
    db_path_obj = Path(db_path)
    if not db_path_obj.exists():
        return []

    try:
        conn = sqlite3.connect(str(db_path_obj))
        conn.row_factory = sqlite3.Row
        conn.execute(_REGISTRY_SCHEMA)
        rows = conn.execute("SELECT * FROM project_registry").fetchall()
        conn.close()

        projects: list[ProjectInfo] = []
        for row in rows:
            markers_raw = row["markers"]
            try:
                markers = _json.loads(markers_raw)
            except (_json.JSONDecodeError, TypeError):
                markers = []

            projects.append(ProjectInfo(
                name=row["name"],
                path=row["path"],
                markers=markers,
                has_git=bool(row["has_git"]),
                package_manager=row["package_manager"],
            ))

        return projects
    except sqlite3.Error:
        return []


def rescan(root_dir: str, db_path: str) -> list[ProjectInfo]:
    """Re-scan and update the registry. Returns new/changed projects since last scan."""
    old = {p.name: p for p in load_registry(db_path)}
    new = scan_workspace(root_dir)
    save_registry(new, db_path)

    changed: list[ProjectInfo] = []
    for p in new:
        if p.name not in old:
            changed.append(p)
        else:
            o = old[p.name]
            if (o.has_git != p.has_git or
                    o.package_manager != p.package_manager or
                    set(o.markers) != set(p.markers)):
                changed.append(p)

    return changed

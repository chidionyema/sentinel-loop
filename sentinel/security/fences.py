"""Security Fences - System determinism rules and strict safety boundaries.

Enforces:
  - Untouchable domains (money, contract, identity, migrations)
  - Forbidden commands (rm -rf, launchctl unload, sudo, chmod -R 777)
  - Single-writer constraint (only coordinator writes to DB)
  - Agent blindness to SQL connections
  - Deterministic escalation on system failures
"""

from __future__ import annotations

from dataclasses import dataclass


UNTOUCHABLE_DOMAINS = ["money", "contract", "identity", "migrations"]
FORBIDDEN_COMMANDS = ["rm -rf", "launchctl unload", "sudo", "chmod -R 777"]


@dataclass
class FailureResult:
    state_frozen: bool = False
    agent_forbidden_from_retry: bool = False
    error: str = ""


class SecurityFence:
    """Security fence enforcing system determinism rules."""

    def can_write_db(self, daemon_name: str) -> bool:
        """Only the coordinator may write to databases."""
        return daemon_name == "coordinator"

    def agent_has_sql_access(self) -> bool:
        """Agents must have no SQL access."""
        return False

    def is_restricted_path(self, path: str) -> bool:
        """Check if a path falls under an untouchable domain."""
        path_normalized = path.strip("/")
        for domain in UNTOUCHABLE_DOMAINS:
            if f"/{domain}/" in f"/{path}/" or path_normalized.startswith(f"estates/{domain}"):
                return True
            # Also match just the domain name in path segments
            segments = path.strip("/").split("/")
            if domain in segments:
                return True
        return False

    def is_command_forbidden(self, command: str) -> bool:
        """Check if a shell command is forbidden. Normalizes whitespace."""
        # Normalize multiple spaces to single spaces for reliable matching
        normalized = " ".join(command.split())
        for forbidden in FORBIDDEN_COMMANDS:
            if forbidden in normalized:
                return True
        return False

    def handle_system_failure(self, exception: Exception, task_id: str) -> FailureResult:
        """Handle a system-level failure with deterministic escalation."""
        return FailureResult(
            state_frozen=True,
            agent_forbidden_from_retry=True,
            error=str(exception),
        )

    def agent_can_fix_permissions(self) -> bool:
        """Agent is never allowed to fix permissions."""
        return False

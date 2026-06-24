"""Cockpit command dispatcher — the execution core (C2).

Turns a validated Telegram callback (``action:target:id``) into a real,
SHELL-FREE subprocess execution under the FiscalSentry 120s/budget guard.

SECURITY MODEL (RCE-sensitive — every step is fail-closed):

  0. execution_enabled()                       — refuse unless COCKPIT_EXECUTION_ENABLED=1
  1. parse_callback(data) -> {action,target,id} — ui_engine; raises on malformed
  2. action ∈ ACTION_SPECS                       — unknown action -> blocked
  3. workspace target -> validate_workspace_path — traversal / symlink-escape -> blocked,
     then resolved to an absolute path INSIDE the root
  4. branch/service token -> ^[A-Za-z0-9._/-]+$  — any injection char -> blocked
  5. argv built by substituting validated values into a PRE-TOKENIZED argv list,
     never a shell string. With shell=False a value can never be re-parsed as a
     command separator (this is why we do NOT shlex.split the acl templates: the
     ``cd {ws} && npm run dev`` forms would exec a ``cd`` binary and break, and
     string-splitting reintroduces the injection surface we are eliminating).
  6. SecurityFence.is_command_forbidden(rendered) — belt-and-suspenders (H1)
  7. FiscalSentry.execute_with_budget(argv, ...)  — real SIGKILL on 120s timeout

ACTION_SPECS is kept in lock-step with acl.COMMAND_REGISTRY (the human-readable
allowlist) by ``tests/test_dispatcher.py::test_action_specs_match_registry`` —
neither can grow an entry the other lacks.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from sentinel.cockpit.acl import validate_workspace_path
from sentinel.cockpit.perimeter import get_workspace_root
from sentinel.cockpit.ui_engine import parse_callback
from sentinel.layers.fiscal_sentry import FiscalSentry
from sentinel.security.fences import SecurityFence


# A branch name or service identifier. Deliberately strict: only characters
# that appear in real git refs / service names. No spaces, no shell metachars.
ALLOWED_TOKEN = re.compile(r"^[A-Za-z0-9._/-]+$")

# Hard ceiling on subprocess time, independent of any caller-supplied sentry.
COMMAND_TIME_BUDGET_SECONDS = 120


@dataclass(frozen=True)
class ArgvSpec:
    """A shell-free command template.

    ``argv`` tokens may contain ``{workspace}``, ``{branch}`` or ``{service}``
    placeholders, each filled by exactly one validated value. ``cwd`` is either
    ``None`` or ``"{workspace}"`` (run inside the validated project dir).
    """

    argv: tuple[str, ...]
    cwd: str | None = None
    # Which parsed-callback field supplies the workspace dir (validated as a
    # path inside the root), or None if this action takes no workspace.
    workspace_from: str | None = "target"
    # Which parsed-callback field supplies an allowlisted token, its semantic
    # name ("branch"/"service"), or (None, None) if the action takes no token.
    token_field: str | None = None
    token_name: str | None = None


# Keyed identically to acl.COMMAND_REGISTRY. Each entry is the SAME command,
# expressed as an explicit argv (no shell, no `cd`, no `&&`). Working directory
# is handled by cwd= instead of a `cd` prefix.
ACTION_SPECS: dict[str, ArgvSpec] = {
    "git_pull": ArgvSpec(
        argv=("git", "-C", "{workspace}", "pull", "origin", "{branch}"),
        token_field="id", token_name="branch",
    ),
    "gp": ArgvSpec(
        argv=("git", "-C", "{workspace}", "pull", "origin", "{branch}"),
        token_field="id", token_name="branch",
    ),
    "git_status": ArgvSpec(argv=("git", "-C", "{workspace}", "status", "--short")),
    "gs": ArgvSpec(argv=("git", "-C", "{workspace}", "status", "--short")),
    "git_log": ArgvSpec(argv=("git", "-C", "{workspace}", "log", "--oneline", "-10")),
    "gl": ArgvSpec(argv=("git", "-C", "{workspace}", "log", "--oneline", "-10")),
    "git_fetch": ArgvSpec(argv=("git", "-C", "{workspace}", "fetch", "--all")),
    "npm_dev": ArgvSpec(argv=("npm", "run", "dev"), cwd="{workspace}"),
    "npm_build": ArgvSpec(argv=("npm", "run", "build"), cwd="{workspace}"),
    "npm_install": ArgvSpec(argv=("npm", "install"), cwd="{workspace}"),
    "npm_test": ArgvSpec(argv=("npm", "test"), cwd="{workspace}"),
    "pip_install": ArgvSpec(
        argv=("pip", "install", "-r", "requirements.txt"), cwd="{workspace}",
    ),
    "docker_up": ArgvSpec(argv=("docker", "compose", "up", "-d"), cwd="{workspace}"),
    "docker_down": ArgvSpec(argv=("docker", "compose", "down"), cwd="{workspace}"),
    "docker_build": ArgvSpec(argv=("docker", "compose", "build"), cwd="{workspace}"),
    "docker_logs": ArgvSpec(
        argv=("docker", "compose", "logs", "--tail=50", "{service}"),
        cwd="{workspace}", token_field="id", token_name="service",
    ),
    # Service-only actions: the callback `target` segment IS the service name.
    "systemctl_restart": ArgvSpec(
        argv=("systemctl", "restart", "{service}"),
        workspace_from=None, token_field="target", token_name="service",
    ),
    "systemctl_status": ArgvSpec(
        argv=("systemctl", "status", "{service}"),
        workspace_from=None, token_field="target", token_name="service",
    ),
    "make_build": ArgvSpec(argv=("make",), cwd="{workspace}"),
    "make_test": ArgvSpec(argv=("make", "test"), cwd="{workspace}"),
}


@dataclass
class DispatchResult:
    """Outcome of a dispatch attempt. Plain dataclass — never an Exception."""

    ok: bool
    action: str = ""
    blocked_reason: str | None = None
    argv: list[str] = field(default_factory=list)
    exit_code: int | None = None
    was_killed: bool = False
    stdout: str = ""
    stderr: str = ""


def execution_enabled() -> bool:
    """Master switch. Execution is OFF unless COCKPIT_EXECUTION_ENABLED=1."""
    return os.environ.get("COCKPIT_EXECUTION_ENABLED", "").strip() == "1"


def _truncate(text: str, max_lines: int) -> str:
    """Cap output to the first ``max_lines`` lines for a Telegram message."""
    if not text:
        return ""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    head = "\n".join(lines[:max_lines])
    return f"{head}\n… ({len(lines) - max_lines} more lines truncated)"


def dispatch(
    callback_data: str,
    workspace_root: str | None = None,
    *,
    sentry: FiscalSentry | None = None,
    fence: SecurityFence | None = None,
    max_output_lines: int = 50,
) -> DispatchResult:
    """Validate a Telegram callback and execute its mapped command safely.

    Returns a DispatchResult. Never raises for an untrusted/malformed input and
    never runs a shell. Every rejection sets ``blocked_reason`` and ``ok=False``.
    """
    # 0. Master gate — fail closed.
    if not execution_enabled():
        return DispatchResult(ok=False, blocked_reason="execution-disabled")

    # 1. Parse (untrusted) callback data.
    try:
        parsed = parse_callback(callback_data)
    except ValueError:
        return DispatchResult(ok=False, blocked_reason="malformed-callback")

    action = parsed["action"]

    # 2. Action must be a known, pre-approved command.
    spec = ACTION_SPECS.get(action)
    if spec is None:
        return DispatchResult(ok=False, blocked_reason="unknown-action", action=action)

    root = workspace_root if workspace_root is not None else get_workspace_root()
    subst: dict[str, str] = {}
    cwd: str | None = None

    # 3. Workspace path — must resolve INSIDE root and exist.
    if spec.workspace_from is not None:
        ws_target = parsed.get(spec.workspace_from, "")
        if not validate_workspace_path(ws_target, root):
            return DispatchResult(ok=False, blocked_reason="invalid-workspace", action=action)
        abs_ws = str((Path(root).resolve() / ws_target).resolve())
        subst["workspace"] = abs_ws
        if spec.cwd is not None:
            cwd = abs_ws

    # 4. Branch / service token — strict allowlist, fail closed.
    if spec.token_field is not None:
        token = parsed.get(spec.token_field, "")
        if not token or not ALLOWED_TOKEN.match(token):
            return DispatchResult(
                ok=False, blocked_reason=f"invalid-{spec.token_name}", action=action
            )
        subst[spec.token_name] = token

    # 5. Build argv by per-token substitution. No shell, ever.
    try:
        argv = [tok.format(**subst) for tok in spec.argv]
    except KeyError:
        # A placeholder had no validated value — refuse rather than run a
        # half-formed command.
        return DispatchResult(ok=False, blocked_reason="missing-substitution", action=action)

    # 6. Belt-and-suspenders forbidden-command screen (H1).
    fence = fence or SecurityFence()
    if fence.is_command_forbidden(" ".join(argv)):
        return DispatchResult(
            ok=False, blocked_reason="forbidden-command", action=action, argv=argv
        )

    # 7. Execute under the fiscal/time sentry (real SIGKILL on timeout).
    sentry = sentry or FiscalSentry(time_budget_seconds=COMMAND_TIME_BUDGET_SECONDS)
    result = sentry.execute_with_budget(argv, f"cockpit:{action}", cwd=cwd)

    return DispatchResult(
        ok=(result.exit_code == 0 and not result.was_killed),
        action=action,
        argv=argv,
        exit_code=result.exit_code,
        was_killed=result.was_killed,
        stdout=_truncate(result.stdout, max_output_lines),
        stderr=_truncate(result.stderr, max_output_lines),
    )

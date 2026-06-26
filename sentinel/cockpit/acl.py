"""Subsystem 5: External ACL & Webhook Auth.

- Telegram user ACL: validate_telegram_user(from_id)
- GitHub HMAC: verify_github_hmac(payload, signature_header)
- Monitor source: verify_monitor_source(source, payload)
- Workspace path safety: validate_workspace_path(workspace, root)
- Shell command registry: COMMAND_REGISTRY (predefined templates only)
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import json as _json
import os
from pathlib import Path

# ---------------------------------------------------------------------------
#  Shell command registry — maps action names to SAFE command templates.
#  Templates use {placeholder} format strings; never inject raw user text.
# ---------------------------------------------------------------------------

COMMAND_REGISTRY: dict[str, str] = {
    "git_pull": "git -C {workspace} pull origin {branch}",
    "git_status": "git -C {workspace} status --short",
    "git_log": "git -C {workspace} log --oneline -10",
    "git_fetch": "git -C {workspace} fetch --all",
    "npm_dev": "cd {workspace} && npm run dev",
    "npm_build": "cd {workspace} && npm run build",
    "npm_install": "cd {workspace} && npm install",
    "npm_test": "cd {workspace} && npm test",
    "pip_install": "cd {workspace} && pip install -r requirements.txt",
    "docker_up": "cd {workspace} && docker compose up -d",
    "docker_down": "cd {workspace} && docker compose down",
    "docker_build": "cd {workspace} && docker compose build",
    "docker_logs": "cd {workspace} && docker compose logs --tail=50 {service}",
    "systemctl_restart": "systemctl restart {service}",
    "systemctl_status": "systemctl status {service}",
    "make_build": "cd {workspace} && make",
    "make_test": "cd {workspace} && make test",
    # Short aliases (for compact callback_data)
    "gs": "git -C {workspace} status --short",
    "gp": "git -C {workspace} pull origin {branch}",
    "gl": "git -C {workspace} log --oneline -10",
}

# ---------------------------------------------------------------------------
#  Telegram user ACL
# ---------------------------------------------------------------------------


def _get_allowed_user_ids() -> set[int]:
    """Parse TELEGRAM_ALLOWED_USER_IDS from env (comma-separated integers)."""
    raw = os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "")
    if not raw.strip():
        return set()
    ids: set[int] = set()
    for part in raw.split(","):
        stripped = part.strip()
        if stripped:
            try:
                ids.add(int(stripped))
            except ValueError:
                pass  # Skip non-integer entries
    return ids


def validate_telegram_user(from_id: int) -> bool:
    """Validate a Telegram user ID against the allowed ACL set.

    If TELEGRAM_ALLOWED_USER_IDS is not set, all users are denied.
    """
    allowed = _get_allowed_user_ids()
    result = from_id in allowed
    if not result:
        # Surface every rejection: a silent drop here is indistinguishable from
        # "the bot is down" to the rejected user (this is how Dario saw nothing).
        import sys as _sys
        print(f"[cockpit] ACL REJECT telegram_id={from_id} "
              f"(allowed={sorted(allowed) or 'NONE — deny-all'})",
              file=_sys.stderr, flush=True)
    return result


# ---------------------------------------------------------------------------
#  GitHub HMAC verification
# ---------------------------------------------------------------------------


def verify_github_hmac(payload: bytes, signature_header: str) -> bool:
    """Verify X-Hub-Signature-256 header using GITHUB_WEBHOOK_SECRET.

    Uses hmac.compare_digest for timing-safe comparison.
    """
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    if not secret or not signature_header:
        return False

    if not signature_header.startswith("sha256="):
        return False

    expected_sig = signature_header[len("sha256="):]
    if not expected_sig:
        return False

    computed = _hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()

    # Use compare_digest for timing safety
    return _hmac.compare_digest(computed, expected_sig)


# ---------------------------------------------------------------------------
#  Monitor source verification
# ---------------------------------------------------------------------------


def _get_monitor_api_keys() -> dict[str, str]:
    """Parse MONITOR_API_KEYS from env (JSON dict of source -> key)."""
    raw = os.environ.get("MONITOR_API_KEYS", "")
    if not raw.strip():
        return {}
    try:
        return _json.loads(raw)
    except _json.JSONDecodeError:
        return {}


def verify_monitor_source(source: str, payload: dict) -> bool:
    """Verify the source of a monitoring webhook payload.

    Checks payload for matching API key or token in expected fields.
    """
    keys = _get_monitor_api_keys()
    if not keys:
        return False

    expected_key = keys.get(source)
    if expected_key is None:
        # Unknown source - deny
        return False

    # Check common token fields
    token_fields = ["api_key", "token", "key", "apiKey", "secret"]
    for field in token_fields:
        if payload.get(field) == expected_key:
            return True

    # Check headers-like nesting
    headers = payload.get("headers", {})
    if isinstance(headers, dict):
        for field in token_fields:
            if headers.get(field) == expected_key:
                return True

    # X-API-Key in a nested structure
    if payload.get("X-API-Key") == expected_key:
        return True

    return False


# ---------------------------------------------------------------------------
#  Workspace path validation
# ---------------------------------------------------------------------------


def validate_workspace_path(workspace: str, root: str) -> bool:
    """Validate that a workspace path is safely within the root directory.

    Resolves the full path, checks it starts with root (no traversal),
    resolves symlinks, and verifies existence.

    Returns True only if the path exists and is within root.
    """
    if not workspace or not root:
        return False

    try:
        root_path = Path(root).resolve()
        full_path = (root_path / workspace).resolve()

        # Must be within root
        try:
            full_path.relative_to(root_path)
        except ValueError:
            return False

        # Must exist
        if not full_path.exists():
            return False

        return True
    except (OSError, RuntimeError):
        return False

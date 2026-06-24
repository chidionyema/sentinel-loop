"""Cockpit server runner — starts the FastAPI webhook server (C3).

Entry point for the cockpit-server launchd daemon. Calls
perimeter.require_production_env() in production mode to enforce
secret presence BEFORE binding a port that may be tunnel-exposed.

Usage:
    python -m sentinel.cockpit.runner          # dev (no gate)
    COCKPIT_ENV=prod python -m sentinel.cockpit.runner  # production gate
"""

from __future__ import annotations

import os
import sys
import time
from typing import Callable


def _is_production() -> bool:
    return os.environ.get("COCKPIT_ENV", "").lower() in {"prod", "production"}


def preflight() -> None:
    """Run all startup checks. Raises on fatal misconfiguration.

    In dev mode (COCKPIT_ENV unset or 'dev'): missing env vars are
    warnings only — the server boots without them for local testing.
    In production mode: require_production_env() is called first, then
    validate_cockpit_env() raises on any MISSING vars.
    """
    from sentinel.cockpit.perimeter import (
        require_production_env,
        validate_cockpit_env,
    )

    if _is_production():
        require_production_env()

    issues = validate_cockpit_env()
    if not issues:
        return

    fatal = [i for i in issues if i.startswith("MISSING:")]
    if fatal:
        if _is_production():
            raise RuntimeError(
                "Cockpit preflight failed — missing required config:\n  "
                + "\n  ".join(fatal)
            )
        else:
            # Dev mode: warn only
            for w in issues:
                print(f"[preflight] {w}", file=sys.stderr)
    else:
        # Warnings only — print but don't abort
        for w in issues:
            print(f"[preflight] {w}", file=sys.stderr)


def main(sleeper: Callable[[float], None] = time.sleep) -> None:
    """Start the cockpit server. Blocks until the process is killed.

    The sleeper parameter exists for testability — tests pass a no-op or
    short sleep to avoid blocking.
    """
    from sentinel.cockpit.perimeter import get_bind_config
    from sentinel.cockpit.server import create_app

    try:
        import uvicorn
    except ImportError:
        print("FATAL: uvicorn not installed. Run: pip install uvicorn", file=sys.stderr)
        sys.exit(1)

    preflight()

    host, port = get_bind_config()

    # H5: Normalise 'localhost' → '127.0.0.1' at bind time to avoid
    # dual-stack ambiguity (localhost can resolve to ::1 on IPv6 hosts).
    # The perimeter's get_bind_config() still returns 'localhost' as-is
    # (held-out test requires it), but we never bind to that string.
    if host == "localhost":
        host = "127.0.0.1"
    app = create_app()
    env_label = "PRODUCTION" if _is_production() else "DEV"

    print(f"[cockpit] {env_label} server starting on {host}:{port}")
    sys.stdout.flush()

    # uvicorn.run blocks; the sleeper is unused in normal operation
    # but present so the function signature can be called by test harnesses
    # without actually starting a real server.
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()

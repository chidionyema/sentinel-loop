"""Subsystem 7: Perimeter Hardening.

Binding configuration, tunnel readiness check, and environment validation.
Enforces localhost-only binding to prevent external port exposure.

Config is read from ~/.hermes/config/cockpit.json with env-var overrides.
"""

from __future__ import annotations

import json as _json
import os
import shutil
from pathlib import Path


# ---------------------------------------------------------------------------
#  Config loader
# ---------------------------------------------------------------------------


def _load_cockpit_config() -> dict:
    """Load cockpit config from ~/.hermes/config/cockpit.json."""
    config_path = Path.home() / ".hermes" / "config" / "cockpit.json"
    if config_path.exists():
        try:
            return _json.loads(config_path.read_text())
        except (_json.JSONDecodeError, OSError):
            pass
    return {}


def get_workspace_root() -> str:
    """Get the configured workspace root for project scanning."""
    config = _load_cockpit_config()
    return os.environ.get(
        "COCKPIT_WORKSPACE_ROOT",
        config.get("workspace_root", os.path.expanduser("~/Documents/code")),
    )


# ---------------------------------------------------------------------------
#  Binding configuration
# ---------------------------------------------------------------------------


def get_bind_config() -> tuple[str, int]:
    """Get the server bind (host, port) from environment or config.

    Priority: env vars > ~/.hermes/config/cockpit.json > defaults.
    COCKPIT_HOST: default '127.0.0.1'. Must be 127.0.0.1 or localhost.
                  0.0.0.0 is forbidden.
    COCKPIT_PORT: default 8800. Must be a valid integer.

    Raises ValueError for invalid configurations.
    """
    config = _load_cockpit_config()
    server_cfg = config.get("server", {})

    host = os.environ.get(
        "COCKPIT_HOST",
        server_cfg.get("host", "127.0.0.1"),
    ).strip()

    if host == "0.0.0.0":
        raise ValueError(
            "COCKPIT_HOST=0.0.0.0 is forbidden. "
            "The server must bind to 127.0.0.1 or localhost only. "
            "External access must go through a Cloudflare Tunnel or ngrok."
        )

    if host not in ("127.0.0.1", "localhost"):
        raise ValueError(
            f"COCKPIT_HOST={host} is not allowed. "
            "Only 127.0.0.1 or localhost are permitted."
        )

    port_str = os.environ.get(
        "COCKPIT_PORT",
        str(server_cfg.get("port", 8800)),
    ).strip()
    try:
        port = int(port_str)
    except ValueError:
        raise ValueError(
            f"COCKPIT_PORT={port_str} is not a valid integer."
        )

    if port < 1 or port > 65535:
        raise ValueError(
            f"COCKPIT_PORT={port} is out of range (1-65535)."
        )

    return host, port


# ---------------------------------------------------------------------------
#  Tunnel readiness check
# ---------------------------------------------------------------------------


def verify_tunnel_config() -> dict:
    """Check if a tunnel client (cloudflared or ngrok) is available.

    Does NOT start or manage the tunnel — just reports configuration status.

    Returns:
        {"ready": bool, "provider": str|None, "message": str}
    """
    # Check for cloudflared
    cloudflared = shutil.which("cloudflared")
    if cloudflared:
        return {
            "ready": True,
            "provider": "cloudflared",
            "message": f"cloudflared found at {cloudflared}",
        }

    # Check for ngrok
    ngrok = shutil.which("ngrok")
    if ngrok:
        return {
            "ready": True,
            "provider": "ngrok",
            "message": f"ngrok found at {ngrok}",
        }

    # Check for ngrok config file (installed but not in PATH)
    ngrok_config = os.path.expanduser("~/.ngrok2/ngrok.yml")
    if os.path.exists(ngrok_config):
        return {
            "ready": True,
            "provider": "ngrok",
            "message": f"ngrok config found at {ngrok_config} (binary may not be in PATH)",
        }

    return {
        "ready": False,
        "provider": None,
        "message": (
            "No tunnel client found. Install cloudflared (brew install cloudflared) "
            "or ngrok (brew install ngrok) to expose the server externally."
        ),
    }


# ---------------------------------------------------------------------------
#  Environment validation
# ---------------------------------------------------------------------------


def validate_cockpit_env() -> list[str]:
    """Validate that required cockpit environment variables are set.

    Returns a list of warning/error messages. Empty list means all good.
    """
    issues: list[str] = []

    # Required
    required = {
        "TELEGRAM_BOT_TOKEN": "Telegram bot token from @BotFather",
        "TELEGRAM_ALLOWED_USER_IDS": "Comma-separated Telegram user IDs allowed to use the cockpit",
    }

    for var, desc in required.items():
        if not os.environ.get(var):
            issues.append(f"MISSING: {var} ({desc})")

    # Recommended
    recommended = {
        "GITHUB_WEBHOOK_SECRET": "Shared secret for GitHub webhook HMAC verification",
        "MONITOR_API_KEYS": "JSON dict of monitoring source API keys",
    }

    for var, desc in recommended.items():
        if not os.environ.get(var):
            issues.append(f"WARNING: {var} not set ({desc})")

    return issues


# ---------------------------------------------------------------------------
#  Production startup gate
# ---------------------------------------------------------------------------


# Secrets that MUST be present before the cockpit is exposed on a public tunnel.
PRODUCTION_REQUIRED_ENV = {
    "TELEGRAM_BOT_TOKEN": "Telegram bot token from @BotFather",
    "TELEGRAM_ALLOWED_USER_IDS": "Allowed Telegram user IDs (ACL)",
    "TELEGRAM_WEBHOOK_SECRET": "setWebhook secret_token — proves requests originate from Telegram",
    "GITHUB_WEBHOOK_SECRET": "GitHub webhook HMAC secret",
    "MONITOR_API_KEYS": "JSON dict of monitor source API keys",
}


def require_production_env() -> None:
    """Hard gate for PRODUCTION startup. Raise RuntimeError if any secret
    required to run safely on a public tunnel is missing.

    This is the enforcement point for two findings the webhook endpoints
    cannot enforce themselves without breaking the held-out dev contract
    (which intentionally accepts unauthenticated calls when a source is
    UNconfigured, so local development needs no secrets):

      - Telegram webhook origin proof — the /webhooks/telegram handler only
        verifies X-Telegram-Bot-Api-Secret-Token when TELEGRAM_WEBHOOK_SECRET
        is set; requiring it here guarantees it is set in production.
      - Monitor source auth — /webhooks/monitor fails OPEN when MONITOR_API_KEYS
        is unset; requiring it here guarantees that never happens in production.

    The server runner MUST call this before binding when not in dev mode.
    """
    missing = [
        f"{var} ({desc})"
        for var, desc in PRODUCTION_REQUIRED_ENV.items()
        if not os.environ.get(var, "").strip()
    ]
    if missing:
        raise RuntimeError(
            "Refusing to start the cockpit in production — missing required secrets:\n  - "
            + "\n  - ".join(missing)
            + "\nSet these (e.g. in ~/.hermes/.env, chmod 600) before exposing the server "
            "on a public tunnel. For local development, leave COCKPIT_ENV unset/`dev` to skip this gate."
        )

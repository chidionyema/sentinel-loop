"""Mothership HTTP Webhook Server (FastAPI).

Endpoints:
  GET  /health              — health check
  POST /webhooks/telegram   — Telegram webhook
  POST /webhooks/github     — GitHub webhook
  POST /webhooks/monitor    — Monitoring webhook
"""

from __future__ import annotations

import hmac as _hmac
import json as _json
import os
import subprocess
import sys
from typing import Any

from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import JSONResponse


def _xml_escape(s: str) -> str:
    """Tiny XML escape for Telegram parse_mode=HTML payloads.

    Avoids pulling in html.escape for one call site. Escapes the four chars
    that Telegram's HTML parser treats as markup.
    """
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
    )


# ---------------------------------------------------------------------------
#  Free-form text → coordinator queue injection
# ---------------------------------------------------------------------------
# Path to the coordinator CLI. Centralized so tests + ops can override it
# without touching the webhook handler.
COORDINATOR_CLI = os.path.expanduser("~/.hermes/scripts/coordinator.py")


def _inject_coordinator_task(text: str) -> tuple[bool, str]:
    """Inject a free-form text message into the coordinator's task queue.

    Returns (ok, payload). payload is the task id on success, or a short
    error description on failure. Never raises — exceptions are caught and
    returned as (False, str(e)).
    """
    try:
        r = subprocess.run(
            [sys.executable, COORDINATOR_CLI, "inject", text],
            capture_output=True, text=True, timeout=10,
        )
    except subprocess.TimeoutExpired:
        return False, "coordinator-inject-timeout"
    except Exception as e:
        return False, f"coordinator-inject-error: {e}"

    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        return False, f"coordinator-exit-{r.returncode}: {err[:160]}"

    task_id = (r.stdout or "").strip().splitlines()[-1] if r.stdout else ""
    if not task_id:
        return False, "coordinator-no-task-id"
    return True, task_id


def get_server_config() -> tuple[str, int]:
    """Get server bind config from perimeter."""
    from sentinel.cockpit.perimeter import get_bind_config
    return get_bind_config()


# ═══════════════════════════════════════════════════════════════════════════
#  App factory
# ═══════════════════════════════════════════════════════════════════════════

def create_app() -> FastAPI:
    app = FastAPI(title="Mothership", docs_url=None, redoc_url=None)
    _register_routes(app)
    return app


def _register_routes(app: FastAPI) -> None:

    @app.get("/health")
    async def health():
        return JSONResponse({
            "status": "ok",
            "daemon": "ai.mothership.gateway",
            "mothership": "active",
        })

    @app.post("/webhooks/telegram")
    async def telegram_webhook(request: Request):
        # ── Origin proof ─────────────────────────────────────────
        expected_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
        if expected_secret:
            got = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if not _hmac.compare_digest(got, expected_secret):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                    detail="Invalid webhook origin token")

        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail="Invalid JSON body")

        message = body.get("message", {}) or {}
        callback_query = body.get("callback_query", {}) or {}

        # ── Extract identity ──────────────────────────────────────
        from_id = None
        if message:
            from_id = (message.get("from", {}) or {}).get("id")
        elif callback_query:
            from_id = (callback_query.get("from", {}) or {}).get("id")

        # ── ACL ───────────────────────────────────────────────────
        from sentinel.cockpit.acl import validate_telegram_user
        if from_id is None or not validate_telegram_user(from_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="User not authorized")

        # ── Extract chat_id ───────────────────────────────────────
        chat_id = ""
        if message:
            chat_id = str((message.get("chat", {}) or {}).get("id", ""))
        elif callback_query:
            msg = callback_query.get("message", {}) or {}
            chat_id = str((msg.get("chat", {}) or {}).get("id", ""))

        # ── Route text message → dashboard OR coordinator queue ────────
        # /start and "menu" re-render the cockpit dashboard (preserved).
        # Other /commands are explicitly unknown (no other slash commands today).
        # Plain text is injected into the coordinator's task queue so the agent
        # can pick it up on its next tick and reply back to this same chat.
        # Non-text messages (stickers, photos) fall through to the dashboard.
        if message and chat_id:
            from sentinel.cockpit.menu import scan_projects, view_dashboard, send
            text_in = (message.get("text") or "").strip()

            if text_in.lower() in ("/start", "/menu", "menu"):
                text, kb = view_dashboard()
                send(chat_id, text, kb)
            elif text_in.startswith("/"):
                send(
                    chat_id,
                    f"❓ Unknown command: <code>{_xml_escape(text_in[:60])}</code>",
                )
            elif text_in:
                ok, payload = _inject_coordinator_task(text_in)
                if ok:
                    send(
                        chat_id,
                        "🧠 Queued as task <code>{}</code>\n"
                        "     <i>{}</i>\n"
                        "     The agent will reply in this chat when done.".format(
                            payload, _xml_escape(text_in[:120]),
                        ),
                    )
                else:
                    send(
                        chat_id,
                        f"⚠ Could not queue task: <code>{_xml_escape(payload)}</code>",
                    )
            else:
                text, kb = view_dashboard()
                send(chat_id, text, kb)

        # ── Callback routing ──────────────────────────────────────
        dispatch_result = None
        if callback_query:
            from sentinel.cockpit.dispatcher import dispatch, execution_enabled
            from sentinel.cockpit.menu import (
                handle_callback, answer, send,
                scan_projects, view_dashboard,
            )

            data = callback_query.get("data", "") or ""

            # Navigation + daemon → menu.py
            if data.startswith("nv:") or data.startswith("ac:") or (data.startswith("d") and len(data) >= 2 and data[1] in "halgcdsxkirz"):
                await handle_callback(data, chat_id, callback_query.get("id", ""))

            # Actions
            elif data.startswith("action:"):
                answer(callback_query.get("id", ""))
                if data.split(":")[1] == "rescan":
                    text, kb = view_dashboard()
                    send(chat_id, text, kb)

            # Git commands etc.
            elif execution_enabled():
                try:
                    dr = dispatch(data)
                    dispatch_result = {
                        "ok": dr.ok, "action": dr.action,
                        "blocked_reason": dr.blocked_reason,
                        "exit_code": dr.exit_code, "was_killed": dr.was_killed,
                    }
                    icon = "✅" if dr.exit_code == 0 else "❌" if dr.exit_code else "⏳"
                    lines = [f"{icon} {dr.action}"]
                    if dr.blocked_reason:
                        lines.append(f"Blocked: {dr.blocked_reason}")
                    elif dr.exit_code is not None:
                        out = dr.stdout.strip() or dr.stderr.strip()
                        lines.append(out[:800] if out else f"Exit: {dr.exit_code}")
                    answer(callback_query.get("id", ""))
                    send(chat_id, "\n".join(lines))
                except Exception:
                    dispatch_result = {"ok": False, "blocked_reason": "dispatch-error"}
            else:
                answer(callback_query.get("id", ""))

        payload_out = {
            "status": "received",
            "chat_id": chat_id,
            "type": "message" if message else "callback_query" if callback_query else "unknown",
        }
        if dispatch_result is not None:
            payload_out["dispatch"] = dispatch_result
        return JSONResponse(payload_out)

    @app.post("/webhooks/github")
    async def github_webhook(request: Request):
        signature = request.headers.get("X-Hub-Signature-256", "")
        secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
        if not signature:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail="Missing signature")
        if secret:
            body_bytes = await request.body()
            expected = "sha256=" + _hmac.new(secret.encode(), body_bytes, "sha256").hexdigest()
            if not _hmac.compare_digest(signature, expected):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                    detail="Invalid signature")
            try:
                payload = _json.loads(body_bytes)
            except Exception:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                    detail="Invalid JSON")
        else:
            body_bytes = await request.body()
            try:
                payload = _json.loads(body_bytes)
            except Exception:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                    detail="Invalid JSON")
        return JSONResponse({"status": "received", "event": request.headers.get("X-GitHub-Event", "")})

    @app.post("/webhooks/monitor")
    async def monitor_webhook(request: Request):
        api_keys = os.environ.get("MONITOR_API_KEYS", "")
        if api_keys:
            auth = request.headers.get("Authorization", "")
            if not auth or auth not in api_keys:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                    detail="Invalid API key")
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail="Invalid JSON")
        return JSONResponse({"status": "received", "source": body.get("source", "unknown")})

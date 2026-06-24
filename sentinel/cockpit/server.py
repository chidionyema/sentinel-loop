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
import time
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


# Per-attempt timeout. Coordinator's daemon (pid 56130) holds an exclusive
# transaction during its tick; a single 30s window comfortably outlasts the
# longest tick observed (~3s) with margin for slow disks.
_INJECT_TIMEOUT_S = 30.0


def _inject_coordinator_task(text: str) -> tuple[bool, str]:
    """Inject a free-form text message into the coordinator's task queue.

    Returns (ok, payload). payload is the task id on success, or a short
    error description on failure. Never raises — exceptions are caught and
    returned as (False, str(e)).

    Retries once on TimeoutExpired. The coordinator's daemon holds a brief
    write lock during its tick (sqlite3 WAL mode serializes writers); if our
    inject arrives during that window the subprocess blocks until the lock
    is released. A second attempt after a short pause reliably wins.
    """
    argv = [sys.executable, COORDINATOR_CLI, "inject", text]
    last_err = ""

    for attempt in (1, 2):
        try:
            r = subprocess.run(
                argv, capture_output=True, text=True, timeout=_INJECT_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            last_err = "coordinator-inject-timeout"
            time.sleep(0.5)  # brief backoff before retry
            continue
        except Exception as e:
            return False, f"coordinator-inject-error: {e}"

        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            return False, f"coordinator-exit-{r.returncode}: {err[:160]}"

        task_id = (r.stdout or "").strip().splitlines()[-1] if r.stdout else ""
        if not task_id:
            return False, "coordinator-no-task-id"
        return True, task_id

    return False, last_err


def get_server_config() -> tuple[str, int]:
    """Get server bind config from perimeter."""
    from sentinel.cockpit.perimeter import get_bind_config
    return get_bind_config()


# ═══════════════════════════════════════════════════════════════════════════
#  Persistent Telegram menu — setMyCommands + setChatMenuButton
# ═══════════════════════════════════════════════════════════════════════════
# These two API calls register UI elements on Telegram's side that survive
# bot restarts, app reinstalls, and session changes. After they run once
# (typically at server startup), the operator sees a permanent MENU icon
# next to the chat input that opens the command list — no need to type /,
# no need to remember commands, no need for the bot to send anything.

# Slash commands registered via setMyCommands. Visible to the operator
# whenever they type /, and inside the chat menu button.
_PERSISTENT_COMMANDS: list[dict[str, str]] = [
    {"command": "dashboard",   "description": "Mothership home — projects + system stats"},
    {"command": "daemon",      "description": "Prospector scheduler — status + gates + funnel"},
    {"command": "killed",      "description": "Recently killed dossiers with gate + score"},
    {"command": "investigate", "description": "Generator investigation — search health check"},
    {"command": "search",      "description": "Search providers — live test (EXA, Brave)"},
    {"command": "heartbeat",   "description": "Last scheduler heartbeat"},
    {"command": "schedule",    "description": "Scheduler cadence + last run"},
    {"command": "alerts",      "description": "Active alerts (top 3)"},
    {"command": "logs",        "description": "Paginated scheduler launchd log"},
    {"command": "menu",        "description": "Alias for /dashboard"},
    {"command": "start",       "description": "Alias for /dashboard"},
]


def register_persistent_menu() -> None:
    """Register the persistent Mothership menu with Telegram.

    - setMyCommands: bot-wide slash command list (visible when user types /).
    - setChatMenuButton: per-chat MENU icon next to the input field.

    Idempotent. Failures are logged to stderr but never crash the server —
    the webhook should keep working even if Telegram's menu API is down.
    """
    import urllib.request as _ur

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("[cockpit] register_persistent_menu skipped: TELEGRAM_BOT_TOKEN unset",
              file=sys.stderr)
        return

    allowed_raw = os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "")
    user_id = 0
    for part in allowed_raw.split(","):
        s = part.strip()
        if s.isdigit():
            user_id = int(s)
            break
    if not user_id:
        print("[cockpit] register_persistent_menu skipped: TELEGRAM_ALLOWED_USER_IDS unset",
              file=sys.stderr)
        return

    base = f"https://api.telegram.org/bot{token}"

    def _post(method: str, payload: dict) -> tuple[bool, str]:
        try:
            req = _ur.Request(
                f"{base}/{method}",
                data=_json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            resp = _json.loads(_ur.urlopen(req, timeout=10).read())
            if resp.get("ok"):
                return True, ""
            return False, str(resp.get("description") or resp)
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    # 1. Bot-wide command list — visible to anyone who opens /menu or types /.
    ok, err = _post("setMyCommands", {"commands": _PERSISTENT_COMMANDS})
    if ok:
        print(f"[cockpit] setMyCommands OK ({len(_PERSISTENT_COMMANDS)} commands)")
    else:
        print(f"[cockpit] setMyCommands failed: {err}", file=sys.stderr)

    # 2. Per-chat MENU button (always-visible icon next to input field).
    ok, err = _post("setChatMenuButton", {
        "chat_id": user_id,
        "menu_button": {"type": "commands"},
    })
    if ok:
        print(f"[cockpit] setChatMenuButton OK for chat_id={user_id}")
    else:
        print(f"[cockpit] setChatMenuButton failed: {err}", file=sys.stderr)

    # Slash-command → handler dispatch. The chat menu button opens this same
# list, so any command added here becomes accessible from the permanent
# MENU icon with no extra work.
_SLASH_HANDLERS: dict[str, Any] = {}


def _build_slash_handlers() -> dict[str, Any]:
    """Build the /<command> → callable map. Called on first invocation."""
    if _SLASH_HANDLERS:
        return _SLASH_HANDLERS
    from sentinel.cockpit.menu import (
        view_dashboard, view_daemon, view_killed, view_investigate,
        view_search, view_heartbeat, view_schedule, view_alerts, view_log,
    )
    _SLASH_HANDLERS.update({
        "/start":       view_dashboard,
        "/menu":        view_dashboard,
        "/dashboard":   view_dashboard,
        "/daemon":      view_daemon,
        "/killed":      view_killed,
        "/investigate": view_investigate,
        "/search":      view_search,
        "/heartbeat":   view_heartbeat,
        "/schedule":    view_schedule,
        "/alerts":      view_alerts,
        "/logs":        lambda: view_log(page=0),
    })
    return _SLASH_HANDLERS


def _dispatch_slash_command(cmd: str) -> tuple[str, Any] | None:
    """Look up a /command and return its rendered (text, kb) or just text."""
    handlers = _build_slash_handlers()
    handler = handlers.get(cmd.lower())
    if handler is None:
        return None
    result = handler()
    if isinstance(result, tuple):
        text, kb = result
        return text, kb
    return result, None


def _dispatch_text_command(text_in: str) -> tuple[str, Any] | None:
    """Dispatch a /command that may carry an argument (e.g. /p prospector)."""
    parts = text_in.split(None, 1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""
    if cmd == "/p":
        from sentinel.cockpit.menu import view_project, _projects as _mprojects
        name = arg.strip()
        if not name:
            return (
                "❓ <code>/p</code> needs a project name. Try /projects to list.",
                {"inline_keyboard": []},
            )
        if name not in _mprojects():
            return (
                f"❓ Project <code>{_xml_escape(name)}</code> not found. Try /projects.",
                {"inline_keyboard": []},
            )
        return view_project(name)
    return _dispatch_slash_command(cmd)


def _send_with_keyboard(chat_id: str, text: str, kb: dict | None = None) -> bool:
    """Send a message that includes the persistent reply keyboard.

    Telegram allows only one reply_markup type per message. When this
    helper is called with `kb=None`, we attach the ReplyKeyboardMarkup
    so the nav bar stays visible. When called with an inline-keyboard
    `kb`, the inline buttons are attached on that message; the reply
    keyboard (if previously set) persists independently.
    """
    from sentinel.cockpit.menu import _api, _t
    token = _t()
    if not token:
        return False
    body: dict = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if kb is not None:
        body["reply_markup"] = kb
    else:
        body["reply_markup"] = _reply_keyboard_markup()
    return _api("sendMessage", body)


# ═══════════════════════════════════════════════════════════════════════════
#  App factory
# ═══════════════════════════════════════════════════════════════════════════

def create_app() -> FastAPI:
    app = FastAPI(title="Mothership", docs_url=None, redoc_url=None)

    @app.on_event("startup")
    async def _on_startup():
        register_persistent_menu()

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

            if text_in.lower() in ("/start", "/menu", "menu", "/dashboard"):
                text, kb = view_dashboard()
                send(chat_id, text, kb)
            elif text_in.startswith("/"):
                cmd = text_in.split()[0].lower()
                result = _dispatch_slash_command(cmd)
                if result is not None:
                    text, kb = result
                    send(chat_id, text, kb)
                else:
                    send(
                        chat_id,
                        f"❓ Unknown command: <code>{_xml_escape(cmd[:60])}</code>\n"
                        f"     Try /menu for the dashboard.",
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

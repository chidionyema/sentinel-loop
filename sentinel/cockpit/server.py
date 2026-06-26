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


def _load_dotenv() -> None:
    """Load secrets from ~/.hermes/.env into os.environ (if not already set)."""
    env_path = os.path.expanduser("~/.hermes/.env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key and val and key not in os.environ:
                        os.environ[key] = val
    except Exception:
        pass


# ---------------------------------------------------------------------------
#  Direct chat → Otto (DeepSeek API via bridge, ~3-5s latency)
# ---------------------------------------------------------------------------

# Persistent Otto server (full hermes agent with memory, soul, tools, verification).
# Started once, shared across all chat requests.
_OTTO_SERVER = "http://127.0.0.1:8802"
_OTTO_TIMEOUT_S = 120.0


def _telegram_api(method: str, body: dict, timeout: float = 10.0) -> dict | None:
    """Call Telegram Bot API. Returns parsed JSON or None on failure."""
    import urllib.request as _ur
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return None
    try:
        url = f"https://api.telegram.org/bot{token}/{method}"
        req = _ur.Request(
            url,
            data=_json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with _ur.urlopen(req, timeout=timeout) as resp:
            return _json.loads(resp.read())
    except Exception:
        return None


def _send_thinking(chat_id: str) -> str | None:
    """Send a 'thinking' placeholder. Returns message_id or None."""
    result = _telegram_api("sendMessage", {
        "chat_id": chat_id,
        "text": "💭 <i>Otto is thinking...</i>",
        "parse_mode": "HTML",
    })
    if result and result.get("ok"):
        return str(result["result"]["message_id"])
    return None


def _edit_response(chat_id: str, message_id: str, text: str) -> bool:
    """Edit a sent message with the real response."""
    safe = text[:4000]  # Telegram limit
    result = _telegram_api("editMessageText", {
        "chat_id": chat_id,
        "message_id": int(message_id),
        "text": safe,
        "parse_mode": "HTML",
    })
    return result is not None and result.get("ok", False)


def _call_otto(prompt: str) -> str | None:
    """Call the persistent Otto server. Returns response text or None on failure."""
    import urllib.request as _ur
    try:
        body = _json.dumps({"prompt": prompt}).encode("utf-8")
        req = _ur.Request(
            f"{_OTTO_SERVER}/chat",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with _ur.urlopen(req, timeout=_OTTO_TIMEOUT_S) as resp:
            return resp.read().decode("utf-8").strip()
    except Exception as e:
        print(f"[otto] server error: {e}", file=sys.stderr)
        return None


def _process_chat_in_background(chat_id: str, prompt: str) -> None:
    """Send thinking placeholder, call Otto, edit with response.
    Designed to run in a background thread so the webhook returns 200 quickly."""
    msg_id = _send_thinking(chat_id)
    if msg_id is None:
        # Fallback: send response directly
        from sentinel.cockpit.menu import send
        response = _call_otto(prompt)
        if response:
            send(chat_id, _xml_escape(response))
        else:
            send(chat_id, "⚠ Sorry, I couldn't reach the AI. Try again in a moment.")
        return

    response = _call_otto(prompt)
    if response:
        _edit_response(chat_id, msg_id, _xml_escape(response))
    else:
        _edit_response(chat_id, msg_id, "⚠ Sorry, I couldn't get a response. Try again.")


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
    {"command": "mothership",  "description": "Mothership home — alias for /dashboard"},
    {"command": "start",       "description": "Alias for /dashboard"},
    {"command": "tasks",       "description": "List escalated tasks pending approval"},
    {"command": "estate",      "description": "Estate control panel — pause/resume/health"},
    {"command": "cron",        "description": "Cron jobs — enabled/disabled status"},
    {"command": "daemons",     "description": "Daemon status — start/stop safe daemons"},
    {"command": "request",     "description": "Request a feature — opens a work item"},
    {"command": "cicd",        "description": "CI/CD pipeline status — re-run low-risk jobs"},
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


# ── Webhook self-healing: re-set on startup so it never stays cleared ──────

def ensure_webhook() -> None:
    """Ensure the Telegram webhook is correctly set.

    Called at cockpit startup. Checks current webhook state via getWebhookInfo.
    If the webhook URL is missing, wrong, or errored, re-sets it. Never raises —
    failures are logged but don't crash the server.

    This is the single most important reliability mechanism: it makes cockpit
    restarts self-healing instead of leaving the webhook in a broken state.
    """
    import urllib.request as _ur

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("[cockpit] ensure_webhook: no TELEGRAM_BOT_TOKEN", file=sys.stderr)
        return

    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
    if not secret:
        print("[cockpit] ensure_webhook: no TELEGRAM_WEBHOOK_SECRET", file=sys.stderr)
        return

    # Find the ngrok public URL
    ngrok_url = ""
    try:
        req = _ur.Request("http://127.0.0.1:4040/api/tunnels")
        with _ur.urlopen(req, timeout=3) as resp:
            data = _json.loads(resp.read())
            tunnels = data.get("tunnels", [])
            if tunnels:
                ngrok_url = tunnels[0].get("public_url", "")
    except Exception as e:
        print(f"[cockpit] ensure_webhook: cannot read ngrok URL: {e}", file=sys.stderr)
        return

    if not ngrok_url:
        print("[cockpit] ensure_webhook: ngrok tunnel not found", file=sys.stderr)
        return

    expected_url = f"{ngrok_url}/webhooks/telegram"

    # Check current webhook state
    needs_reset = False
    try:
        req = _ur.Request(f"https://api.telegram.org/bot{token}/getWebhookInfo")
        with _ur.urlopen(req, timeout=10) as resp:
            info = _json.loads(resp.read()).get("result", {})
            current_url = info.get("url", "")
            last_error = info.get("last_error_message")

            if current_url != expected_url:
                needs_reset = True
                print(f"[cockpit] Webhook URL mismatch: current='{current_url}' expected='{expected_url}'", file=sys.stderr)
            elif last_error and last_error != "none":
                needs_reset = True
                print(f"[cockpit] Webhook has errors: {last_error[:120]}", file=sys.stderr)
            else:
                print(f"[cockpit] Webhook OK: {expected_url}", file=sys.stderr)
    except Exception as e:
        print(f"[cockpit] ensure_webhook: getWebhookInfo failed: {e}", file=sys.stderr)
        return

    if not needs_reset:
        return

    # Re-set the webhook
    try:
        boundary = "boundary" + str(int(time.time()))
        body_parts = []
        for name, value in [
            ("url", expected_url),
            ("secret_token", secret),
            ("allowed_updates", '["message","callback_query"]'),
            ("max_connections", "5"),
        ]:
            body_parts.append(f"--{boundary}".encode())
            body_parts.append(f'Content-Disposition: form-data; name="{name}"'.encode())
            body_parts.append(b"")
            body_parts.append(value.encode())
        body_parts.append(f"--{boundary}--".encode())
        body = b"\r\n".join(body_parts)

        req = _ur.Request(
            f"https://api.telegram.org/bot{token}/setWebhook",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with _ur.urlopen(req, timeout=15) as resp:
            result = _json.loads(resp.read())
            if result.get("ok"):
                print(f"[cockpit] Webhook re-set: {expected_url}", file=sys.stderr)
            else:
                print(f"[cockpit] Webhook set failed: {result}", file=sys.stderr)
    except Exception as e:
        print(f"[cockpit] ensure_webhook: setWebhook failed: {e}", file=sys.stderr)


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

    def _estate_cmd():
        """Shell for /estate — the full handler is callback-driven."""
        return ("🏛 Estate control — tap a button:\n"
                "⏸ Pause / ▶️ Resume / 🔄 Refresh / ♻️ Restart / 📋 Active / 🪵 Logs / ⛽ Fuel",
                {"inline_keyboard": [
                    [{"text": "🔄 Refresh", "callback_data": "estate:refresh"},
                     {"text": "⏸ Pause", "callback_data": "estate:pause"}],
                    [{"text": "📋 Active", "callback_data": "estate:list_active"},
                     {"text": "🪵 Logs", "callback_data": "estate:view_logs"}],
                    [{"text": "⛽ Fuel", "callback_data": "estate:system_fuel"},
                     {"text": "📋 Cron", "callback_data": "estate:cron"}],
                    [{"text": "🖥 Daemons", "callback_data": "estate:daemons"}],
                ]})

    def _tasks_cmd():
        """Shell for /tasks — the full handler is callback-driven."""
        return ("📋 Tasks — tap to list escalated tasks needing approval.",
                {"inline_keyboard": [
                    [{"text": "📋 List Escalated", "callback_data": "task:list"}],
                ]})

    def _cron_cmd():
        """Shell for /cron."""
        return ("📋 Cron jobs — tap to view all.",
                {"inline_keyboard": [
                    [{"text": "📋 List Jobs", "callback_data": "estate:cron"}],
                ]})

    def _daemons_cmd():
        """Shell for /daemons."""
        return ("🖥 Daemon control — tap to view and manage.",
                {"inline_keyboard": [
                    [{"text": "🖥 View Daemons", "callback_data": "estate:daemons"}],
                ]})

    def _cicd_cmd():
        """Shell for /cicd."""
        return ("🔄 CI/CD — tap to list recent workflow runs.",
                {"inline_keyboard": [
                    [{"text": "🔄 List Runs", "callback_data": "cicd:list"}],
                    [{"text": "🏠 Home", "callback_data": "nv:dash:"}],
                ]})

    def _request_cmd():
        """Shell for /request — tells user how to use the feature."""
        return ("➕ Request a feature — type /request followed by your request,\n"
                "e.g. `/request add CSV export to prospector`\n\n"
                "Or tap the ➕ Request button from the nav bar.",
                {"inline_keyboard": [
                    [{"text": "🏠 Home", "callback_data": "nv:dash:"}],
                ]})

    _SLASH_HANDLERS.update({
        "/start":       view_dashboard,
        "/menu":        view_dashboard,
        "/dashboard":   view_dashboard,
        "/mothership":  view_dashboard,
        "/daemon":      view_daemon,
        "/killed":      view_killed,
        "/investigate": view_investigate,
        "/search":      view_search,
        "/heartbeat":   view_heartbeat,
        "/schedule":    view_schedule,
        "/alerts":      view_alerts,
        "/logs":        lambda: view_log(page=0),
        "/tasks":       _tasks_cmd,
        "/estate":      _estate_cmd,
        "/cron":        _cron_cmd,
        "/daemons":     _daemons_cmd,
        "/cicd":        _cicd_cmd,
        "/request":     _request_cmd,
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
    if cmd == "/request" and arg.strip():
        # WI-5: /request <text> → open a coordinator task
        return (f"➕ Request received. Opening work item…", None)  # handled below
    return _dispatch_slash_command(cmd)


def _send_with_keyboard(chat_id: str, text: str, kb: dict | None = None) -> bool:
    """Send a message. If kb is None, send only the persistent reply keyboard.

    When kb is provided (inline keyboard), we send the inline message AND
    separately establish the reply keyboard so it appears on the phone.
    Telegram shows only one reply_markup type per message, so we use two
    sends: one with the content + inline buttons, one with just the nav bar.
    Once sent, the ReplyKeyboardMarkup persists across subsequent messages.
    """
    from sentinel.cockpit.menu import _api, _t, _reply_keyboard_markup
    token = _t()
    if not token:
        return False

    # Always send the content message first (with inline keyboard if provided)
    body: dict = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if kb is not None:
        body["reply_markup"] = kb
    _api("sendMessage", dict(body))

    # Establish the persistent reply keyboard on /start and /dashboard.
    # We send it as a tiny invisible message so it doesn't clutter the chat.
    # Telegram's ReplyKeyboardMarkup, once sent, persists until replaced.
    nav_body: dict = {
        "chat_id": chat_id,
        "text": "▾",  # minimal visible marker — Telegram requires non-empty text
        "reply_markup": _reply_keyboard_markup(),
    }
    _api("sendMessage", nav_body)
    return True


async def _dispatch_nav(chat_id: str, cb_data: str, send_fn) -> None:
    """WI-1: Dispatch a nav button tap to the right screen handler.

    Mirrors handle_callback but for ReplyKeyboardMarkup taps that arrive
    as plain text, not callback queries.
    """
    from sentinel.cockpit.menu import (
        view_dashboard, view_projects, view_daemon,
        handle_estate_callback, handle_task_callback,
        handle_callback,
    )
    if cb_data == "nv:dash:":
        text, kb = view_dashboard()
        _send_with_keyboard(chat_id, text, kb)
    elif cb_data == "nv:projects:":
        text, kb = view_projects()
        send_fn(chat_id, text, kb)
    elif cb_data == "estate:refresh":
        await handle_estate_callback("estate:refresh", chat_id, "")
    elif cb_data == "task:list":
        await handle_task_callback("task:list", chat_id, "")
    elif cb_data == "nv:deploy:":
        # Deploy nav button → show project picker so user can tap into a
        # project and use its per-project deploy button
        text, kb = view_projects()
        send_fn(chat_id,
                "🚀 Deploy — tap a project below, then use its deploy button.",
                kb)
    elif cb_data == "cicd:list":
        # CI/CD nav button → call the real CI/CD handler
        await handle_callback("cicd:list", chat_id, "")
    elif cb_data == "nv:request:":
        from sentinel.cockpit.menu import _PENDING_INTAKE
        _PENDING_INTAKE[chat_id] = True
        send_fn(chat_id,
                "➕ What would you like built?\n\n"
                "Type your request now — your next message will be filed as a work item.", 
                {"inline_keyboard": [
                    [{"text": "🏠 Home", "callback_data": "nv:dash:"}],
                ]})


# ═══════════════════════════════════════════════════════════════════════════
#  App factory
# ═══════════════════════════════════════════════════════════════════════════

def create_app() -> FastAPI:
    app = FastAPI(title="Mothership", docs_url=None, redoc_url=None)

    @app.on_event("startup")
    async def _on_startup():
        # Load secrets from .env — ensures the cockpit always has them
        # regardless of how the process was started.
        _load_dotenv()
        register_persistent_menu()
        ensure_webhook()

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

        # ── Route text message → dashboard OR Otto relay ───────────────
        # /start and "menu" re-render the cockpit dashboard (preserved).
        # Slash commands route through _dispatch_slash_command (else "unknown").
        # WI-1: nav button labels (from ReplyKeyboardMarkup) are matched BEFORE
        # relaying to Otto — a tap on "🏠 Home" renders the dashboard, not chat.
        # Plain conversational text is relayed to the Otto server (:8802) in a
        # background thread, which replies back to this same chat — never to the
        # coordinator's automation queue (ESTATE_NORTH_STAR.md:129-132 routing fix).
        # Non-text messages (stickers, photos) fall through to the dashboard.
        if message and chat_id:
            from sentinel.cockpit.menu import (
                scan_projects, view_dashboard, send,
                _NAV_BUTTON_MAP, _reply_keyboard_markup,
            )
            text_in = (message.get("text") or "").strip()

            if text_in.lower() in ("/start", "/menu", "menu", "/dashboard"):
                text, kb = view_dashboard()
                _send_with_keyboard(chat_id, text, kb)
            elif text_in in _NAV_BUTTON_MAP:
                # WI-1: nav button tap → dispatch to handler
                cb_data = _NAV_BUTTON_MAP[text_in]
                await _dispatch_nav(chat_id, cb_data, send)
            elif text_in.startswith("/"):
                # WI-5: /request <text> → open coordinator task
                parts = text_in.split(None, 1)
                cmd = parts[0].lower()
                if cmd == "/request" and len(parts) > 1:
                    from sentinel.cockpit.menu import _handle_intake_request
                    _handle_intake_request(chat_id, parts[1], send)
                else:
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
                # Wave 1: stateful intake — if user tapped ➕ Request,
                # capture their next message as a feature request
                from sentinel.cockpit.menu import _PENDING_INTAKE, _handle_intake_request
                if _PENDING_INTAKE.pop(chat_id, False):
                    _handle_intake_request(chat_id, text_in, send)
                else:
                    # Process via Otto directly — no queue delay
                    import threading
                    t = threading.Thread(
                        target=_process_chat_in_background,
                        args=(chat_id, text_in),
                        daemon=True,
                    )
                    t.start()
            else:
                text, kb = view_dashboard()
                _send_with_keyboard(chat_id, text, kb)

        # ── Callback routing ──────────────────────────────────────
        dispatch_result = None
        if callback_query:
            from sentinel.cockpit.dispatcher import dispatch, execution_enabled
            from sentinel.cockpit.menu import (
                handle_callback, answer, send,
                scan_projects, view_dashboard,
                handle_estate_callback, handle_task_callback,
                handle_prompt_callback,
            )

            data = callback_query.get("data", "") or ""

            # Navigation + daemon → menu.py
            if data.startswith("nv:") or data.startswith("ac:") or (data.startswith("d") and len(data) >= 2 and data[1] in "halgcdsxkirz"):
                await handle_callback(data, chat_id, callback_query.get("id", ""))

            # WI-3 deploy + WI-4 cicd → handle_callback (with confirm + rerun sub-routes)
            elif data.startswith("deploy:") or data.startswith("deploy_confirm:") or data.startswith("cicd:"):
                answer(callback_query.get("id", ""))
                await handle_callback(data, chat_id, callback_query.get("id", ""))

            # Estate control panel (pause/resume/refresh/restart/logs/fuel)
            elif data.startswith("estate:"):
                answer(callback_query.get("id", ""))
                await handle_estate_callback(data, chat_id, callback_query.get("id", ""))

            # Task approval (list + cancel; approve is Claude-only — see fence spec §0.2)
            elif data.startswith("task:"):
                answer(callback_query.get("id", ""))
                await handle_task_callback(data, chat_id, callback_query.get("id", ""))

            # Prompt update approval (y/n)
            elif data.startswith("update_prompt:"):
                answer(callback_query.get("id", ""))
                await handle_prompt_callback(data, chat_id, callback_query.get("id", ""))

            # Git/project buttons (gs: gp: gl:) → handle_callback
            elif data.startswith("gs:") or data.startswith("gp:") or data.startswith("gl:"):
                answer(callback_query.get("id", ""))
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

        # Wave 2: Relay monitor alerts to Telegram
        source = body.get("source", "unknown")
        title = body.get("title") or body.get("alert", {}).get("title", "Monitor Alert")
        message = body.get("message") or body.get("text", "")
        severity = body.get("severity", "info")
        icon = {"critical": "🔴", "error": "🔴", "warning": "🟡", "info": "ℹ️"}.get(severity, "ℹ️")

        if title or message:
            from sentinel.cockpit.menu import send as _send_menu
            allowed_raw = os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "")
            if allowed_raw:
                first_id = allowed_raw.split(",")[0].strip()
                if first_id:
                    text = f"{icon} **{title}** (from {source})\n{message[:800]}"
                    _send_menu(first_id, text)

        return JSONResponse({"status": "received", "source": source, "relayed": bool(title or message)})

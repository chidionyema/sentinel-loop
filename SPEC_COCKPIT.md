# Build Specification: Unified DevOps Telegram Cockpit

**Target:** Extend the Hermes Sentinel Loop with a human-operable DevOps control plane 
running entirely inside Telegram, per the Unified DevOps Telegram Cockpit spec.

**Foundation:** Reuses sentinel daemon infrastructure (gateway transport, coordinator state 
machine, watchdog process management, fiscal sentry, security fences).

**Verify command:**
```bash
bash scripts/check-integrity.sh && python3 -m pytest tests/ -q && python3 -m pytest verify/ -q
```

---

## Subsystem 1: HTTP Webhook Server (`sentinel/cockpit/server.py`)

### Requirements
- **Framework:** FastAPI (add `fastapi>=0.115.0` and `uvicorn>=0.34.0` to pyproject.toml)
- **Endpoints:**
  - `POST /webhooks/telegram` — Telegram Bot API webhook ingestion. Validates the update, 
    extracts `message.text` or `callback_query.data`, routes to UI engine.
  - `POST /webhooks/github` — GitHub push/workflow events. Validates `X-Hub-Signature-256` 
    header (via ACL subsystem #5). Routes to GitHub processor (#3).
  - `POST /webhooks/monitor` — Universal monitoring alert ingestion from Sentry, Better Stack, 
    Logtail, Datadog. Validates source authenticity (via ACL #5). Routes to monitor ingestion (#4).
  - `GET /health` — Returns `{"status": "ok", "daemon": "ai.hermes.gateway"}`.
- **Binding:** Must bind exclusively to `127.0.0.1` (configurable via `COCKPIT_HOST` env). 
  Port from `COCKPIT_PORT` env (default 8800).
- **Lifecycle:** Runs as a uvicorn server inside the gateway daemon. Must not conflict with 
  existing sentinel gateway notification path.
- **Static token auth on webhook paths:** `X-API-Key` header validated against env var for 
  monitor/webhook endpoints (not for Telegram, which uses its own token).

### Data Flow
```
[Telegram API] → POST /webhooks/telegram → ACL.from_id_check() → UI Engine router
[GitHub]       → POST /webhooks/github   → ACL.verify_github_hmac() → GitHub Processor
[Sentry/etc]   → POST /webhooks/monitor  → ACL.verify_monitor_source() → Monitor Ingestion
```

---

## Subsystem 2: Telegram UI Engine (`sentinel/cockpit/ui_engine.py`)

### Requirements
- **Callback data format:** `action:target:id` (e.g., `git_pull:project-alpha:main`)
- **Parser:** `parse_callback(data: str) -> dict` returning `{"action": "...", "target": "...", "id": "..."}`.
  Must raise `ValueError` on malformed data (wrong number of segments, empty segments).
- **Menu tree state machine (3 levels):**
  - **Level 0: Global Dashboard** — System health summary, component navigation buttons.
    Buttons: `[🖥 Projects] [🔄 CI/CD] [📊 Monitoring] [⚙️ Config]`
  - **Level 1: Sub-System Controllers**
    - Projects menu shows discovered workspace projects with actions per project.
    - CI/CD shows recent deployments, active branches.
    - Monitoring shows alert status, recent incidents.
  - **Level 2: Resource Views** — Specific project actions (git pull, npm run dev, view logs, 
    restart), active log streams, deployment history.
- **Navigation:** `navigate(current_level, target) -> (new_level, keyboard_markup)` method.
  Must handle back-navigation: each sub-menu includes `[◀ Back]` button.
- **Anti-spam persistence:** `get_edit_state(chat_id) -> dict` tracks the last message_id 
  for each chat. When rendering a new screen, use `editMessageText` if a message_id exists 
  for that chat, otherwise `sendMessage`. New messages store their message_id.
- **Keyboard builder:** `build_inline_keyboard(buttons: list[list[dict]]) -> InlineKeyboardMarkup` 
  helper. Each button dict has `text`, `callback_data`, optional `url`.
- **Alert modal:** `send_alert_callback(callback_query_id, text, show_alert=True)` wraps 
  `answerCallbackQuery`.

### Chat State SQLite Table
```sql
CREATE TABLE IF NOT EXISTS cockpit_chat_state (
    chat_id TEXT PRIMARY KEY,
    current_level INTEGER NOT NULL DEFAULT 0,
    current_context TEXT,          -- JSON: {target, id, params...}
    last_message_id TEXT,
    last_updated TEXT NOT NULL
);
```
Stored in `state.db` via `StateDB` or a dedicated `CockpitDB`.

---

## Subsystem 3: GitHub Webhook Processor (`sentinel/cockpit/github_processor.py`)

### Requirements
- **Push event handler:** `process_push_event(payload: dict) -> dict`:
  - Extracts: `repository.full_name`, `ref` (branch), `head_commit.message`, `head_commit.author.name`
  - Generates a unique deployment token: `sha256(f"{repo}:{sha}:{timestamp}:{secret}")[:16]`
  - Returns a Telegram message block: commit info with `[Deploy Code]` callback button 
    (`deploy:repo:sha`)
  - The message is sent by the UI engine via the gateway transport
- **Workflow event handler:** `process_workflow_event(payload: dict) -> dict | None`:
  - Waits for `workflow_run.status == "completed"`
  - Maps `conclusion`: `"success"` → 🟢, `"failure"` → 🔴, else 🟡
  - If a previous status message exists for this workflow, returns an `edit` instruction 
    to mutate the status indicator. Otherwise returns a new message.
- **Branch name parsing:** `parse_branch_ref(ref: str) -> str` extracts branch from 
  `refs/heads/main` → `main`.

---

## Subsystem 4: Monitoring Alert Ingestion (`sentinel/cockpit/monitor_ingestion.py`)

### Requirements
- **Multi-source parser:** `parse_alert(payload: dict, source: str) -> AlertData`:
  - `source` is one of: `"sentry"`, `"betterstack"`, `"logtail"`, `"datadog"`, `"generic"`
  - Returns a normalized `AlertData` dataclass with:
    - `service: str` — affected service name
    - `message: str` — human-readable alert message
    - `severity: str` — one of `"critical"`, `"warning"`, `"info"`
    - `stack_trace: str | None` — stack snippet if available
    - `source: str` — original source system
    - `raw: dict` — original payload
- **State override engine:** `should_override(alert: AlertData, current_state: dict) -> bool`:
  - Returns `True` if alert severity is `"critical"` and the current chat is not already 
    displaying a critical alert
  - Otherwise returns `False`
- **Emergency button builder:** `build_emergency_buttons(alert: AlertData) -> list[list[dict]]`:
  - Returns inline keyboard rows: `[Restart Container] [Rollback] [Mute Alert]`
  - Callback data: `restart:service_name:container`, `rollback:service_name:latest`, 
    `mute:service_name:duration_minutes`
- **Critical alert message formatter:** Formats alert as:
  ```
  🚨 CRITICAL: {service}
  {message}
  
  Stack:
  ```
  {stack_trace truncated to 500 chars}
  ```
  ```
  Followed by emergency buttons.

---

## Subsystem 5: External ACL & Webhook Auth (`sentinel/cockpit/acl.py`)

### Requirements
- **Telegram user ACL:** `validate_telegram_user(from_id: int) -> bool`:
  - Reads `TELEGRAM_ALLOWED_USER_IDS` from environment (comma-separated integers)
  - Returns `True` if `from_id` is in the allowed set
  - Logs unauthorized access attempts with timestamp + user ID
  - **Never falls back to conversational/text processing for unauthorized users**
- **GitHub HMAC verification:** `verify_github_hmac(payload: bytes, signature_header: str) -> bool`:
  - Reads `GITHUB_WEBHOOK_SECRET` from environment
  - Computes HMAC-SHA256 of payload with secret
  - Compares against `X-Hub-Signature-256` header (format: `sha256=...`)
  - Uses `hmac.compare_digest` for timing-safe comparison
- **Monitor source verification:** `verify_monitor_source(source: str, payload: dict) -> bool`:
  - Each source has its own verification method (API key, shared secret, IP allowlist)
  - `MONITOR_API_KEYS` env var contains JSON: `{"sentry": "key1", "datadog": "key2", ...}`
  - Checks payload for matching API key or token in expected field
- **Shell command mapping:** `COMMAND_REGISTRY` — a hardcoded dict mapping action names to 
  command templates:
  ```python
  COMMAND_REGISTRY = {
      "git_pull": "git -C {workspace} pull origin {branch}",
      "npm_dev": "cd {workspace} && npm run dev",
      "npm_build": "cd {workspace} && npm run build",
      "docker_up": "cd {workspace} && docker compose up -d",
      "docker_down": "cd {workspace} && docker compose down",
      "systemctl_restart": "systemctl restart {service}",
      "git_status": "git -C {workspace} status --short",
      "git_log": "git -C {workspace} log --oneline -10",
  }
  ```
- **Shell validation:** `validate_workspace_path(workspace: str, root: str) -> bool`:
  - Resolves the full path: `os.path.join(root, workspace)`
  - Verifies the resolved path starts with the root directory (no traversal)
  - Uses `os.path.realpath` to resolve symlinks
  - Returns `True` only if the path exists and is within root

---

## Subsystem 6: Workspace Project Scanner (`sentinel/cockpit/workspace_scanner.py`)

### Requirements
- **Directory scanner:** `scan_workspace(root_dir: str) -> list[ProjectInfo]`:
  - Scans `root_dir` non-recursively (one level only)
  - Returns `ProjectInfo` dataclass for each directory:
    ```python
    @dataclass
    class ProjectInfo:
        name: str           # directory name
        path: str           # absolute path
        markers: list[str]  # detected marker files
        has_git: bool
        package_manager: str | None  # "npm", "pip", "poetry", "cargo", None
    ```
- **Marker file detection:** Checks for:
  - `.git` directory or file → `has_git = True`, adds `".git"` to markers
  - `package.json` → `package_manager = "npm"`, adds `"package.json"`
  - `requirements.txt` or `pyproject.toml` → `package_manager = "pip"` or `"poetry"`
  - `Cargo.toml` → `package_manager = "cargo"`
  - `Dockerfile` or `docker-compose.yml` → adds to markers
  - `Makefile` → adds to markers
- **Project registry persistence:** `save_registry(projects: list[ProjectInfo], db_path: str)` 
  stores in SQLite table:
  ```sql
  CREATE TABLE IF NOT EXISTS project_registry (
      name TEXT PRIMARY KEY,
      path TEXT NOT NULL,
      markers TEXT NOT NULL,       -- JSON array
      has_git INTEGER NOT NULL,
      package_manager TEXT,
      last_scanned TEXT NOT NULL
  );
  ```
- **Rescan:** `rescan(root_dir: str, db_path: str) -> list[ProjectInfo]` re-scans and updates 
  the registry, returns projects that were added or changed since last scan.

---

## Subsystem 7: Perimeter Hardening (`sentinel/cockpit/perimeter.py`)

### Requirements
- **Binding configuration:** `get_bind_config() -> tuple[str, int]`:
  - Returns `(host, port)` from env vars `COCKPIT_HOST` (default `"127.0.0.1"`) and 
    `COCKPIT_PORT` (default `8800`)
  - Validates host is either `127.0.0.1` or `localhost` (refuses `0.0.0.0`)
- **Tunnel readiness check:** `verify_tunnel_config() -> dict`:
  - Checks for presence of `cloudflared` binary or ngrok config
  - Returns `{"ready": bool, "provider": str | None, "message": str}`
  - Does NOT start or manage the tunnel — just reports status
- **Environment config validator:** `validate_cockpit_env() -> list[str]`:
  - Checks required env vars are set: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_IDS`
  - Checks optional but recommended: `GITHUB_WEBHOOK_SECRET`, `MONITOR_API_KEYS`
  - Returns list of missing/warning messages

---

## Integration Requirements

### Gateway Extension
The existing `HermesGateway` must be extended with:
- A `set_ui_engine(engine)` method to inject the UI engine for inbound message routing
- A `handle_telegram_update(update: dict)` method that routes to the UI engine
- The HTTP transport must support inbound webhook mode (receiving, not just sending)

### Config File (`config/cockpit-settings.json`)
```json
{
    "workspace_root": "~/Documents/code",
    "server": {
        "host": "127.0.0.1",
        "port": 8800
    },
    "telegram": {
        "polling_mode": false,
        "webhook_url": null
    },
    "monitor_sources": ["sentry", "betterstack", "logtail", "datadog"],
    "commands": {
        "timeout_seconds": 120,
        "max_output_lines": 50
    },
    "ui": {
        "anti_spam_enabled": true,
        "max_buttons_per_row": 3
    }
}
```

### New Dependencies
Add to `pyproject.toml`:
- `fastapi>=0.115.0`
- `uvicorn[standard]>=0.34.0`

---

## Acceptance Criteria

Every subsystem must pass its corresponding test class in `tests/test_cockpit.py` AND 
`verify/test_cockpit_held_out.py`. No subsystem is "done" until all its tests pass.

### Subsystem verification mapping:

| Subsystem | Visible Tests | Held-Out Tests | Key Behaviors Verified |
|---|---|---|---|
| 1. HTTP Server | `TestCockpitServer` | `TestHeldOut_CockpitServer` | Endpoints routing, binding, health, 404s |
| 2. UI Engine | `TestCockpitUIEngine` | `TestHeldOut_CockpitUIEngine` | Callback parsing, menu navigation, edit state, alert modal |
| 3. GitHub Processor | `TestCockpitGitHub` | `TestHeldOut_CockpitGitHub` | Push parsing, workflow status, deploy token, branch extraction |
| 4. Monitor Ingestion | `TestCockpitMonitor` | `TestHeldOut_CockpitMonitor` | Multi-source parsing, severity override, emergency buttons |
| 5. ACL & Auth | `TestCockpitACL` | `TestHeldOut_CockpitACL` | User validation, HMAC, workspace path safety, command mapping |
| 6. Workspace Scanner | `TestCockpitScanner` | `TestHeldOut_CockpitScanner` | Marker detection, project registry, rescan diff |
| 7. Perimeter | `TestCockpitPerimeter` | `TestHeldOut_CockpitPerimeter` | Bind config, tunnel check, env validation |

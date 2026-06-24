# Mothership Handbook

**A complete reference for the `sentinel.cockpit` control plane — how Telegram, ngrok, the FastAPI webhook receiver, the menu engine, and the shell-free action dispatcher hang together, and how to operate the whole thing.**

> Audience: the operator (you), and any future contributor or incident-responder who needs to debug "the panel isn't showing" in under five minutes.

---

## Table of Contents

1. [TL;DR](#1-tldr)
2. [What the Mothership is — and isn't](#2-what-the-mothership-is--and-isnt)
3. [System landscape](#3-system-landscape)
4. [Filesystem layout](#4-filesystem-layout)
5. [Process inventory](#5-process-inventory)
6. [The webhook delivery chain](#6-the-webhook-delivery-chain)
7. [Configuration reference](#7-configuration-reference)
8. [The menu catalog](#8-the-menu-catalog)
9. [The action execution model](#9-the-action-execution-model)
10. [Security model (7 layers)](#10-security-model-7-layers)
11. [Operational procedures](#11-operational-procedures)
12. [Failure mode catalog](#12-failure-mode-catalog)
13. [Known fragility points](#13-known-fragility-points)
14. [Glossary](#14-glossary)

---

## 1. TL;DR

The Mothership is a **Telegram-based control plane** for the projects under `~/Documents/code` and for the Prospector scheduler in particular. The "panel" the operator interacts with is the **inline-keyboard menu that Otto (the Telegram bot) renders in chat** — there is no web dashboard for the Mothership.

```
Operator ──► Telegram ──► Telegram Bot API ──► ngrok tunnel ──► :8801 (uvicorn)
                                                                     │
                                                                     ▼
                                                       sentinel.cockpit.server
                                                                     │
                                                                     ▼
                                                      menu.view_*() → Telegram API
                                                                     │
                                                                     ▼
                                                          InlineKeyboardMarkup in chat
```

Two conditions must both hold for the panel to appear in chat:

1. **Telegram knows where to deliver updates** — `getWebhookInfo.url` is non-empty and points at a route that reaches `POST /webhooks/telegram` on the running uvicorn.
2. **The dispatch path is intact** — uvicorn is bound, ACL passes, `view_dashboard()` renders, the menu sends itself back via `sendMessage`.

When the panel doesn't appear, the failure is almost always (1).

---

## 2. What the Mothership is — and isn't

### Is

- A FastAPI webhook receiver (`sentinel.cockpit.server`) on `127.0.0.1:8801` (default).
- A Telegram-only UI surface — every view is an `InlineKeyboardMarkup` payload sent to the operator's chat via `sendMessage`.
- A **shell-free action executor** (`sentinel.cockpit.dispatcher`) that runs `git`, `npm`, `docker`, `pip`, `make`, and `systemctl` commands against validated workspaces, under a 120-second SIGKILL budget.
- A **multi-source ingest** endpoint — `POST /webhooks/telegram`, `POST /webhooks/github`, `POST /webhooks/monitor` — each with its own auth model.
- Bound to `127.0.0.1` by `perimeter.get_bind_config()` — **0.0.0.0 is forbidden**. External exposure is the responsibility of an upstream tunnel (ngrok, Cloudflare).

### Isn't

- Not a web dashboard. `GET /` returns `404 Not Found`. There is no HTML, no SPA, no `StaticFiles` mount, no `Jinja2Templates` in `sentinel.cockpit.*`.
- Not the same as the Hermes web dashboard (which lives in `~/.hermes/hermes-agent/`, port 9119, `hermes_cli/web_server.py` — entirely separate codebase).
- Not auto-managed. There is **no `ai.sentinel.cockpit.plist`** in `~/Library/LaunchAgents/`. The server runs in a terminal/launchd-managed uvicorn that has to be (re)started by hand or by whatever script the operator set up.

---

## 3. System landscape

Two systems share your machine and frequently get conflated. They are independent.

```
┌──────────────────────────────────────────┐   ┌──────────────────────────────────────┐
│              MOTHERSHIP                  │   │              HERMES AGENT            │
│  ~/Documents/code/sentinel-loop          │   │  ~/.hermes/hermes-agent              │
│                                          │   │                                      │
│  sentinel.cockpit.server     (:8801)     │   │  hermes_cli.web_server   (:9119)     │
│  sentinel.cockpit.runner     (:8811)     │   │  hermes_cli.main gateway (poll)      │
│                                          │   │  coordinator, progress, rsi,         │
│  Telegram UI: inline keyboard            │   │  watchdog daemons (LaunchAgents)     │
│  Web UI: NONE                            │   │                                      │
│                                          │   │  Web UI: full SPA                    │
│                                          │   │  Telegram: gateway long-poll         │
│                                          │   │                                      │
│  Bot: Otto (@Ottototbot) id 8656132729   │   │  Bot: may share same token (!!)      │
└──────────────────────────────────────────┘   └──────────────────────────────────────┘
              │                                              │
              └──────────── share ~/Documents/code ──────────┘
                              share DeepSeek key
```

**The conflict**: Telegram allows exactly one delivery mode per bot (webhook URL or polling). If the Hermes `gateway run --replace` polls the same bot token that the Mothership's webhook receives, the second one is starved. Today, the LaunchAgent for `ai.hermes.gateway` runs against the same Otto token — see §13.

---

## 4. Filesystem layout

### Code (read-only at runtime)

```
~/Documents/code/sentinel-loop/
├── pyproject.toml                        # Package metadata + deps
├── SPECIFICATION.md                      # High-level spec
├── SPEC_COCKPIT.md                       # Cockpit-specific spec
├── SHIP_READINESS_REVIEW.md              # Pre-launch gap analysis
├── LOOP.md                               # Day-to-day operating doc
├── config/                               # Static config (workspace_root, etc.)
├── launchd/                              # LaunchAgent plist templates
├── scripts/                              # Operational scripts (preflight, etc.)
├── skills/                               # Skill manifests
├── tests/                                # Pytest suite (incl. test_dispatcher)
├── verify/                               # Verify-chain entrypoints
└── sentinel/
    ├── cockpit/                          # ◄── THIS IS THE MOTHERSHIP
    │   ├── __init__.py
    │   ├── server.py                     # FastAPI webhook receiver (187 lines)
    │   ├── runner.py                     # Daemon launcher (calls create_app())
    │   ├── ui_engine.py                  # CockpitUIEngine — keyboard builder,
    │   │                                 #   callback parser, chat-state, 3-level nav
    │   ├── menu.py                       # ◄── The actual UI: 13 view_*() funcs
    │   ├── dispatcher.py                 # Shell-free subprocess executor
    │   ├── acl.py                        # Telegram ACL, GitHub HMAC, monitor keys,
    │   │                                 #   workspace path safety, COMMAND_REGISTRY
    │   ├── perimeter.py                  # Bind config, env validation, prod gate
    │   ├── github_processor.py           # GitHub push/workflow_run parsing
    │   ├── monitor_ingestion.py          # Sentry/BetterStack/Logtail/Datadog parsers
    │   └── workspace_scanner.py          # Project marker detection
    ├── layers/
    │   └── fiscal_sentry.py              # 120s hard-SIGKILL subprocess budget
    └── security/
        └── fences.py                     # is_command_forbidden() belt-and-suspenders
```

### Secrets & config

| Path | Owner | Purpose |
|---|---|---|
| `~/.hermes/.env` (chmod 600) | operator | All Telegram/GitHub/Monitor secrets + LLM keys. Loaded by the operator's shell, inherited by uvicorn. |
| `~/.hermes/config/cockpit.json` | operator | JSON config for the cockpit server. Currently **empty** (`{}`); all values fall back to env vars → defaults. Schema lives in `perimeter.get_bind_config()` and `validate_cockpit_env()`. |
| `/private/tmp/cockpit.log` | uvicorn | Uvicorn stdout/stderr — every POST hits this. |

### Data the Mothership reads (Prospector)

```
~/Documents/code/prospector/store/scheduler/
├── heartbeat.json           # Last scheduler tick {ts, phase, batch_size, pid, cycles}
├── DIAGNOSTICS_LATEST.txt   # Funnel + gate counts
├── ALERT.txt                # Active alerts (newline-delimited)
└── launchd.err.log          # Paginated by view_log(page=)
```

### Logs

| Path | What |
|---|---|
| `/private/tmp/cockpit.log` | Every HTTP request to the Mothership server |
| `~/.hermes/logs/gateway.log` | Hermes gateway stderr/stdout (different process) |
| `~/.hermes/logs/coordinator.log` | Coordinator tick log (different system) |

---

## 5. Process inventory

At the time of this writing (and historically the "normal" state):

| PID | Command | Port | Role |
|---|---|---|---|
| `29090` | `uvicorn sentinel.cockpit.server:create_app --factory --host 127.0.0.1 --port 8801` | `127.0.0.1:8801` | **Primary Mothership webhook server**. Has `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_ALLOWED_USER_IDS=8868748055`, `COCKPIT_EXECUTION_ENABLED=1` in its env. |
| `26782` | `python -u -m sentinel.cockpit.runner` | `127.0.0.1:8811` | **Secondary Mothership runner** (calls the same `create_app()`). Has a bogus `TELEGRAM_BOT_TOKEN=x`, `TELEGRAM_ALLOWED_USER_IDS=1`. Useful only as a second bind / dev instance. |
| `22345` | `ngrok http 127.0.0.1:8801 --log=stdout` | `127.0.0.1:4040` (admin API) | Public tunnel: `https://83a7-81-102-90-209.ngrok-free.app → :8801`. Free-tier URL rotates on restart. |
| `30568` | `hermes_cli.main gateway run --replace` | (polling) | **Hermes Telegram gateway**. Long-polls the same Otto bot token — see §13. |

There are **no LaunchAgent plists** for the Mothership (`ai.sentinel.cockpit.*`, `ai.mothership.*`). Both uvicorn processes were started manually (or by some unrecorded script). When the machine reboots, both Mothership processes are gone — only the Hermes LaunchAgents (`ai.hermes.coordinator`, `ai.hermes.gateway`, `ai.hermes.progress`, `ai.hermes.rsi`, `ai.hermes.watchdog`) come back.

The Hermes web dashboard (`hermes_cli.web_server.py`, port 9119) is also **not running** and has no LaunchAgent.

---

## 6. The webhook delivery chain

The full round-trip for a single operator message:

```
1. Operator sends /start (or any text) to @Ottototbot in Telegram
2. Telegram's Bot API receives the message
3. Telegram looks up the bot's webhook config:
   GET https://api.telegram.org/bot<TOKEN>/getWebhookInfo
   → "url": "https://83a7-81-102-90-209.ngrok-free.app/webhooks/telegram"
4. Telegram POSTs the update to that URL with header:
   X-Telegram-Bot-Api-Secret-Token: e806bebe56db9c39de6f5b3141e0fea2
   Body: {"message":{"from":{"id":8868748055}, "chat":{"id":8868748055}, "text":"/start", ...}}
5. ngrok receives on its public URL, terminates TLS, forwards to 127.0.0.1:8801
6. uvicorn dispatches to sentinel.cockpit.server.telegram_webhook()
7. server.py verifies:
   a. X-Telegram-Bot-Api-Secret-Token == os.environ["TELEGRAM_WEBHOOK_SECRET"]
      (hmac.compare_digest, 403 if mismatch)
   b. validate_telegram_user(from_id) against TELEGRAM_ALLOWED_USER_IDS
      (403 if not in allowlist)
8. Extract chat_id. If `message` (not callback_query):
   → menu.view_dashboard() → text + inline_keyboard
   → menu.send(chat_id, text, kb)
     → POST https://api.telegram.org/bot<TOKEN>/sendMessage
       body={"chat_id":..., "text":..., "parse_mode":"HTML",
             "reply_markup":{"inline_keyboard":[[...]]}}
9. Telegram delivers the rendered message to the operator's chat
   with the inline keyboard buttons attached.
10. server.py returns 200 {"status":"received","chat_id":...,"type":"message"}
    to Telegram's webhook POST.
11. When the operator taps a button, Telegram sends a `callback_query` update
    back through steps 3–10 with `callback_query.data = "nv:prospector:"` etc.
12. server.py routes via `handle_callback()` in menu.py (line 414+):
      nv:*      → navigation (dashboard / project)
      ac:rescan → rescan workspace
      dx:*      → view_daemon()
      dh:*, dg:* → view_heartbeat()
      da:*      → view_alerts()
      ds:*      → view_schedule()
      dk:*      → view_killed()
      di:*      → view_investigate()
      dz:*      → view_search()
      dl:N:0    → view_log(page=N)
      dr:N      → trigger_gen(count=N)
      gp:NAME:BRANCH, gs:NAME:, gl:NAME: → dispatcher.dispatch()
```

### Latency budget

Steps 2–6: ~50–200 ms (Telegram → ngrok → uvicorn)
Steps 7–9: ~100–300 ms (FastAPI handler → Telegram API)
Steps 10–11: Telegram fan-out to client, ~200–500 ms
**Total wall-clock: ~400 ms – 1 s** for a tap to render the next view.

### What's fragile in this chain

- **Step 3**: if `getWebhookInfo.url` is empty or stale, Telegram drops the update on the floor (`pending_update_count` stays at 0; messages never reach `:8801`).
- **Step 5**: ngrok free-tier URLs change on restart. The webhook registered in step 3 then becomes a 404.
- **Step 7b**: ACL is fail-closed. If `TELEGRAM_ALLOWED_USER_IDS` doesn't include your actual Telegram numeric ID, every webhook returns 403 silently — no Telegram reply ever comes.
- **Step 8**: `sendMessage` requires a valid `TELEGRAM_BOT_TOKEN`. If the env is missing/rotated, the menu view renders nothing.

---

## 7. Configuration reference

### Environment variables read by `sentinel.cockpit.*`

| Variable | Required? | Default | Purpose |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | **YES (prod)** | — | Bot token from @BotFather. Used by `menu.send()` and `acl.verify_*`. |
| `TELEGRAM_ALLOWED_USER_IDS` | **YES (prod)** | — | Comma-separated integers. If unset, **all users denied** (acl.py:71). |
| `TELEGRAM_WEBHOOK_SECRET` | **YES (prod)** | — | Sent by Telegram as `X-Telegram-Bot-Api-Secret-Token` when set via `setWebhook` `secret_token=`. Compared with `hmac.compare_digest` (server.py:50–55). |
| `GITHUB_WEBHOOK_SECRET` | **YES (prod)** | — | HMAC key for `X-Hub-Signature-256` (server.py:147). |
| `MONITOR_API_KEYS` | **YES (prod)** | — | JSON dict `{"sentry":"...","datadog":"..."}`. Compared against `Authorization` header (server.py:175). |
| `COCKPIT_HOST` | no | `127.0.0.1` | Bind host. `0.0.0.0` is forbidden by `perimeter.get_bind_config()` line 67. |
| `COCKPIT_PORT` | no | `8800` | Bind port. Server is currently bound to `8801` (env override or `cockpit.json` override). |
| `COCKPIT_WORKSPACE_ROOT` | no | `~/Documents/code` | Used by `dispatcher.get_workspace_root()` and as the validation root for `validate_workspace_path`. |
| `COCKPIT_EXECUTION_ENABLED` | **YES for actions** | unset | Master switch. If not exactly `"1"`, every `dispatch()` returns `blocked_reason="execution-disabled"` (dispatcher.py:151). |
| `COCKPIT_ENV` | no | `dev` | If `prod`/`production`, `runner.preflight()` calls `require_production_env()` which raises if any required secret is missing. |

### Where these vars are set

**`~/.hermes/.env`** holds the LLM keys and the Hermes Telegram config:
```
TELEGRAM_BOT_TOKEN=8656132729:AAEP_kDprUeqz7pCDFVZq1SiPsLPOFssN1c
TELEGRAM_ALLOWED_USERS=<not the same as TELEGRAM_ALLOWED_USER_IDS!>
TELEGRAM_HOME_CHANNEL=<chat_id>
```

**Note on the naming trap**: the .env key is `TELEGRAM_ALLOWED_USERS` (Hermes's variable, used by `hermes_cli`), but the Mothership reads `TELEGRAM_ALLOWED_USER_IDS` (`_IDS` suffix). They are different variables. The Mothership's running process (`pid 29090`) has `TELEGRAM_ALLOWED_USER_IDS=8868748055` baked into its environment at launch time — adding `TELEGRAM_ALLOWED_USERS` to `.env` does **not** affect the Mothership.

**`~/.hermes/config/cockpit.json`** is currently `{}`. Schema (from `perimeter.py`):
```json
{
  "workspace_root": "~/Documents/code",
  "server": {"host": "127.0.0.1", "port": 8800}
}
```
Env vars override config; config overrides defaults.

---

## 8. The menu catalog

The full set of inline-keyboard views in `sentinel/cockpit/menu.py`. Every view returns `(text, kb)` where `kb = {"inline_keyboard": [[{"text":..., "callback_data":...}, ...], ...]}`.

### 8.1 Top dashboard — `view_dashboard()` (`menu.py:123`)

Renders on **any** inbound message (server.py:84–86). Layout:

```
<b>◆  M O T H E R S H I P</b>

  <b>System</b>    <uptime> up  ·  load <loadavg>  ·  disk <free> of <total>
            <N> processes

  ────────────────────────────
  ⚡ Prospector    <phase>  ·  batch <n>  ·  pid <pid>
            cycle <n>  ·  <age>

  ⚠ <top alert line 1>
  ⚠ <top alert line 2>

  ────────────────────────────
  <b>Projects</b>    <N> repos  ·  tap to inspect

[proj1] [proj2] [proj3]
[proj4] [proj5] [proj6]
...
[📊 Refresh]   [🔍 Rescan]
```

Data sources:
- `_sys()` → `uptime`, `df -h /`, `ps aux | wc -l`
- `_rjson(CFG["hb"])` → `~/Documents/code/prospector/store/scheduler/heartbeat.json`
- `_rtxt(CFG["alerts"])` → `~/Documents/code/prospector/store/scheduler/ALERT.txt`
- `_projects()` → `iterdir()` of `~/Documents/code`, filtered to dirs with `.git/`, name doesn't start with `.`, "worktree" not in name. Sorted.

### 8.2 Per-project view — `view_project(name)` (`menu.py:174`)

Renders branch + `git status --short` (first 6 files), special handling for `prospector`.

Buttons per project:
- `gs:{name}:` → dispatcher → `git status --short`
- `gp:{name}:main` → dispatcher → `git pull origin main`
- `gl:{name}:` → dispatcher → `git log --oneline -10`

Extra buttons if `name == "prospector"`:
- `dx:0` → `view_daemon()`
- `dk:0` → `view_killed()`
- `dz:0` → `view_search()`
- `dl:0:0` → `view_log(page=0)`
- `dr:3` → `trigger_gen(count=3)` — fire-and-forget subprocess
- `dh:0` → `view_heartbeat()`

### 8.3 Scheduler daemon — `view_daemon()` (`menu.py:222`)

Status, heartbeat, batch funnel (gen → novel → vetted → PASS/KILL + survival %), all 7 gate scores, verification health (% unverifiable, web calls), alerts.

### 8.4 Killed dossiers — `view_killed()` (`menu.py:282`)

Last 5 `*.kill.json` from `~/Documents/code/prospector/store/dossiers/`, sorted by mtime desc. Shows title, gate that fired, `dense_reward`, reason.

### 8.5 Generator investigation — `view_investigate()` (`menu.py:308`)

Distills `DIAGNOSTICS_LATEST.txt`. Auto-detects broken search: emits `🔴 SEARCH BROKEN — zero web calls with high unverifiability` when both conditions hit.

### 8.6 Search health — `view_search()` (`menu.py:330`)

Checks `EXA_API_KEY`, runs a live `ExaSearchProvider().search("test", k=2)` against `~/Documents/code/prospector/prospector/retrieval.py`. Reports `BRAVE_API_KEY` presence.

### 8.7 Heartbeat — `view_heartbeat()` (`menu.py:350`)

`heartbeat.json` → time, phase, pid, cycles, batch_size.

### 8.8 Schedule — `view_schedule()` (`menu.py:362`)

`CFG["int"]` (currently `7200`s = 2h) + last run age.

### 8.9 Alerts — `view_alerts()` (`menu.py:372`)

Top 3 lines of `ALERT.txt`.

### 8.10 Log viewer — `view_log(page=0)` (`menu.py:378`)

50 lines/page of `~/Documents/code/prospector/store/scheduler/launchd.err.log`. Newer/Older/Refresh buttons.

### 8.11 Callback routing table

Defined in `handle_callback()` (`menu.py:414`+):

| Callback prefix | Handler |
|---|---|
| `nv:*` | `view_dashboard()` or `view_project(target)` |
| `ac:rescan` | `view_dashboard()` |
| `dx:*` | `view_daemon()` |
| `dh:*` / `dg:*` | `view_heartbeat()` |
| `da:*` | `view_alerts()` |
| `ds:*` | `view_schedule()` |
| `dk:*` | `view_killed()` |
| `di:*` | `view_investigate()` |
| `dz:*` | `view_search()` |
| `dl:N:0` | `view_log(page=N)` |
| `dr:N` | `trigger_gen(count=N)` |
| `gp:*:branch` | `dispatcher.dispatch()` → `git_pull` |
| `gs:*:*` | `dispatcher.dispatch()` → `git_status` |
| `gl:*:*` | `dispatcher.dispatch()` → `git_log` |

`action:rescan:` (note: `action:`, not `ac:`) is also routed in `server.py:108` for callback_query messages.

### 8.12 `ui_engine.py` — level-2 keyboard engine

Independent of the per-project dispatcher path. Defines `CockpitUIEngine` with:
- `LEVEL_0_BUTTONS`: Projects, CI/CD, Monitoring, Config
- `LEVEL_1_BUTTONS`: sub-sections (deployments, branches, alerts, settings, logs)
- Level 2 actions per project: git_pull, git_status, git_log, git_fetch, npm_dev, npm_build, docker_up, docker_down

**Important**: as of this writing, `CockpitUIEngine` is **defined but not called from the dispatcher path**. The currently-active per-project menu comes from `view_project()` in `menu.py` directly. `ui_engine.py` is wired into `dispatcher.py:36` only for callback parsing (`parse_callback`), not for keyboard rendering.

---

## 9. The action execution model

### 9.1 `COMMAND_REGISTRY` (acl.py:18)

The human-readable allowlist — every command the Mothership is allowed to run:

```python
COMMAND_REGISTRY = {
    "git_pull":       "git -C {workspace} pull origin {branch}",
    "git_status":     "git -C {workspace} status --short",
    "git_log":        "git -C {workspace} log --oneline -10",
    "git_fetch":      "git -C {workspace} fetch --all",
    "npm_dev":        "cd {workspace} && npm run dev",
    "npm_build":      "cd {workspace} && npm run build",
    "npm_install":    "cd {workspace} && npm install",
    "npm_test":       "cd {workspace} && npm test",
    "pip_install":    "cd {workspace} && pip install -r requirements.txt",
    "docker_up":      "cd {workspace} && docker compose up -d",
    "docker_down":    "cd {workspace} && docker compose down",
    "docker_build":   "cd {workspace} && docker compose build",
    "docker_logs":    "cd {workspace} && docker compose logs --tail-50 {service}",
    "systemctl_restart": "systemctl restart {service}",
    "systemctl_status":  "systemctl status {service}",
    "make_build":     "cd {workspace} && make",
    "make_test":      "cd {workspace} && make test",
    # Short aliases
    "gs": "git -C {workspace} status --short",
    "gp": "git -C {workspace} pull origin {branch}",
    "gl": "git -C {workspace} log --oneline -10",
}
```

### 9.2 `ACTION_SPECS` (dispatcher.py:78)

The same set, expressed as **argv tuples** (no shell). The test `tests/test_dispatcher.py::test_action_specs_match_registry` enforces the two stay in lock-step.

```python
ACTION_SPECS = {
    "git_pull":   ArgvSpec(argv=("git","-C","{workspace}","pull","origin","{branch}"),
                           token_field="id", token_name="branch"),
    "git_status": ArgvSpec(argv=("git","-C","{workspace}","status","--short")),
    "git_log":    ArgvSpec(argv=("git","-C","{workspace}","log","--oneline","-10")),
    "git_fetch":  ArgvSpec(argv=("git","-C","{workspace}","fetch","--all")),
    "npm_dev":    ArgvSpec(argv=("npm","run","dev"),        cwd="{workspace}"),
    "npm_build":  ArgvSpec(argv=("npm","run","build"),      cwd="{workspace}"),
    "npm_install":ArgvSpec(argv=("npm","install"),          cwd="{workspace}"),
    "npm_test":   ArgvSpec(argv=("npm","test"),             cwd="{workspace}"),
    "pip_install":ArgvSpec(argv=("pip","install","-r","requirements.txt"), cwd="{workspace}"),
    "docker_up":  ArgvSpec(argv=("docker","compose","up","-d"),      cwd="{workspace}"),
    "docker_down":ArgvSpec(argv=("docker","compose","down"),         cwd="{workspace}"),
    "docker_build":ArgvSpec(argv=("docker","compose","build"),       cwd="{workspace}"),
    "docker_logs":ArgvSpec(argv=("docker","compose","logs","--tail=50","{service}"),
                           cwd="{workspace}", token_field="id", token_name="service"),
    "systemctl_restart": ArgvSpec(argv=("systemctl","restart","{service}"),
                                  workspace_from=None, token_field="target", token_name="service"),
    "systemctl_status":  ArgvSpec(argv=("systemctl","status","{service}"),
                                  workspace_from=None, token_field="target", token_name="service"),
    "make_build": ArgvSpec(argv=("make",),                  cwd="{workspace}"),
    "make_test":  ArgvSpec(argv=("make","test"),             cwd="{workspace}"),
}
```

### 9.3 `dispatch()` flow (dispatcher.py:160)

Returns a `DispatchResult` — **never raises** for untrusted/malformed input.

```
parse callback "action:target:id"        → ui_engine.parse_callback
   malformed? → ok=False, blocked_reason="malformed-callback"
look up ACTION_SPECS[action]
   unknown?  → ok=False, blocked_reason="unknown-action"
validate target as workspace:
   resolve(root / target).relative_to(root) — must be inside root
   must exist (resolve symlinks first)
   fail? → ok=False, blocked_reason="invalid-workspace"
validate id as branch/service token:
   ALLOWED_TOKEN = ^[A-Za-z0-9._/-]+$
   fail? → ok=False, blocked_reason="invalid-branch" or "invalid-service"
format argv tokens with validated substitutions
   missing placeholder? → ok=False, blocked_reason="missing-substitution"
SecurityFence.is_command_forbidden(rendered)
   fail? → ok=False, blocked_reason="forbidden-command"
FiscalSentry.execute_with_budget(argv, ..., cwd=cwd, time_budget_seconds=120)
   on timeout: SIGKILL the process group, was_killed=True
return DispatchResult(ok, action, argv, exit_code, was_killed, stdout, stderr)
```

### 9.4 `trigger_gen()` (menu.py:398)

The one non-shell-free fire-and-forget action. Spawns Prospector's scheduler directly:

```python
subprocess.Popen(
    ["~/Documents/code/prospector/.venv/bin/python",
     "-m","prospector.scheduler.run_scheduled","--once",
     f"--candidates={count}","--config=config.yaml"],
    cwd="~/Documents/code/prospector",
    stdout=DEVNULL, stderr=DEVNULL,
)
```

No `FiscalSentry`, no `SecurityFence`, no workspace validation. This is a known gap — the subprocess is hardcoded to the prospector project directory and runs prospector's own CLI, which is internally rate-limited.

### 9.5 `view_daemon()`'s `_ps()` helper (menu.py:70)

Runs `launchctl list | grep <label>` and `ps -p <pid>` to get live PID/CPU/MEM/uptime. Not a security boundary — purely informational.

---

## 10. Security model (7 layers)

Every `dispatch()` call passes through all 7 gates. **Every gate fails closed.**

| # | Layer | Where | What it does |
|---|---|---|---|
| 0 | Execution master switch | `dispatcher.execution_enabled()` | `COCKPIT_EXECUTION_ENABLED` must be exactly `"1"`. |
| 1 | Callback parser | `ui_engine.parse_callback()` | Splits `action:target:id`; rejects all-empty, trailing-non-empty after empty (`":b:c"`), missing action. |
| 2 | Action allowlist | `ACTION_SPECS` lookup | Unknown action → `unknown-action`. |
| 3 | Workspace path validation | `acl.validate_workspace_path()` | `Path(root).resolve() / workspace → relative_to(root)` must succeed; must exist; symlinks resolved. Defeats `../` traversal and symlink escape. |
| 4 | Branch/service token regex | `ALLOWED_TOKEN = ^[A-Za-z0-9._/-]+$` | No spaces, no shell metachars, no unicode. |
| 5 | Shell-free argv substitution | `tok.format(**subst)` | The placeholder template is filled **token by token**, never shlex-joined. `shell=False` is implicit in `subprocess.run(argv, ...)`. |
| 6 | Forbidden-command screen | `SecurityFence.is_command_forbidden()` | Belt-and-suspenders re-screen of the rendered argv. Catches anything that slipped through layers 1–5 (e.g. a project name that aliases a binary on PATH). |
| 7 | Time/cost budget | `FiscalSentry.execute_with_budget()` | Hard 120s ceiling. On timeout: `os.killpg(SIGKILL)`, sets `was_killed=True`. |

### What's intentionally not enforced

- **No command dry-run / approval step.** Tapping Pull on a project runs `git pull` immediately. Mitigated by: (a) `git pull` is the lowest-risk mutation in the registry, (b) the operator can `view_log` to see what ran.
- **No audit log.** Dispatch results return to the chat inline (server.py:115–127) but aren't persisted to disk. The Prospector scheduler's `launchd.err.log` is the only persistent record.
- **`trigger_gen` bypasses the dispatcher's security envelope.** It uses `subprocess.Popen` directly with hardcoded argv. Acceptable because there's no user-controlled substring in the rendered command, but it's the one action that doesn't go through `FiscalSentry`.

---

## 11. Operational procedures

### 11.1 Health checks (read-only)

```bash
# Server up?
curl -s http://127.0.0.1:8801/health
# → {"status":"ok","daemon":"ai.mothership.gateway","mothership":"active"}

# Webhook destination correct?
TOKEN=$(awk -F= '/^TELEGRAM_BOT_TOKEN=/ {sub(/^[^=]*=/,""); gsub(/["'"'"']/,""); print; exit}' ~/.hermes/.env)
curl -s "https://api.telegram.org/bot${TOKEN}/getWebhookInfo" | python3 -m json.tool | head -8
# Expect: "url": "https://<ngrok-id>.ngrok-free.app/webhooks/telegram"

# Tunnel alive?
curl -s http://127.0.0.1:4040/api/tunnels | python3 -m json.tool | head -20

# Recent traffic?
tail -20 /private/tmp/cockpit.log
```

### 11.2 Re-register the webhook (when URL is empty or stale)

```bash
TOKEN='8656132729:AAEP_kDprUeqz7pCDFVZq1SiPsLPOFssN1c'
SECRET='e806bebe56db9c39de6f5b3141e0fea2'
NGROK_URL=$(curl -s http://127.0.0.1:4040/api/tunnels | python3 -c "import sys,json;print(json.load(sys.stdin)['tunnels'][0]['public_url'])")

curl -X POST "https://api.telegram.org/bot${TOKEN}/setWebhook" \
  -d "url=${NGROK_URL}/webhooks/telegram" \
  -d "secret_token=${SECRET}"
```

### 11.3 Restart the Mothership server

```bash
# Kill the existing uvicorn
pkill -f "sentinel.cockpit.server" || true
pkill -f "sentinel.cockpit.runner" || true

# Reload secrets
set -a; source ~/.hermes/.env; set +a

# Start the primary server
cd ~/Documents/code/sentinel-loop
export COCKPIT_PORT=8801 COCKPIT_HOST=127.0.0.1
export TELEGRAM_ALLOWED_USER_IDS=8868748055
export TELEGRAM_WEBHOOK_SECRET=e806bebe56db9c39de6f5b3141e0fea2
export COCKPIT_EXECUTION_ENABLED=1
nohup /usr/local/Cellar/python@3.14/3.14.6/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python \
    -m uvicorn sentinel.cockpit.server:create_app --factory \
    --host 127.0.0.1 --port 8801 --log-level info \
    >> /private/tmp/cockpit.log 2>&1 &
```

### 11.4 Find your Telegram numeric ID

```bash
# Have the bot see a message from you; then read it back
TOKEN='...'
curl -s "https://api.telegram.org/bot${TOKEN}/getUpdates" | python3 -m json.tool | grep '"id"'
# Or use @userinfobot / @RawDataBot in Telegram.
```

### 11.5 End-to-end smoke test (without sending a real Telegram message)

```bash
NGROK_URL=$(curl -s http://127.0.0.1:4040/api/tunnels | python3 -c "import sys,json;print(json.load(sys.stdin)['tunnels'][0]['public_url'])")

curl -s -X POST "${NGROK_URL}/webhooks/telegram" \
  -H "Content-Type: application/json" \
  -H "X-Telegram-Bot-Api-Secret-Token: e806bebe56db9c39de6f5b3141e0fea2" \
  -d '{"update_id":1,"message":{"message_id":1,"from":{"id":8868748055,"is_bot":false,"first_name":"Test"},"chat":{"id":8868748055,"type":"private"},"date":1719200000,"text":"/start"}}'
# → {"status":"received","chat_id":"8868748055","type":"message"}
```

### 11.6 Add a LaunchAgent so the Mothership survives reboot

```bash
cat > ~/Library/LaunchAgents/ai.sentinel.cockpit.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>ai.sentinel.cockpit</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/Cellar/python@3.14/3.14.6/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python</string>
    <string>-m</string>
    <string>sentinel.cockpit.runner</string>
  </array>
  <key>WorkingDirectory</key><string>/Users/chidionyema/Documents/code/sentinel-loop</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>COCKPIT_PORT</key><string>8801</string>
    <key>COCKPIT_HOST</key><string>127.0.0.1</string>
    <key>TELEGRAM_ALLOWED_USER_IDS</key><string>8868748055</string>
    <key>TELEGRAM_WEBHOOK_SECRET</key><string>e806bebe56db9c39de6f5b3141e0fea2</string>
    <key>COCKPIT_EXECUTION_ENABLED</key><string>1</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>/private/tmp/cockpit.log</string>
  <key>StandardErrorPath</key><string>/private/tmp/cockpit.error.log</string>
</dict>
</plist>
EOF

# Load secrets into the agent's env — symlink or use a secret manager; do NOT
# paste tokens into the plist. Simplest: a 0600 file at ~/.cockpit.env that the
# agent sources via a wrapper script.
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.sentinel.cockpit.plist
```

---

## 12. Failure mode catalog

Every symptom an operator is likely to hit, with the exact check and the exact fix.

| Symptom | First check | Likely cause | Fix |
|---|---|---|---|
| "I sent a message to Otto, nothing came back" | `getWebhookInfo.url` | URL empty (webhook never set, or was cleared) | §11.2 — re-register |
| "I sent a message, Otto replies with nothing" | `/private/tmp/cockpit.log` for the most recent POST | POST didn't arrive (tunnel down) | `curl -s http://127.0.0.1:4040/api/tunnels`; restart ngrok; re-register webhook |
| "POST arrives, server returns 403" | `tail /private/tmp/cockpit.log` for `User not authorized` | ACL denied your Telegram ID | `TELEGRAM_ALLOWED_USER_IDS` env on the Mothership process doesn't include your ID; restart with the correct value (§11.3) |
| "POST arrives, server returns 422" | `tail /private/tmp/cockpit.log` for `Invalid JSON body` | Telegram sent malformed payload (extremely rare) | Retry — usually transient |
| "Project grid is missing projects I just cloned" | `ls ~/Documents/code/<proj>/.git` | Project scanner filters: no `.git`, starts with `.`, contains "worktree" | Name it appropriately; `cd` into it; `git init` if not yet a repo |
| "git pull button does nothing" | `tail /private/tmp/cockpit.log` for the dispatcher reply | `COCKPIT_EXECUTION_ENABLED!=1` OR branch token fails the regex OR workspace validation failed | Restart with the right env; check that the branch is a valid git ref |
| "Prospector view shows ○ STOPPED" | `launchctl list \| grep com.prospector.scheduler` | The launchd scheduler isn't running | Restart Prospector (separate concern, not Mothership) |
| "Log viewer shows 'No log file'" | `ls ~/Documents/code/prospector/store/scheduler/launchd.err.log` | Prospector scheduler never wrote anything (never ran) | Same as above |
| "Server restarted, webhook URL is wrong again" | `getWebhookInfo.url` | ngrok free-tier URL rotated | §13.1 — reserved ngrok domain or self-heal script |
| "Menu renders, but my taps aren't acknowledged" | `tail /private/tmp/cockpit.log` for the callback_query POST | Telegram isn't pushing callbacks — usually same root cause as the empty URL | Same as the "I sent a message, nothing came back" row |
| "Forbidden command" inline reply | The action's allowed use | `SecurityFence` blocked the rendered argv (rare) | Inspect `SecurityFence.is_command_forbidden()` in `sentinel/security/fences.py`; the project name or branch may have triggered a deny rule |
| "Telegram says 'rejected webhook'" | `getWebhookInfo.last_error_message` | HTTPS cert, secret mismatch, or self-signed cert without `setWebhook` `certificate=` | For ngrok the cert is fine; ensure `secret_token` matches `TELEGRAM_WEBHOOK_SECRET` |

---

## 13. Known fragility points

Three structural weaknesses in the current setup that any future operator should fix:

### 13.1 ngrok URL rotation

`ngrok http 127.0.0.1:8801 --log=stdout` (free tier) is started by hand. Free-tier URLs rotate on every restart and on idle timeout. Each rotation invalidates `getWebhookInfo.url`. Today: `https://83a7-81-102-90-209.ngrok-free.app`.

**Fix A — Reserved domain ($8/mo):**
```bash
ngrok http 127.0.0.1:8801 --domain=your-reserved.ngrok-free.app
# Then setWebhook once with the permanent URL. Never breaks again unless you cancel ngrok.
```

**Fix B — Self-heal script** (drop in `~/.hermes/scripts/cockpit_webhook_sync.py`):
```python
#!/usr/bin/env python3
"""Re-register Telegram webhook whenever the ngrok URL changes."""
import json, os, time, urllib.request
from pathlib import Path

STATE = Path.home() / ".hermes" / "state" / "cockpit_webhook.json"
SECRET = "e806bebe56db9c39de6f5b3141e0fea2"  # or read from ~/.hermes/.env
TOKEN = "8656132729:AAEP_kDprUeqz7pCDFVZq1SiPsLPOFssN1c"

def current_url():
    with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=5) as r:
        return json.load(r)["tunnels"][0]["public_url"] + "/webhooks/telegram"

def last_url():
    return json.loads(STATE.read_text())["url"] if STATE.exists() else None

def register(url):
    body = urllib.parse.urlencode({"url": url, "secret_token": SECRET}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/setWebhook", data=body)
    urllib.request.urlopen(req).read()

def main():
    STATE.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            url = current_url()
            if url != last_url():
                register(url)
                STATE.write_text(json.dumps({"url": url, "ts": time.time()}))
        except Exception:
            pass
        time.sleep(30)

if __name__ == "__main__":
    main()
```

Wire it into a LaunchAgent (`ai.cockpit.webhook-sync`) with `StartInterval=30`.

### 13.2 Bot collision with the Hermes gateway

The LaunchAgent `ai.hermes.gateway` runs `hermes_cli.main gateway run --replace`, which long-polls Telegram's Bot API for updates using the same `TELEGRAM_BOT_TOKEN` as the Mothership. Telegram does not allow concurrent webhook + polling on the same bot. Today, `pending_update_count: 0` suggests the gateway is idle (or its `replace` strategy has yielded), but the configuration is racy.

**Fix**: Give the Mothership its own bot (recommended). Create `@OttoSentinelBot` via @BotFather, set its token as `TELEGRAM_BOT_TOKEN` on the Mothership process only. The Hermes gateway keeps Otto.

### 13.3 No persistence across reboots

No `ai.sentinel.cockpit.plist`. No `ai.cockpit.ngrok.plist`. After every reboot the operator must re-run:

```bash
ngrok http 127.0.0.1:8801 &
# then §11.3 for the server
# then §11.2 to re-register the webhook
```

**Fix**: §11.6 adds the LaunchAgent for the server; add a parallel one for ngrok (`ai.cockpit.ngrok.plist` running `ngrok http 127.0.0.1:8801 --domain=<reserved>`). Combine with §13.1 fix B for full self-healing.

### 13.4 Secrets in plaintext on the process command line / plist

`TELEGRAM_BOT_TOKEN` and `TELEGRAM_WEBHOOK_SECRET` are visible in `ps aux` output for the duration of the process. LaunchAgent plists in `~/Library/LaunchAgents/` are readable by anyone with shell access.

**Fix**: Use `~/.cockpit.env` (chmod 600) and source it via a wrapper script. The Mothership's `perimeter.py` already enforces `chmod 600` is recommended in `require_production_env()`.

### 13.5 No audit trail

Dispatch results return inline in chat but are not written to a file. The only persistent trail is Prospector's own `launchd.err.log` (which only captures the scheduler, not arbitrary `git pull` / `npm run build` runs).

**Fix**: Add a `dispatch_audit` table or `~/.hermes/logs/cockpit-audit.jsonl` written from `dispatcher.dispatch()`. Each line: `ts, action, workspace, exit_code, was_killed, argv, stdout_hash`.

---

## 14. Glossary

| Term | Meaning |
|---|---|
| **Mothership** | The `sentinel.cockpit.*` Python package and its uvicorn server. Rendered into Telegram as the inline-keyboard menu. |
| **Cockpit** | Synonym for Mothership in this codebase. |
| **Otto** | The Telegram bot (`@Ottototbot`, id `8656132729`). The Mothership's voice. |
| **Hermes agent** | A separate codebase at `~/.hermes/hermes-agent/`. Has its own web dashboard (`:9119`), its own LaunchAgents, and a long-polling Telegram gateway that may share Otto's token. |
| **Prospector** | The scheduler project at `~/Documents/code/prospector/`. The Mothership is largely a control surface for it. |
| **Webhook** | The HTTP endpoint Telegram POSTs updates to. Configured via `setWebhook`. |
| **`pending_update_count`** | Telegram's count of updates queued for delivery but not yet acknowledged. `0` means no backlog. |
| **`getWebhookInfo`** | Telegram API method to inspect the current webhook destination. |
| **Inline keyboard** | A Telegram UI element: a matrix of buttons attached to a message, where each tap sends a `callback_query` back to the bot. |
| **`callback_data`** | The string Telegram sends back when a button is tapped. Format here: `action:target:id` (or shorter prefixes like `nv:`, `gp:`, `dl:`). |
| **ACL** | Access Control List. Here: `TELEGRAM_ALLOWED_USER_IDS`, a comma-separated set of Telegram numeric user IDs. Fail-closed — empty set means nobody is allowed. |
| **`secret_token`** | The `setWebhook` parameter. Telegram sends it back as `X-Telegram-Bot-Api-Secret-Token` on every delivery; the server compares with `hmac.compare_digest`. |
| **HMAC** | Hash-based Message Authentication Code. GitHub's `X-Hub-Signature-256` is verified against `GITHUB_WEBHOOK_SECRET`. |
| **FiscalSentry** | `sentinel.layers.fiscal_sentry`. Wraps `subprocess.run` with a hard time budget; SIGKILLs the process group on expiry. |
| **SecurityFence** | `sentinel.security.fences`. A static deny-list re-screen of rendered argv, layered behind the substitution step. |
| **ArgvSpec** | The dataclass in `dispatcher.py` that defines a shell-free command template — argv tuple + optional cwd + which callback field supplies the workspace and which supplies the branch/service token. |
| **Sentinel-loop** | The directory `~/Documents/code/sentinel-loop/` — the Mothership's source repo. |
| **`view_*`** | The 13 `view_*()` functions in `menu.py`. Each returns `(text, inline_keyboard)`. |
| **`dispatch()`** | The function in `dispatcher.py` that validates a callback and executes the mapped command. Returns a `DispatchResult` — never raises. |
| **`DispatchResult`** | The dataclass: `ok, action, blocked_reason, argv, exit_code, was_killed, stdout, stderr`. |
| **Dispatch rejection codes** | `execution-disabled`, `malformed-callback`, `unknown-action`, `invalid-workspace`, `invalid-branch`, `invalid-service`, `missing-substitution`, `forbidden-command`. |
| **`cockpit.json`** | The optional JSON config at `~/.hermes/config/cockpit.json`. Currently empty. |

---

*End of handbook. Owner: operator. Last incident reference: 2026-06-24 — webhook URL was empty for an extended period; restored via `setWebhook` + ngrok URL extraction. See `/private/tmp/cockpit.log` for the moment-of-restoration POST at `127.0.0.1:57061`.*
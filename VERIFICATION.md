# Hermes + Sentinel — System Verification Doc

**Last verified:** 2026-06-24 15:00  
**Verification command:** `python3 ~/Documents/code/sentinel-loop/verify_hermes.py`  
**Exit 0 = all good. Exit 1 = something broken. Never trust, always verify.**

---

## Architecture

```
Telegram Bot API
      │ webhook (POST)
      ▼
ngrok (https://83a7-...ngrok-free.app)
      │ tunnel → localhost:8801
      ▼
Cockpit Server (Python 3.14, FastAPI, port 8801)
  /Users/chidionyema/Documents/code/sentinel-loop/
      │
      ├── /dashboard, /mothership, /menu
      │   → Cockpit inline-keyboard UI (menu.py)
      │   → Reads Prospector scheduler state
      │
      ├── /daemon, /logs, /alerts, /heartbeat, etc.
      │   → DevOps controls (menu.py)
      │
      └── Free-form text ("Hey Otto")
          → POST localhost:8802/chat
              │
              ▼
          Otto Server (Python 3.11, hermes venv, port 8802)
            otto_server.py
              │
              └── Full Hermes AIAgent
                  ├── Soul: LUX Proof-Driven Development
                  ├── Model: deepseek-v4-pro
                  ├── Memory: session state
                  ├── Tools: 18 toolsets (search, code, verify)
                  └── Verification: tool-calling with proof

Coordinator Daemon (tick every 5s, port 8811)
  ~/.hermes/scripts/coordinator.py daemon
  └── Background tasks, project health, estate management
```

## Key files

| File | Role |
|---|---|
| `sentinel-loop/sentinel/cockpit/server.py` | Webhook receiver, routes commands → menu, text → Otto |
| `sentinel-loop/scripts/otto_server.py` | Persistent hermes agent HTTP server |
| `sentinel-loop/scripts/hermes_chat_bridge.py` | Fallback: direct agent call (one-shot) |
| `sentinel-loop/verify_hermes.py` | **VERIFICATION SCRIPT** — run this to prove everything works |
| `~/.hermes/config.yaml` | Hermes config (model, toolsets, platforms) |
| `~/.hermes/.env` | Secrets (API keys, bot tokens) |
| `~/.hermes/scripts/coordinator.py` | Background task daemon |
| `~/.hermes/hermes-agent/` | Hermes agent code (Python 3.11 venv) |

## Services

| Service | Port | Process | How to start |
|---|---|---|---|
| Cockpit | 8801 | `uvicorn sentinel.cockpit.server` | See start commands below |
| Otto Server | 8802 | `otto_server.py` (hermes venv) | See start commands below |
| ngrok | 4040 | `ngrok http 8801` | Must be running for webhook |
| Coordinator | 8811 | `coordinator.py daemon` | launchd: `ai.hermes.coordinator` |

## Start commands

```bash
# 1. Load secrets
source ~/.hermes/.env
export TELEGRAM_ALLOWED_USER_IDS="${TELEGRAM_ALLOWED_USERS:-8868748055}"
export TELEGRAM_WEBHOOK_SECRET=e806bebe56db9c39de6f5b3141e0fea2

# 2. Start Otto server (hermes venv, takes ~25s to init)
~/.hermes/hermes-agent/venv/bin/python \
  ~/Documents/code/sentinel-loop/scripts/otto_server.py 8802 \
  >> ~/.hermes/logs/otto-server.log 2>&1 &

# 3. Start cockpit
cd ~/Documents/code/sentinel-loop
python3 -m uvicorn sentinel.cockpit.server:create_app \
  --factory --host 127.0.0.1 --port 8801 --log-level warning \
  >> ~/.hermes/logs/cockpit.log 2>&1 &

# 4. Wait for both to be healthy
curl http://127.0.0.1:8801/health  # → {"status":"ok"}
curl http://127.0.0.1:8802/health  # → {"status":"ok"}

# 5. Set Telegram webhook (do this LAST, after cockpit is up)
curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -F "url=https://83a7-81-102-90-209.ngrok-free.app/webhooks/telegram" \
  -F "secret_token=${TELEGRAM_WEBHOOK_SECRET}" \
  -F "allowed_updates[]=message" \
  -F "allowed_updates[]=callback_query" \
  -F "max_connections=5"

# 6. Verify everything
python3 ~/Documents/code/sentinel-loop/verify_hermes.py
```

## CRITICAL: Why the webhook breaks

**Root cause:** The webhook URL gets cleared by Telegram when the cockpit
is down and Telegram's webhook deliveries fail repeatedly.

**This means:**
- NEVER restart the cockpit while someone might be sending messages
- If cockpit goes down, webhook gets cleared → messages lost → must re-set webhook
- The ngrok URL is ephemeral (free tier). If ngrok restarts, URL changes, webhook breaks.

**Fix if webhook is empty:**
```bash
source ~/.hermes/.env
curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -F "url=https://83a7-81-102-90-209.ngrok-free.app/webhooks/telegram" \
  -F "secret_token=e806bebe56db9c39de6f5b3141e0fea2" \
  -F "allowed_updates[]=message" \
  -F "allowed_updates[]=callback_query"
```

## Verification protocol

**Before making ANY change** to hermes or sentinel code:
1. Run `python3 ~/Documents/code/sentinel-loop/verify_hermes.py`
2. Confirm 15/15 PASS
3. Make your change
4. Run verification again
5. Confirm still 15/15 PASS
6. Send a real Telegram message to confirm

**If verification fails:**
- Don't guess. The script tells you exactly what's broken.
- Fix the specific failing check before changing anything else.
- Run verification again after each fix.

## Common failures and fixes

| Symptom | Check | Fix |
|---|---|---|
| "Otto not responding" | `verify_hermes.py` §4 — webhook URL | Webhook cleared → re-set it |
| "Dashboard broken" | `verify_hermes.py` §1 — cockpit port | Cockpit down → restart it |
| "Otto has no memory" | `verify_hermes.py` §3 — otto response | Otto server down → restart it |
| "Messages lost" | `verify_hermes.py` §4 — pending count | Pending > 0 → cockpit was down |
| "ngrok 502" | `verify_hermes.py` §2 — cockpit health | Cockpit not running or just restarted |

## ngrok URL

Current: `https://83a7-81-102-90-209.ngrok-free.app`

If this changes (ngrok restart), update:
1. The webhook URL in the `setWebhook` command above
2. This document
3. Re-run `verify_hermes.py`

## Dependencies

```
sentinel-loop/ (Python 3.14)
  ├── fastapi, uvicorn (HTTP server)
  └── urllib (stdlib)

~/.hermes/hermes-agent/ (Python 3.11 venv)
  ├── hermes_cli (config, models, tools)
  ├── run_agent (AIAgent)
  ├── agent/ (agent_init, memory, compression, guardrails)
  └── gateway/ (platform adapters — NOT used for chat, only for sendMessage API)

~/.hermes/scripts/ (Python 3.14)
  └── coordinator.py (background daemon)
```

#!/usr/bin/env bash
# =============================================================================
# C4 — Telegram webhook registration with secret_token (origin proof)
# =============================================================================
#
# Usage:
#   scripts/setup_webhook.sh [--dry-run]
#
# Reads TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET from environment
# (or ~/.hermes/.env).  Discovers the tunnel URL by checking:
#   1. CLOUDFLARED_TUNNEL_URL env var (set by your tunnel runner)
#   2. Active cloudflared quick tunnel (cloudflared tunnel info)
#   3. Active ngrok tunnel (ngrok api)
#
# Registers the webhook with Telegram, then verifies it's active.
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Load secrets ────────────────────────────────────────────────────────────
if [ -f "$HOME/.hermes/.env" ]; then
    set -a
    # shellcheck source=/dev/null
    source "$HOME/.hermes/.env"
    set +a
fi

: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN not set}"
: "${TELEGRAM_WEBHOOK_SECRET:?TELEGRAM_WEBHOOK_SECRET not set}"

# ── Discover tunnel URL ─────────────────────────────────────────────────────
discover_url() {
    # 1. Explicit env var
    if [ -n "${CLOUDFLARED_TUNNEL_URL:-}" ]; then
        echo "$CLOUDFLARED_TUNNEL_URL"
        return
    fi

    # 2. cloudflared quick tunnel
    if command -v cloudflared &>/dev/null; then
        local cf_url
        cf_url=$(cloudflared tunnel info --json 2>/dev/null | python3 -c "
import json,sys
try:
    info = json.load(sys.stdin)
    for t in info.get('tunnels', []):
        if t.get('conns', []):
            print(t.get('hostname',''))
            sys.exit(0)
except: pass
" 2>/dev/null) || true
        if [ -n "${cf_url:-}" ]; then
            echo "https://${cf_url}"
            return
        fi
    fi

    # 3. ngrok
    if command -v ngrok &>/dev/null; then
        local ngrok_url
        ngrok_url=$(curl -s --max-time 3 http://127.0.0.1:4040/api/tunnels 2>/dev/null | python3 -c "
import json,sys
try:
    data = json.load(sys.stdin)
    for t in data.get('tunnels', []):
        print(t.get('public_url',''))
        sys.exit(0)
except: pass
" 2>/dev/null) || true
        if [ -n "${ngrok_url:-}" ]; then
            echo "$ngrok_url"
            return
        fi
    fi

    echo ""
}

TUNNEL_URL=$(discover_url)
if [ -z "$TUNNEL_URL" ]; then
    echo "ERROR: No tunnel URL found."
    echo "  Set CLOUDFLARED_TUNNEL_URL or start a tunnel (cloudflared/ngrok) first."
    echo "  Quick start: cloudflared tunnel --url http://127.0.0.1:8800"
    exit 1
fi

WEBHOOK_URL="${TUNNEL_URL}/webhooks/telegram"
echo "Tunnel URL:  $TUNNEL_URL"
echo "Webhook URL: $WEBHOOK_URL"
echo "Secret:      ${TELEGRAM_WEBHOOK_SECRET:0:4}****"

# ── Dry run ─────────────────────────────────────────────────────────────────
if [ "${1:-}" = "--dry-run" ]; then
    echo ""
    echo "[DRY RUN] Would call setWebhook with:"
    echo "  url=$WEBHOOK_URL"
    echo "  secret_token=$TELEGRAM_WEBHOOK_SECRET"
    exit 0
fi

# ── Register webhook ────────────────────────────────────────────────────────
echo ""
echo "Registering webhook..."

RESPONSE=$(curl -s --max-time 15 \
    -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
    -H "Content-Type: application/json" \
    -d "$(python3 -c "
import json, os
print(json.dumps({
    'url': '${WEBHOOK_URL}',
    'secret_token': '${TELEGRAM_WEBHOOK_SECRET}',
    'allowed_updates': ['message', 'callback_query'],
}))
")")

echo "Response: $RESPONSE"

# ── Verify ──────────────────────────────────────────────────────────────────
echo ""
echo "Verifying webhook info..."

INFO=$(curl -s --max-time 10 \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo")

echo "$INFO" | python3 -c "
import json,sys
info = json.load(sys.stdin).get('result', {})
url = info.get('url', '')
has_custom = info.get('has_custom_certificate', False)
pending = info.get('pending_update_count', 0)
errors = info.get('last_error_message', '')

print(f'  URL:              {url}')
print(f'  Has custom cert:  {has_custom}')
print(f'  Pending updates:  {pending}')
if errors:
    print(f'  Last error:       {errors}')
if url:
    print()
    print('✅ Webhook registered.')
else:
    print()
    print('❌ Webhook NOT set — check the response above.')
"

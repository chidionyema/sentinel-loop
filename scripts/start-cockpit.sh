#!/bin/bash
# Wrapper: sources .env then starts the cockpit server
set -e
export HOME=/Users/chidionyema

# Load secrets from .env
if [ -f "$HOME/.hermes/.env" ]; then
  set -a
  source "$HOME/.hermes/.env"
  set +a
fi

# Ensure required vars
export COCKPIT_HOST="${COCKPIT_HOST:-127.0.0.1}"
export COCKPIT_PORT="${COCKPIT_PORT:-8801}"
export TELEGRAM_ALLOWED_USER_IDS="${TELEGRAM_ALLOWED_USERS:-8868748055}"

cd "$HOME/Documents/code/sentinel-loop"
exec /usr/local/bin/python3 -m uvicorn sentinel.cockpit.server:create_app \
  --factory --host "$COCKPIT_HOST" --port "$COCKPIT_PORT" --log-level warning

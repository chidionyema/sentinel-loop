#!/bin/bash
set -e
export HOME=/Users/chidionyema
if [ -f "$HOME/.hermes/.env" ]; then
  set -a
  source "$HOME/.hermes/.env"
  set +a
fi
exec /Users/chidionyema/.hermes/hermes-agent/venv/bin/python \
  /Users/chidionyema/Documents/code/sentinel-loop/scripts/otto_server.py 8802

# Quarantine — DO NOT RUN

## `reliable_otto.py.DANGER`

A standalone Telegram **long-polling** client. On startup it calls
`telegram_api("deleteWebhook", ...)` (reliable_otto.py:244) to switch the bot
into polling mode.

**Running it while the cockpit webhook is live deletes the webhook and deafens
the live Telegram door** — the one estate-wide safety rule
(see `~/.hermes/specs/estate-cockpit-deepseek-spec.md:19` and
`ESTATE_NORTH_STAR.md`): exactly one process owns the bot token, and the cockpit
webhook is canonical.

It is renamed with a `.DANGER` suffix so it cannot be launched accidentally
(`python scripts/reliable_otto.py` no longer resolves). Free-text chat is served
by the **safe** HTTP relay `scripts/otto_server.py` (127.0.0.1:8802), which the
cockpit reaches via `server.py:_call_otto()`. Use that, not this.

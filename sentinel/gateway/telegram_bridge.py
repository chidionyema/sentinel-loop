"""Hermes Gateway — Telegram bridge & real-time notification push engine.

Supports injectable transports (e.g., httpx.AsyncClient) for production use.
Default transport stores messages in-memory for testing.

H4: MarkdownV2 escape applied at the SEND layer (HTTPTransport.send) so
operator-facing messages never break formatting or allow injection through
untrusted fields. Tests assert plain substrings in builder functions — the
escape lives HERE, not in message builders.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol


# ---------------------------------------------------------------------------
#  H4 — MarkdownV2 escape (Telegram Bot API)
# ---------------------------------------------------------------------------

_MD_V2_ESCAPE_RE = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")


def escape_markdown_v2(text: str) -> str:
    """Escape Telegram MarkdownV2 special characters.

    Applied at the send layer so that dynamic fields (commit messages,
    alert summaries, project names) cannot break formatting or inject
    markup. Safe to call on already-escaped strings — the backslash
    itself gets escaped, so double-escaping is visible but harmless.
    """
    return _MD_V2_ESCAPE_RE.sub(r"\\\1", text)


class Transport(Protocol):
    """Injectable transport for gateway notifications."""

    def send(self, payload: dict[str, Any]) -> bool:
        """Send a notification payload. Returns True on success."""
        ...


@dataclass
class GatewayMessage:
    message: str
    priority: str
    timestamp: str
    delivered: bool


class MemoryTransport:
    """In-memory transport for testing."""

    def __init__(self):
        self.sent: list[dict[str, Any]] = []

    def send(self, payload: dict[str, Any]) -> bool:
        self.sent.append(payload)
        return True


class HTTPTransport:
    """HTTP transport for production Telegram bot API."""

    def __init__(self, bot_token: str, chat_id: str, session: Any = None):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._session = session
        self.sent: list[dict[str, Any]] = []

    def send(self, payload: dict[str, Any]) -> bool:
        """Send via Telegram Bot API. Uses httpx if available, otherwise stores.

        H4: message text is MarkdownV2-escaped at this layer so dynamic
        fields from builders (commit messages, etc.) are safe.
        """
        try:
            import urllib.request
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            body = json.dumps({
                "chat_id": self.chat_id,
                "text": escape_markdown_v2(payload.get("message", "")),
                "parse_mode": "MarkdownV2",
            }).encode()
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10)
            self.sent.append(payload)
            return True
        except Exception:
            self.sent.append(payload)
            return False


class HermesGateway:
    """Inbound/Outbound Telegram bridge + real-time notification push engine.

    Spec §1: ai.hermes.gateway — continuously monitored by launchd.
    Accepts an injectable Transport; defaults to MemoryTransport for testing.
    """

    DAEMON_NAME = "ai.hermes.gateway"

    def __init__(self, transport: Transport | None = None):
        self._transport = transport or MemoryTransport()
        self._sent_messages: list[GatewayMessage] = []

    def send_urgent_push(self, message: str) -> GatewayMessage:
        """Send a high-priority urgent push notification."""
        msg = GatewayMessage(
            message=message,
            priority="urgent",
            timestamp=datetime.now(timezone.utc).isoformat(),
            delivered=True,
        )
        self._sent_messages.append(msg)
        self._transport.send({"message": message, "priority": "urgent"})
        return msg

    def send_notification(self, message: str, priority: str = "normal") -> GatewayMessage:
        """Send a notification with specified priority."""
        msg = GatewayMessage(
            message=message,
            priority=priority,
            timestamp=datetime.now(timezone.utc).isoformat(),
            delivered=True,
        )
        self._sent_messages.append(msg)
        self._transport.send({"message": message, "priority": priority})
        return msg

    def bridge_status(self) -> dict:
        """Return the current bridge status."""
        return {
            "daemon": self.DAEMON_NAME,
            "messages_sent": len(self._sent_messages),
            "status": "active",
        }

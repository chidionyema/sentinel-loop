"""Tests for H4 — MarkdownV2 escape at the gateway send layer."""

from __future__ import annotations

import pytest


def test_escape_markdown_v2_basic():
    """Special characters are backslash-escaped."""
    from sentinel.gateway.telegram_bridge import escape_markdown_v2

    assert escape_markdown_v2("hello") == "hello"
    assert escape_markdown_v2("a_b") == r"a\_b"
    assert escape_markdown_v2("a*b") == r"a\*b"
    assert escape_markdown_v2("[link]") == r"\[link\]"
    assert escape_markdown_v2("(paren)") == r"\(paren\)"
    assert escape_markdown_v2("~strike~") == r"\~strike\~"
    assert escape_markdown_v2("`code`") == r"\`code\`"


def test_escape_markdown_v2_injection_vectors():
    """Characters that could be used to inject formatting are escaped."""
    from sentinel.gateway.telegram_bridge import escape_markdown_v2

    # Hash/heading
    assert escape_markdown_v2("# heading") == r"\# heading"
    # Pipe (table)
    assert escape_markdown_v2("a|b") == r"a\|b"
    # Exclamation (image/link)
    assert escape_markdown_v2("!alert") == r"\!alert"
    # Dot (ordered list / domain)
    assert escape_markdown_v2("evil.com") == r"evil\.com"
    # All specials together
    dirty = "_*[]()~`>#+-=|{}.!"
    escaped = escape_markdown_v2(dirty)
    for ch in dirty:
        assert escaped.count(ch) == 1  # Each special appears exactly once, preceded by \


def test_escape_markdown_v2_idempotent_safe():
    """Already-escaped strings don't break — double-escaping is visible but
    the output is still safe (no unescaped specials at odd positions)."""
    from sentinel.gateway.telegram_bridge import escape_markdown_v2

    once = escape_markdown_v2("hello_world")
    twice = escape_markdown_v2(once)
    # Both safe — no bare underscore
    assert "_" not in twice.replace("\\_", "")


def test_httptransport_send_escapes_message(monkeypatch):
    """HTTPTransport.send applies MarkdownV2 escape to the message text."""
    from sentinel.gateway.telegram_bridge import HTTPTransport, escape_markdown_v2

    sent_payloads = []

    def fake_urlopen(req, timeout):
        import json as _json
        sent_payloads.append(_json.loads(req.data))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    transport = HTTPTransport(bot_token="test", chat_id="123")
    # Message contains unescaped special chars
    transport.send({"message": "commit: fix `null` in module_name"})

    assert len(sent_payloads) == 1
    body = sent_payloads[0]
    # The text should be escaped
    assert body["text"] == escape_markdown_v2("commit: fix `null` in module_name")
    assert body["parse_mode"] == "MarkdownV2"
    # Bare backticks should NOT be in the sent text
    assert "`" not in body["text"].replace("\\`", "")

"""Tests for H6 — sanitize callback_data tokens."""

from __future__ import annotations

import pytest


def test_sanitize_callback_token_preserves_safe():
    """Alphanumeric, underscore, and hyphen pass through unchanged."""
    from sentinel.cockpit.ui_engine import sanitize_callback_token

    assert sanitize_callback_token("my-project_alpha") == "my-project_alpha"
    assert sanitize_callback_token("ABC123") == "ABC123"
    assert sanitize_callback_token("a") == "a"


def test_sanitize_callback_token_replaces_unsafe():
    """Characters outside [A-Za-z0-9_-] are replaced with underscore."""
    from sentinel.cockpit.ui_engine import sanitize_callback_token

    assert sanitize_callback_token("my project") == "my_project"
    assert sanitize_callback_token("evil.com") == "evil_com"
    assert sanitize_callback_token("path/to/repo") == "path_to_repo"
    assert sanitize_callback_token("hello:world") == "hello_world"


def test_sanitize_callback_token_blocks_injection():
    """Injection characters (colon, semicolon, backtick, newline) are replaced."""
    from sentinel.cockpit.ui_engine import sanitize_callback_token

    # Colon injection (routing corruption)
    assert ":" not in sanitize_callback_token("a:b")
    # Semicolon injection
    assert ";" not in sanitize_callback_token("a;rm")
    # Backtick injection
    assert "`" not in sanitize_callback_token("a`b")
    # Newline injection
    assert "\n" not in sanitize_callback_token("a\nb")
    # Dollar-brace
    assert "$" not in sanitize_callback_token("${HOME}")


def test_project_buttons_sanitize_callback_data():
    """Project names with unsafe chars get sanitized in callback_data."""
    from sentinel.cockpit.ui_engine import CockpitUIEngine

    ui = CockpitUIEngine(projects=["my-project", "evil:injection", "path/to/repo"])
    buttons = ui.LEVEL_1_BUTTONS["projects"]

    # Collect all callback_data values
    all_cb = []
    for row in buttons:
        for btn in row:
            all_cb.append(btn["callback_data"])

    # No callback_data should contain a colon beyond the first two (action:target:)
    for cb in all_cb:
        parts = cb.split(":")
        # Format: action:target:id — at most 3 parts (2 colons)
        assert len(parts) <= 3, f"callback_data {cb!r} has too many colons"
        # The target portion (parts[1]) should be safe
        if len(parts) >= 2:
            target = parts[1]
            assert ":" not in target
            assert ";" not in target


def test_monitor_buttons_sanitize_service(monkeypatch):
    """Emergency buttons sanitize attacker-supplied service names."""
    from sentinel.cockpit.ui_engine import sanitize_callback_token

    # Simulate a malicious service name
    dirty = "svc;rm -rf /"
    safe = sanitize_callback_token(dirty)
    assert ";" not in safe
    assert " " not in safe
    assert safe == "svc_rm_-rf__"


def test_github_deploy_button_sanitize_repo():
    """Deploy button sanitizes repo_name in callback_data."""
    from sentinel.cockpit.ui_engine import sanitize_callback_token

    dirty = "owner/repo-name"
    safe = sanitize_callback_token(dirty)
    assert "/" not in safe
    assert safe == "owner_repo-name"

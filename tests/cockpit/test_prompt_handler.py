"""A4 — update_prompt: handler (acknowledge y/n; full wiring is TODO)."""

import pytest


def test_update_prompt_y_acks(monkeypatch):
    """update_prompt:y must not raise and must ack."""
    from sentinel.cockpit.menu import handle_prompt_callback

    sent_messages = []
    monkeypatch.setattr("sentinel.cockpit.menu.send",
                        lambda chat_id, text, kb=None: sent_messages.append(text))
    monkeypatch.setattr("sentinel.cockpit.menu.answer", lambda cbq_id: True)
    monkeypatch.setattr("sentinel.cockpit.menu._api", lambda m, b: True)

    import asyncio
    # Should not raise
    asyncio.run(handle_prompt_callback("update_prompt:y", "8868748055", "cbq-1"))
    assert len(sent_messages) > 0, "Handler should send a response"


def test_update_prompt_n_acks(monkeypatch):
    """update_prompt:n must not raise and must ack."""
    from sentinel.cockpit.menu import handle_prompt_callback

    sent_messages = []
    monkeypatch.setattr("sentinel.cockpit.menu.send",
                        lambda chat_id, text, kb=None: sent_messages.append(text))
    monkeypatch.setattr("sentinel.cockpit.menu.answer", lambda cbq_id: True)
    monkeypatch.setattr("sentinel.cockpit.menu._api", lambda m, b: True)

    import asyncio
    asyncio.run(handle_prompt_callback("update_prompt:n", "8868748055", "cbq-2"))
    assert len(sent_messages) > 0, "Handler should send a response"

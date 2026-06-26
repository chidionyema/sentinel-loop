"""WI-4 — Live CI/CD screen: list runs, re-run low-risk, gate money/identity."""

import pytest


def test_cicd_screen_has_home_button(monkeypatch):
    """cicd:list must render with a Home button."""
    from sentinel.cockpit.menu import handle_callback

    sent_messages = []
    sent_keyboards = []

    def fake_send(chat_id, text, kb=None):
        sent_messages.append(text)
        if kb:
            sent_keyboards.append(kb)

    monkeypatch.setattr("sentinel.cockpit.menu.send", fake_send)
    monkeypatch.setattr("sentinel.cockpit.menu.answer", lambda cbq_id: True)
    monkeypatch.setattr("sentinel.cockpit.menu._api", lambda m, b: True)

    # Mock gh run list to return empty
    import subprocess
    def fake_run(cmd, **kwargs):
        class R:
            stdout = "[]"
            stderr = ""
            returncode = 0
        return R()
    monkeypatch.setattr(subprocess, "run", fake_run)

    import asyncio
    asyncio.run(handle_callback("cicd:list", "8868748055", "cbq-cicd"))

    combined = " ".join(sent_messages)
    assert "ci" in combined.lower() or "pipeline" in combined.lower() or "workflow" in combined.lower(), \
        f"No CI/CD content: {combined}"

    # Must have a Home button
    all_cb = []
    for kb in sent_keyboards:
        for row in kb.get("inline_keyboard", []):
            for btn in row:
                all_cb.append(btn.get("callback_data", ""))
    assert any("nv:dash:" in cb or "nv:home:" in cb for cb in all_cb), \
        f"No Home button in keyboards: {sent_keyboards}"

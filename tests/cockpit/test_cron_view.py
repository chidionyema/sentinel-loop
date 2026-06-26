"""B2 — /cron view: list all cron jobs with enabled/disabled markers."""

import json
import os
import tempfile

import pytest


def test_cron_view_shows_enabled_disabled(monkeypatch):
    """cron:list must render all jobs with ✅/⏸ markers."""
    from sentinel.cockpit.menu import handle_estate_callback

    # Create a temp jobs.json with one enabled + one disabled job
    fixture = {
        "jobs": [
            {
                "id": "test-job-1",
                "name": "Always on job",
                "enabled": True,
                "schedule": {"kind": "interval", "minutes": 60, "display": "every 60m"},
            },
            {
                "id": "test-job-2",
                "name": "Goal of the moment",
                "enabled": False,
                "schedule": {"kind": "interval", "minutes": 60, "display": "every 60m"},
            },
        ]
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(fixture, f)
        jobs_path = f.name

    sent_messages = []
    monkeypatch.setattr("sentinel.cockpit.menu.send",
                        lambda chat_id, text, kb=None: sent_messages.append(text))
    monkeypatch.setattr("sentinel.cockpit.menu.answer", lambda cbq_id: True)
    monkeypatch.setattr("sentinel.cockpit.menu._api", lambda m, b: True)

    # Point the cron handler at our fixture
    import builtins
    orig_open = builtins.open

    def fake_open(path, *args, **kwargs):
        if "jobs.json" in str(path) and "cron" in str(path):
            return orig_open(jobs_path, *args, **kwargs)
        return orig_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)

    import asyncio
    asyncio.run(handle_estate_callback("estate:cron", "8868748055", "cbq-cron"))

    combined = " ".join(sent_messages)
    # Must show both jobs
    assert "Always on job" in combined, f"Enabled job not shown in: {combined}"
    assert "Goal of the moment" in combined, f"Disabled job not shown in: {combined}"
    # Must differentiate enabled vs disabled
    assert "⏸" in combined or "disabled" in combined.lower() or "paused" in combined.lower(), \
        f"No disabled/paused marker in: {combined}"

    os.unlink(jobs_path)

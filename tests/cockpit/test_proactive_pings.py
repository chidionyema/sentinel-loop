"""WI-7 — Proactive action pings: CI failures get One-Tap buttons."""

import pytest

from sentinel.cockpit.github_processor import process_workflow_event


def test_workflow_failure_has_action_buttons():
    """A failing workflow_run event must include Re-run/View buttons."""
    payload = {
        "workflow_run": {
            "status": "completed",
            "conclusion": "failure",
            "name": "CI",
        },
        "repository": {
            "full_name": "chidionyema/prospector",
            "name": "prospector",
        },
    }

    result = process_workflow_event(payload)
    assert result is not None, "Should return result for completed workflow"
    assert "🔴" in result["text"], f"Expected 🔴 for failure: {result['text']}"

    kb = result.get("reply_markup", {})
    inline = kb.get("inline_keyboard", [])
    # Flatten buttons
    all_cb = []
    all_text = []
    for row in inline:
        for btn in row:
            all_cb.append(btn.get("callback_data", ""))
            all_text.append(btn.get("text", ""))

    assert any("rerun" in cb.lower() for cb in all_cb), \
        f"No Re-run button in: {all_cb}"
    assert any("view" in txt.lower() or "ci" in txt.lower() for txt in all_text), \
        f"No View CI/CD button in: {all_text}"


def test_workflow_success_still_has_view_button():
    """A passing workflow should still have a View CI/CD button."""
    payload = {
        "workflow_run": {
            "status": "completed",
            "conclusion": "success",
            "name": "CI",
        },
        "repository": {
            "full_name": "chidionyema/prospector",
            "name": "prospector",
        },
    }

    result = process_workflow_event(payload)
    assert result is not None
    kb = result.get("reply_markup", {})
    inline = kb.get("inline_keyboard", [])
    all_text = []
    for row in inline:
        for btn in row:
            all_text.append(btn.get("text", ""))
    assert any("view" in txt.lower() or "ci" in txt.lower() for txt in all_text), \
        f"No View button for success: {all_text}"

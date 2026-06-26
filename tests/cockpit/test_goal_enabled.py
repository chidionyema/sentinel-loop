"""C1 — Re-enable goal-of-the-moment (job id 8b3beb82ae6e)."""

import json
import os


def test_goal_of_the_moment_is_enabled():
    """Goal-of-the-moment cron job must have enabled: true."""
    cron_path = os.path.expanduser("~/.hermes/cron/jobs.json")
    with open(cron_path, "r") as f:
        data = json.load(f)

    goal_job = None
    for job in data.get("jobs", []):
        if job.get("id") == "8b3beb82ae6e":
            goal_job = job
            break

    assert goal_job is not None, "goal-of-the-moment job (8b3beb82ae6e) not found in jobs.json"
    assert goal_job.get("enabled") is True, (
        f"goal-of-the-moment must be enabled=True, got {goal_job.get('enabled')}"
    )
    assert goal_job.get("name", "").startswith("goal-of-the-moment"), (
        f"Unexpected job name: {goal_job.get('name')}"
    )

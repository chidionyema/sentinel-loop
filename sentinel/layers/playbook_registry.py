"""Layer 1: Playbook Registry - Enforces strict task definitions & schema-validated criteria."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PlaybookValidationResult:
    is_valid: bool
    playbook_name: str = ""
    failure_signature: str = ""
    escalated: bool = False


class PlaybookRegistry:
    """Layer 1: Playbook Registry enforces schema-validated task definitions.

    Every item picked up from kanban.db must match a corresponding
    JSON definition file inside the playbooks directory.
    """

    def __init__(self, playbooks_path: str):
        self.playbooks_path = Path(playbooks_path)
        self._playbooks: dict[str, dict] = {}
        self._load_playbooks()

    def _load_playbooks(self) -> None:
        if not self.playbooks_path.exists():
            return
        for pb_file in self.playbooks_path.glob("*.playbook.json"):
            try:
                content = pb_file.read_text()
                definition = json.loads(content)
                task_type = definition.get("task_type", pb_file.stem.replace(".playbook", ""))
                self._playbooks[task_type] = definition
            except (json.JSONDecodeError, KeyError):
                # Invalid JSON or malformed playbook - skip
                pass

    def validate_task_playbook(self, task_type: str, task_data: dict) -> PlaybookValidationResult:
        """Validate that a task matches its playbook definition."""
        if task_type not in self._playbooks:
            return PlaybookValidationResult(
                is_valid=False,
                playbook_name=task_type,
                failure_signature="missing-playbook-rubric",
                escalated=True,
            )

        playbook = self._playbooks[task_type]
        schema = playbook.get("schema", {})
        required_fields = schema.get("required_fields", [])

        # Check required fields exist in task_data
        for field in required_fields:
            if field not in task_data:
                return PlaybookValidationResult(
                    is_valid=False,
                    playbook_name=task_type,
                    failure_signature=f"missing-required-field:{field}",
                    escalated=False,
                )

        return PlaybookValidationResult(
            is_valid=True,
            playbook_name=task_type,
        )

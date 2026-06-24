"""Subsystem 2: Telegram UI Engine.

Stateful menu navigation with hierarchical keyboard rendering,
anti-spam message editing, and callback query parsing.

Callback data format: action:target:id

H6: project/service names interpolated into callback_data are sanitized
to ``[A-Za-z0-9_\-]+`` to prevent routing corruption / parse DoS.
"""

from __future__ import annotations

import re


# ---------------------------------------------------------------------------
#  H6 — sanitize tokens interpolated into callback_data
# ---------------------------------------------------------------------------

_CALLBACK_TOKEN_RE = re.compile(r"[^A-Za-z0-9_\-]")


def sanitize_callback_token(value: str) -> str:
    """Replace any character that is not ``[A-Za-z0-9_\-]`` with ``_``.

    Applied to project names, service identifiers, and any attacker/
    config-controlled value before it's interpolated into Telegram
    ``callback_data`` (format ``action:target:id``). Prevents routing
    corruption, parse DoS, and injection via the colon-delimited protocol.
    """
    return _CALLBACK_TOKEN_RE.sub("_", value)

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
#  Callback parser
# ---------------------------------------------------------------------------


def parse_callback(data: str) -> dict[str, str]:
    """Parse a callback data string in action:target:id format.

    Examples:
        'git_pull:project-alpha:main' -> {action: 'git_pull', target: 'project-alpha', id: 'main'}
        'navigate:level_1:'            -> {action: 'navigate', target: 'level_1', id: ''}
        'back::'                       -> {action: 'back', target: '', id: ''}

    Raises ValueError on malformed data (empty, all-empty segments,
    or non-empty segment after an empty one).
    """
    if not data or not data.strip():
        raise ValueError("Callback data is empty")

    # Strip surrounding whitespace
    data = data.strip()

    # Split into at most 3 segments
    segments = data.split(":", 2)
    if all(s == "" for s in segments):
        raise ValueError("Callback data has no content (all segments empty)")

    action = segments[0] if len(segments) > 0 else ""
    target = segments[1] if len(segments) > 1 else ""
    id_val = segments[2] if len(segments) > 2 else ""

    if not action:
        raise ValueError("Callback data missing action segment")

    # Reject when a non-empty segment follows an empty one
    # Valid:  "a:b:c", "a:b:", "a::"  (trailing empties OK)
    # Invalid: "a::c" (empty b before non-empty c), ":b:c", "a::d"
    found_empty = False
    for i, seg in enumerate(segments):
        if not seg:
            found_empty = True
        elif found_empty:
            raise ValueError(
                f"Callback data has empty segment before non-empty segment "
                f"at position {i}: {data!r}"
            )

    return {
        "action": action,
        "target": target,
        "id": id_val,
    }


# ---------------------------------------------------------------------------
#  Keyboard builder
# ---------------------------------------------------------------------------


def build_inline_keyboard(buttons: list[list[dict]]) -> dict:
    """Build a Telegram InlineKeyboardMarkup from button definitions.

    Args:
        buttons: List of rows, each row is a list of button dicts with
                 'text' and 'callback_data' (and optional 'url').

    Returns:
        {"inline_keyboard": [[{...}, {...}], ...]}
    """
    keyboard_rows: list[list[dict[str, Any]]] = []

    for row in buttons:
        keyboard_row: list[dict[str, Any]] = []
        for btn in row:
            btn_def: dict[str, Any] = {
                "text": btn.get("text", ""),
            }
            if "url" in btn and btn["url"]:
                btn_def["url"] = btn["url"]
            else:
                btn_def["callback_data"] = btn.get("callback_data", "")
            keyboard_row.append(btn_def)
        keyboard_rows.append(keyboard_row)

    return {"inline_keyboard": keyboard_rows}


# ---------------------------------------------------------------------------
#  Chat state tracking
# ---------------------------------------------------------------------------


@dataclass
class ChatState:
    chat_id: str
    current_level: int = 0
    current_context: str = ""          # JSON: {target, id, params...}
    last_message_id: str | None = None


# ---------------------------------------------------------------------------
#  UI Engine
# ---------------------------------------------------------------------------


class CockpitUIEngine:
    """Stateful Telegram UI engine with hierarchical menu navigation.

    Maintains per-chat edit state for anti-spam message editing.
    Renders keyboard markup for 3 navigation levels.
    """

    # Default keyboard definitions
    LEVEL_0_BUTTONS: list[list[dict]] = [
        [
            {"text": "🖥 Projects", "callback_data": "navigate:projects:"},
            {"text": "🔄 CI/CD", "callback_data": "navigate:cicd:"},
        ],
        [
            {"text": "📊 Monitoring", "callback_data": "navigate:monitor:"},
            {"text": "⚙️ Config", "callback_data": "navigate:config:"},
        ],
    ]

    LEVEL_1_BUTTONS: dict[str, list[list[dict]]] = {
        "projects": [],   # Populated dynamically
        "cicd": [
            [{"text": "📦 Deployments", "callback_data": "navigate:deployments:"}],
            [{"text": "🌿 Branches", "callback_data": "navigate:branches:"}],
        ],
        "monitor": [
            [{"text": "🚨 Active Alerts", "callback_data": "navigate:alerts:"}],
            [{"text": "📈 Status Overview", "callback_data": "navigate:status:"}],
        ],
        "config": [
            [{"text": "🔧 Settings", "callback_data": "navigate:settings:"}],
            [{"text": "📋 Logs", "callback_data": "navigate:logs:"}],
        ],
    }

    def __init__(self, projects: list[str] | None = None):
        self._chat_states: dict[str, ChatState] = {}
        self._projects: list[str] = projects or []

        # Build project buttons for Level 1
        self._build_project_buttons()

    def _build_project_buttons(self) -> None:
        """Build Level 1 project buttons from the configured project list.

        Projects are injected via set_projects() — the engine does NOT
        auto-scan; that is the caller's responsibility (e.g., the gateway
        daemon reading from .hermes/config/cockpit.json workspace_root).
        """
        if not self._projects:
            self.LEVEL_1_BUTTONS["projects"] = [
                [{"text": "No projects discovered", "callback_data": "navigate:scan:"}],
                [{"text": "🔍 Scan Workspace", "callback_data": "action:rescan:"}],
            ]
        else:
            rows: list[list[dict]] = []
            for proj_name in sorted(self._projects):
                safe = sanitize_callback_token(proj_name)
                rows.append([
                    {"text": f"📁 {proj_name}", "callback_data": f"navigate:{safe}:"},
                ])
            self.LEVEL_1_BUTTONS["projects"] = rows

    # ------------------------------------------------------------------
    #  Project list management
    # ------------------------------------------------------------------

    def set_projects(self, projects: list[str]) -> None:
        """Update the project list and rebuild buttons."""
        self._projects = projects
        self._build_project_buttons()

    # ------------------------------------------------------------------
    #  Navigation
    # ------------------------------------------------------------------

    def navigate(self, current_level: int, target: str) -> tuple[int, list[list[dict]]]:
        """Navigate the menu tree and return (new_level, keyboard_buttons).

        Level 0: Global Dashboard — main nav buttons
        Level 1: Sub-system controllers — section or project selected
        Level 2: Resource view — specific item with back button

        Special targets:
            'back'  — navigate up one level
            'root'  — go to level 0
        """
        if target == "back":
            return self._go_back(current_level)

        if target == "root":
            return 0, self.LEVEL_0_BUTTONS

        if current_level == 0:
            return self._from_level_0(target)
        elif current_level == 1:
            return self._from_level_1(target)
        elif current_level == 2:
            return self._from_level_2(target)

        # Unknown level — reset to root
        return 0, self.LEVEL_0_BUTTONS

    def _from_level_0(self, target: str) -> tuple[int, list[list[dict]]]:
        """Navigate from Level 0 to Level 1."""
        if target in self.LEVEL_1_BUTTONS:
            buttons = self.LEVEL_1_BUTTONS[target]
            return 1, buttons

        # Unknown target — stay at level 0
        return 0, self.LEVEL_0_BUTTONS

    def _from_level_1(self, target: str) -> tuple[int, list[list[dict]]]:
        """Navigate from Level 1 to Level 2 (specific project/section detail).

        Any target from level 1 navigates to a level 2 detail view.
        """
        if target in self.LEVEL_1_BUTTONS and target != "projects":
            # Navigate to a known section's sub-level
            return 1, self.LEVEL_1_BUTTONS[target]

        # All other targets (including projects) go to level 2 detail
        buttons = self._build_level_2_buttons(target)
        return 2, buttons

    def _from_level_2(self, target: str) -> tuple[int, list[list[dict]]]:
        """Navigate within Level 2 (action buttons)."""
        # Stay at level 2 with refreshed view
        buttons = self._build_level_2_buttons(target)
        return 2, buttons

    def _go_back(self, current_level: int) -> tuple[int, list[list[dict]]]:
        """Navigate up one level."""
        if current_level <= 0:
            return 0, self.LEVEL_0_BUTTONS
        elif current_level == 1:
            return 0, self.LEVEL_0_BUTTONS
        elif current_level == 2:
            # Back from level 2 goes to level 1
            return 1, self._build_level_1_fallback()

        return 0, self.LEVEL_0_BUTTONS

    def _build_level_1_fallback(self) -> list[list[dict]]:
        """Build a fallback Level 1 view."""
        return self.LEVEL_1_BUTTONS.get("projects", [
            [{"text": "📁 Projects", "callback_data": "navigate:projects:"}],
        ])

    def _build_level_2_buttons(self, target: str) -> list[list[dict]]:
        """Build Level 2 buttons for a specific project or section."""
        buttons: list[list[dict]] = []

        # Always provide project-style actions for any target at level 2
        buttons = [
            [
                {"text": "⬇ Git Pull", "callback_data": f"git_pull:{target}:main"},
                {"text": "📋 Git Status", "callback_data": f"git_status:{target}:"},
            ],
            [
                {"text": "📜 Git Log", "callback_data": f"git_log:{target}:"},
                {"text": "🔄 Fetch", "callback_data": f"git_fetch:{target}:"},
            ],
            [
                {"text": "🚀 npm dev", "callback_data": f"npm_dev:{target}:"},
                {"text": "🔨 npm build", "callback_data": f"npm_build:{target}:"},
            ],
            [
                {"text": "🐳 Docker Up", "callback_data": f"docker_up:{target}:"},
                {"text": "⬇ Docker Down", "callback_data": f"docker_down:{target}:"},
            ],
        ]

        # Add back button
        buttons.append([
            {"text": "◀ Back", "callback_data": "navigate:back:"},
        ])

        return buttons

    # ------------------------------------------------------------------
    #  Chat state / anti-spam persistence
    # ------------------------------------------------------------------

    def record_message(self, chat_id: str, message_id: str) -> None:
        """Record a sent message ID for anti-spam editing."""
        if chat_id not in self._chat_states:
            self._chat_states[chat_id] = ChatState(chat_id=chat_id)
        self._chat_states[chat_id].last_message_id = message_id

    def get_edit_state(self, chat_id: str) -> dict:
        """Get the edit state for a chat.

        Returns:
            {"last_message_id": str|None, "current_level": int, ...}
        """
        state = self._chat_states.get(chat_id)
        if state is None:
            return {
                "last_message_id": None,
                "current_level": 0,
                "current_context": "",
            }
        return {
            "last_message_id": state.last_message_id,
            "current_level": state.current_level,
            "current_context": state.current_context,
        }

    def set_level(self, chat_id: str, level: int) -> None:
        """Update the current navigation level for a chat."""
        if chat_id not in self._chat_states:
            self._chat_states[chat_id] = ChatState(chat_id=chat_id)
        self._chat_states[chat_id].current_level = level

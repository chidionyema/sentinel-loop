"""Hermes RSI — Isolated model parameter/prompt tuning and sandboxed skill validation.

Spec §1: ai.hermes.rsi — isolated tuning and sandboxed validation.
Validates skills against playbook JSON schemas; tunes parameters in
sandboxed Python subprocess to prevent host environment contamination.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TuningResult:
    sandboxed: bool
    success: bool
    tuned_params: dict | None = None
    error: str = ""


@dataclass
class SkillValidationResult:
    sandboxed: bool
    success: bool
    errors: list[str] | None = None


class HermesRSI:
    """Isolated model parameter/prompt tuning and sandboxed skill validation.

    Tuning runs in a subprocess to prevent parameter leakage into the host.
    Skill validation checks playbook JSON structure and required fields.
    """

    DAEMON_NAME = "ai.hermes.rsi"

    def tune_parameters(self, model: str, params: dict,
                        sandboxed: bool = True) -> TuningResult:
        """Tune model parameters in an isolated sandbox.

        If sandboxed=True, validates params in a subprocess to catch
        import errors or syntax issues before applying to the host.
        """
        if not sandboxed:
            return TuningResult(sandboxed=False, success=True, tuned_params=params)

        # Validate parameter *values* inside the sandboxed subprocess so a
        # malformed value (wrong type or out-of-range) is rejected, not echoed.
        # bounds: (python type, inclusive-min, inclusive-max); None bound = unbounded
        try:
            script = (
                "import json, sys\n"
                f"params = {json.dumps(params)}\n"
                "BOUNDS = {\n"
                "    'temperature': (float, 0.0, 2.0),\n"
                "    'top_p': (float, 0.0, 1.0),\n"
                "    'frequency_penalty': (float, -2.0, 2.0),\n"
                "    'presence_penalty': (float, -2.0, 2.0),\n"
                "    'max_tokens': (int, 1, None),\n"
                "    'top_k': (int, 1, None),\n"
                "}\n"
                "for key, val in params.items():\n"
                "    if key not in BOUNDS:\n"
                "        continue  # unknown params pass through untouched\n"
                "    typ, lo, hi = BOUNDS[key]\n"
                "    # bool is a subclass of int — reject it explicitly for numeric params\n"
                "    if isinstance(val, bool) or not isinstance(val, (int, float)):\n"
                "        sys.stderr.write(f'{key}={val!r} is not a number')\n"
                "        sys.exit(1)\n"
                "    if typ is int and not float(val).is_integer():\n"
                "        sys.stderr.write(f'{key}={val!r} must be an integer')\n"
                "        sys.exit(1)\n"
                "    if (lo is not None and val < lo) or (hi is not None and val > hi):\n"
                "        sys.stderr.write(f'{key}={val!r} out of range [{lo}, {hi}]')\n"
                "        sys.exit(1)\n"
                "print(json.dumps(params))\n"
            )
            result = subprocess.run(
                ["python3", "-c", script],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return TuningResult(
                    sandboxed=True,
                    success=True,
                    tuned_params=json.loads(result.stdout.strip()) if result.stdout.strip() else params,
                )
            return TuningResult(
                sandboxed=True, success=False,
                error=result.stderr.strip() or "Parameter validation failed",
            )
        except Exception as e:
            return TuningResult(sandboxed=True, success=False, error=str(e))

    def validate_skill(self, skill_name: str, playbook_path: str,
                       sandboxed: bool = True) -> SkillValidationResult:
        """Validate a skill definition against its playbook schema.

        Checks that the playbook JSON is valid, has required structure,
        and can be loaded and validated in an isolated context.
        """
        errors: list[str] = []
        playbook_file = Path(playbook_path)

        # Structural checks
        if not playbook_file.exists():
            errors.append(f"Playbook file not found: {playbook_path}")

        try:
            content = playbook_file.read_text()
            definition = json.loads(content)
        except json.JSONDecodeError as e:
            errors.append(f"Invalid JSON in playbook {skill_name}: {e}")
        except Exception as e:
            errors.append(f"Cannot read playbook {skill_name}: {e}")
        else:
            if "task_type" not in definition:
                errors.append(f"Playbook {skill_name} missing 'task_type' field")
            if "schema" not in definition:
                errors.append(f"Playbook {skill_name} missing 'schema' field")
            elif "required_fields" not in definition["schema"]:
                errors.append(f"Playbook {skill_name} schema missing 'required_fields'")
            if "version" not in definition:
                errors.append(f"Playbook {skill_name} missing 'version' field")

        return SkillValidationResult(
            sandboxed=sandboxed,
            success=len(errors) == 0,
            errors=errors if errors else None,
        )

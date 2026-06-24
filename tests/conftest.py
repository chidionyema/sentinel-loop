"""
Pytest conftest — runs the integrity gate before any test collection begins.
If the gate fails, the test run is blocked with a clear message.
"""

import subprocess
import sys
from pathlib import Path


def pytest_configure(config):
    """Run the integrity gate before test collection."""
    gate_script = Path(__file__).parent.parent / "scripts" / "check-integrity.sh"
    if not gate_script.exists():
        return

    result = subprocess.run(
        ["bash", str(gate_script)],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent.parent),
    )

    sys.stdout.write(result.stdout)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        pytest.exit("Integrity gate failed — build blocked. See above for violations.", returncode=1)

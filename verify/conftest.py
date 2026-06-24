"""
Verify conftest — held-out verification audit trail.

These tests are NEVER visible to the implementation agent.
The conftest records every access to verify/ files so tampering is detectable.
"""

import hashlib
import json
import os
import sys
from pathlib import Path

VERIFY_DIR = Path(__file__).parent
AUDIT_FILE = VERIFY_DIR / ".audit.json"


def pytest_configure(config):
    """Log the test run to the audit trail."""
    audit = _load_audit()
    entry = {
        "timestamp": _now_iso(),
        "python": sys.version,
        "test_files": sorted(
            str(p.relative_to(VERIFY_DIR))
            for p in VERIFY_DIR.glob("test_*.py")
        ),
        "hashes": {
            str(p.relative_to(VERIFY_DIR)): _hash_file(p)
            for p in VERIFY_DIR.glob("test_*.py")
        },
    }
    audit.setdefault("runs", []).append(entry)
    _save_audit(audit)


def _load_audit() -> dict:
    if AUDIT_FILE.exists():
        return json.loads(AUDIT_FILE.read_text())
    return {"created": _now_iso(), "runs": []}


def _save_audit(audit: dict) -> None:
    AUDIT_FILE.write_text(json.dumps(audit, indent=2))


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

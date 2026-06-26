#!/usr/bin/env bash
# =============================================================================
# SENTINEL TIMEOUT VERIFICATION GATE
# =============================================================================
# Verifies that the coordinator inject operation handles timeout and lock
# contention correctly using the retry mechanism and completes under 5 seconds.
# =============================================================================

set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "Running timeout verification gate..."
if timeout 5 pytest tests/test_security_fixes.py -k test_inject_timeout_retry_under_deadline; then
    echo "✅ Timeout verification passed: operation completed successfully before deadline."
    exit 0
else
    echo "❌ Timeout verification failed or operation exceeded 5s deadline."
    exit 1
fi

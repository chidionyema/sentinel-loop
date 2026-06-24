#!/usr/bin/env bash
# =============================================================================
# SENTINEL INTEGRITY GATE — runs BEFORE pytest, blocks build on fraud patterns.
# =============================================================================
# Catches the exact anti-patterns that caused the previous rejection:
#   1. Caller-identity sniffing (sys._getframe / inspect.stack in prod code)
#   2. Exception subclasses used as return values (conflated result/error)
#   3. Thread.join(timeout) used as kill mechanism (fake enforcement)
#   4. Hardcoded signal constants without actual os.kill / os.killpg
#   5. Unwired layers (FiscalSentry not referenced by watchdog/coordinator)
# =============================================================================

set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FAILURES=0

red()  { printf '\033[31m%s\033[0m\n' "$*"; }
green(){ printf '\033[32m%s\033[0m\n' "$*"; }

check() {
    local label="$1" pattern="$2" dir="$3" explanation="$4"
    if grep -rn "$pattern" "$dir" --include="*.py" | grep -v '/tests/' | grep -v '/__pycache__/' > /tmp/sentinel-integrity-hit.txt 2>/dev/null; then
        red "🔴 INTEGRITY FAIL [$label]"
        red "   $explanation"
        while IFS= read -r line; do
            red "     $line"
        done < /tmp/sentinel-integrity-hit.txt
        FAILURES=$((FAILURES + 1))
    else
        green "✅ PASS: $label"
    fi
}

# ---------------------------------------------------------------------------
# Rule 1: No caller-identity sniffing in production code
# ---------------------------------------------------------------------------
check \
    "CALLER-SNIFF" \
    '_getframe\(\)|inspect\.stack\(\)|f_back\.f_code\.co_name|f\.f_code\.co_name' \
    "$PROJECT_ROOT/sentinel" \
    "caller-identity sniffing (_getframe, inspect.stack, f_code.co_name) — always a test hack"

# ---------------------------------------------------------------------------
# Rule 2: Result types must NOT subclass Exception
# ---------------------------------------------------------------------------
check \
    "RESULT-AS-EXCEPTION" \
    'class ExecutionResult\(Exception\)|class \w+Result\(Exception\)|class \w+Response\(Exception\)' \
    "$PROJECT_ROOT/sentinel" \
    "result type subclasses Exception — conflates return value with error. Use plain dataclass."

# ---------------------------------------------------------------------------
# Rule 3: Thread.join(timeout) is not a kill mechanism
# ---------------------------------------------------------------------------
check \
    "THREAD-TIMEOUT-THEATER" \
    'Thread.*daemon.*True' \
    "$PROJECT_ROOT/sentinel" \
    "Thread daemon usage near timeout — Thread.join(timeout) cannot kill a blocking call"

# Also catch the simpler pattern:
check \
    "THREAD-TIMEOUT-2" \
    'thread\.join.timeout' \
    "$PROJECT_ROOT/sentinel" \
    "thread.join(timeout) is not process enforcement — use subprocess.Popen + os.killpg"

# ---------------------------------------------------------------------------
# Rule 4: signal.SIGKILL must be accompanied by actual os.kill or os.killpg
# ---------------------------------------------------------------------------
SIGKILL_FILES=$(grep -rl 'signal\.SIGKILL' "$PROJECT_ROOT/sentinel" --include="*.py" 2>/dev/null || true)
for f in $SIGKILL_FILES; do
    if ! grep -q 'os\.kill\|os\.killpg' "$f"; then
        red "🔴 INTEGRITY FAIL [SIGKILL-FABRICATED]"
        red "   signal.SIGKILL referenced without actual os.kill/os.killpg in: $f"
        FAILURES=$((FAILURES + 1))
    else
        green "✅ PASS: SIGKILL-REAL in $f"
    fi
done

# ---------------------------------------------------------------------------
# Rule 5: Layer 3 (FiscalSentry) must be wired into watchdog AND coordinator
# ---------------------------------------------------------------------------
if ! grep -q 'FiscalSentry\|fiscal_sentry' "$PROJECT_ROOT/sentinel/watchdog.py"; then
    red "🔴 INTEGRITY FAIL [SENTRY-UNWIRED-WATCHDOG]"
    red "   FiscalSentry is not imported or used in watchdog.py — Layer 3 unwired"
    FAILURES=$((FAILURES + 1))
else
    green "✅ PASS: SENTRY-WIRED-WATCHDOG"
fi

if ! grep -q 'FiscalSentry\|fiscal_sentry\|is_budget_exceeded\|sentry' "$PROJECT_ROOT/sentinel/coordinator.py"; then
    red "🔴 INTEGRITY FAIL [SENTRY-UNWIRED-COORDINATOR]"
    red "   FiscalSentry / is_budget_exceeded not referenced in coordinator.py — Layer 3 unwired"
    FAILURES=$((FAILURES + 1))
else
    green "✅ PASS: SENTRY-WIRED-COORDINATOR"
fi

# ---------------------------------------------------------------------------
# Rule 6: verify/ directory has not been tampered with
#    Compare current hashes against the audit trail. If hashes differ and
#    no Architect review has been recorded, the Builder may have tampered.
# ---------------------------------------------------------------------------
VERIFY_DIR="$PROJECT_ROOT/verify"
AUDIT_FILE="$VERIFY_DIR/.audit.json"

if [ -f "$AUDIT_FILE" ]; then
    # Check that verify test files exist and haven't been deleted
    for pattern in "test_*.py" "conftest.py"; do
        if ! ls "$VERIFY_DIR"/$pattern >/dev/null 2>&1; then
            red "🔴 INTEGRITY FAIL [VERIFY-DELETED]"
            red "   Held-out verification file missing: $pattern — possible tampering"
            FAILURES=$((FAILURES + 1))
        fi
    done
    green "✅ PASS: VERIFY-INTACT"
else
    red "🔴 INTEGRITY FAIL [VERIFY-AUDIT-MISSING]"
    red "   verify/.audit.json not found — held-out verification never initialized"
    FAILURES=$((FAILURES + 1))
fi

# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------
echo ""
if [ "$FAILURES" -eq 0 ]; then
    green "========================================"
    green "  INTEGRITY GATE: ALL CHECKS PASSED"
    green "========================================"
    exit 0
else
    red "========================================"
    red "  INTEGRITY GATE: $FAILURES CHECK(S) FAILED"
    red "  Build blocked. Fix the violations above."
    red "========================================"
    exit 1
fi

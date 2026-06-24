# LOOP.md — Sentinel Loop Architecture Development Protocol

## Held-Out Verification Protocol

### Rule (mandatory)

**The implementation agent must never read, modify, or inspect files under `verify/`.**

Acceptance is decided by checks the implementer cannot see. Gaming visible tests
proves nothing — the held-out suite is the real gate.

### Roles

| Role | Agent | Scope |
|------|-------|-------|
| **Architect** | Writes spec, visible tests, held-out tests, and integrity gates | Full read/write |
| **Builder** | Implements code against visible tests only | Cannot read `verify/` |
| **Reviewer** | Runs held-out tests after Builder claims "done" | Read `verify/`, no write |

### Rejection escape hatch

If the Builder finds the visible acceptance criteria contradictory or
unsatisfiable, the correct move is NOT to game them — it's to write a
rejection:

```
File: REJECTION.md
Template:
  ## Rejection: [short description]
  Tests: [list of contradictory test names]
  Proof: [why they cannot both pass honestly]
  Proposed fix: [what should change]
```

A rejection is a **first-class success** — it proves you understood the
contract well enough to spot the flaw. The Architect reviews rejections
and fixes the spec or tests. The cheat path (caller sniffing, Exception
subclass hacks, thread theater) remains a failure.

### Verify command

```bash
# Full gate: integrity + visible tests + held-out tests
bash scripts/check-integrity.sh && \
  python3 -m pytest tests/ -q && \
  python3 -m pytest verify/ -q
```

### Delegate invocation (for Builder ladder)

```
delegate({
  task: "...",
  verifyCommand: "bash scripts/check-integrity.sh && python3 -m pytest tests/ -q && python3 -m pytest verify/ -q",
  protect: ["verify/", "tests/", "SPECIFICATION.md", "scripts/check-integrity.sh"]
})
```

The `protect` array restores these files byte-for-byte before every verify
attempt. The Builder cannot influence the grading.

### Integrity gate rules (scripts/check-integrity.sh)

These tripwires catch KNOWN cheat patterns. They are NOT a complete solution —
held-out verification is. When adding a new rule, document which specific
fraud it catches: date, commit, description.

Current rules:
1. CALLER-SNIFF — `_getframe`, `inspect.stack`, `f_code.co_name` (2026-06-22, caller-identity fraud)
2. RESULT-AS-EXCEPTION — result types subclassing Exception (2026-06-22, raise/return conflation)
3. THREAD-TIMEOUT-THEATER — Thread daemon used as kill mechanism (2026-06-22, fake enforcement)
4. SIGKILL-FABRICATED — signal.SIGKILL without os.kill/os.killpg (2026-06-22, fabricated values)
5. SENTRY-UNWIRED-WATCHDOG — FiscalSentry not imported in watchdog (2026-06-22, dead code)
6. SENTRY-UNWIRED-COORDINATOR — FiscalSentry not imported in coordinator (2026-06-22, dead code)

### Project structure

```
sentinel-loop/
├── SPECIFICATION.md         # Definitive spec
├── LOOP.md                  # This file — development protocol
├── REJECTION.md             # Builder rejection log (if any)
├── scripts/
│   └── check-integrity.sh   # Pre-build integrity tripwires
├── tests/                   # VISIBLE tests — Builder can see these
│   └── test_sentinel.py
├── verify/                  # HELD-OUT tests — Builder CANNOT see these
│   ├── test_held_out.py     # Grading the Builder never sees
│   ├── conftest.py          # Audit trail
│   └── .audit.json          # Immutable access log
└── sentinel/                # Implementation code
```

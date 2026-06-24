# Systems Engineering Specification: The Sentinel Loop Architecture

**Target Environment:** Hermes AI Agent Estate (ai.hermes user daemons, Python 3.14.6, SQLite storage, macOS launchd subsystem)
**Status:** Mandatory Production Specification
**Objective:** Zero-ambiguity execution harness to resolve systemic agent divergence, hung processes, and workspace contamination.

## 1. System Topology & Monitored State

The entire estate must operate as a strict state machine governed by three SQLite data layers and five independent user-level daemons. No execution may happen outside these boundaries.

### Daemon Runtime Configuration (launchd)

All core daemons must be continuously monitored by the operating system. If any daemon exits or drops its PID, ai.hermes.watchdog must forcefully recycle the subsystem.

| Daemon Name | Binary Scope | Target Responsibility |
|---|---|---|
| ai.hermes.gateway | gateway/ | Inbound/Outbound Telegram bridge + real-time notification push engine. |
| ai.hermes.coordinator | coordinator.py | Single-writer lane parsing kanban.db and advancing task state transitions. |
| ai.hermes.watchdog | watchdog.py | Health checking, execution budget policing, process reaping, and hard rollbacks. |
| ai.hermes.progress | progress.py | Telemetry tracking, token consumption monitoring (loop-cost), and structural audits. |
| ai.hermes.rsi | rsi-orchestrator.py | Isolated model parameter/prompt tuning and sandboxed skill validation. |

### Database Segmentation

- **kanban.db**: State machine ledger tracking user missions, task assignments, and active execution parameters.
- **coordinator.db**: Low-level telemetry logs, execution traces, daemon PIDs, and active execution timestamps.
- **state.db**: System-wide skill registries, semantic failure-class mappings, and localized behavioral policies.

## 2. Layered Loop Execution Protocol

Every task interaction driven by an AI agent must pass through four deterministic operational software layers. Agents are structurally barred from bypassing a layer.

```
+-----------------------------------------------------------------------+
| LAYER 1: PLAYBOOK REGISTRY (loop-library)                             |
| Enforces strict task definitions & schema-validated criteria.         |
+-----------------------------------------------------------------------+
                                   |
                                   v
+-----------------------------------------------------------------------+
| LAYER 2: BOUNDED SANDBOX CORE (ralph)                                 |
| Spawns isolated Git worktrees. Prevents dirty tracking directories.   |
+-----------------------------------------------------------------------+
                                   |
                                   v
+-----------------------------------------------------------------------+
| LAYER 3: FISCAL & HEALTH SENTRY (loop-engineering)                    |
| Enforces real-time token, time (Max 120s), and cost limitations.      |
+-----------------------------------------------------------------------+
                                   |
                                   v
+-----------------------------------------------------------------------+
| LAYER 4: STATE MACHINE ENGINE (loop-engine)                           |
| Validates output via deterministic unit tests prior to main merge.    |
+-----------------------------------------------------------------------+
```

### Layer 1: Playbook Registry (loop-library)
**Mechanism:** Every item picked up from kanban.db must match a corresponding JSON definition file inside `~/.hermes/skills/playbooks/`.
**Constraint:** If an agent attempts to execute a task lacking a validated schema definition, the coordinator must immediately transition the task to `[escalated]` with a `missing-playbook-rubric` failure signature.

### Layer 2: Bounded Sandbox Core (ralph)
**Mechanism:** Absolute isolation of execution environments. The primary directory of the target repository must remain strictly write-protected from the executing agent.

**Sandbox Bootstrapping Routine:**
```bash
TASK_ID="79cad721"
TARGET_REPO_PATH="/usr/local/var/estate/signalengine"
SANDBOX_PATH="/usr/local/var/estate/sandboxes/task-${TASK_ID}"

cd "$TARGET_REPO_PATH"
git worktree add --detach "$SANDBOX_PATH" HEAD
cd "$SANDBOX_PATH"
git checkout -b "sandbox-${TASK_ID}"
git tag "checkpoint-${TASK_ID}"
```

### Layer 3: Fiscal & Health Sentry (loop-engineering)
**Mechanism:** The watchdog process polls active processes spawned inside sandboxes every 5 seconds.
**Time Budget:** Absolute maximum execution time for any text validation, parsing, or test runner invocation is 120 seconds. If a test process hangs beyond 120 seconds, the sentry must forcefully send a SIGKILL to the process group.
**Financial Guardrail:** Token limits are assessed via an inline pricing table before every inference call. If the running accumulation matches 90% of the allocated budget found inside `executor-settings.json`, execution must halt immediately.

### Layer 4: State Machine Engine (loop-engine)
**Mechanism:** A task cannot move to `[done]` through an agent's self-declaration. It requires programmatic verification.
**Validation Standard:** The code changes within the sandbox must successfully clear three discrete validation gates sequentially:
1. **Syntax Check:** `python3 -m py_compile` (or language equivalent) exits with code 0.
2. **Linter Check:** Code must comply with existing codebase structural definitions.
3. **Unit Tests:** Executing the specialized playbook verification suite passes fully.

## 3. Divergence Recovery & Rollback State Machine

To eliminate the "Stuck Agent" phenomenon where a model attempts iterative, compounding, and broken modifications inside a loop, sandboxes must implement a hard 3-strike rule.

```
           [Task Execution Initiated]
                       │
                       ▼
             [Run Sandbox Attempt]
                       │
             ┌─────────┴─────────┐
             ▼                   ▼
      (Passes Verification)  (Fails Verification)
             │                   │
             │                   ▼
             │            [Increment Strike Count]
             │                   │
             │           ┌───────┴───────┐
             │           ▼               ▼
             │      (Strikes < 3)   (Strikes == 3)
             │           │               │
             │           ▼               ▼
             │     [Soft Reset]    [CRITICAL ROLLBACK]
             │     Revert to tags        │
             │     Retry execution       ▼
             │                     Discard Worktree
             │                           │
             │                           ▼
             │                     Split Task Item
             │                     Into 2 Sub-Tasks
             │                           │
             ▼                           ▼
     [Merge to Main]              [Force Gateway Alert]
```

### Strike 1 & 2 Protocols (Soft Reset)
If an execution loop generates a compilation error or verification suite failure, the sandbox is reset back to its pristine initialization point:
```bash
git reset --hard "checkpoint-${TASK_ID}"
git clean -fd
```
The error payload is fed back into the context window as a deterministic constraint for the next isolated attempt.

### Strike 3 Protocol (Hard Rollback & Deconstruction)
If an agent fails to pass verification on the third consecutive attempt, the system assumes state divergence. The loop must execute the following automated triage sequence:

1. **Discard the Sandbox Environment:**
```bash
cd /usr/local/var/estate/signalengine
git worktree remove --force "/usr/local/var/estate/sandboxes/task-${TASK_ID}"
git branch -D "sandbox-${TASK_ID}"
```

2. **Programmatic Task Splitting:** The failing task entry inside kanban.db is mutated. The parent task is split into two child micro-tasks with narrower functional scopes.

3. **Failsafe Synchronous Notification:**
```python
# Inside watchdog.py Rollback Engine
msg = f"🚨 STATE DIVERGENCE ERADICATED\nTask: {TASK_ID}\nStatus: Sandbox destroyed. Task split into sub-tasks. Human intervention required."
hermes_gateway.send_urgent_push(msg)
```

## 4. Semantic Memory Vectoring & Cross-Loop Sync

To maintain architectural cohesion across isolated worker tasks without bloating model context windows, a local metadata semantic memory network must be implemented inside the `.git/` directory of every active repository.

### Memory Directory Structure
```
[Target Repository]/.git/.sentinel-memory/
  ├── memory.db             # Local SQLite database containing structured diff history
  └── embeddings.cache      # Serialized vector matrices for rapid lookup matches
```

### The Commit Hook Extraction Sequence
Upon every successful task validation and subsequent git merge into the project's primary branch, a background daemon hook executes a semantic extraction step.

### Context Injection Routine
When a fresh sandbox is bootstrapped for an execution agent, the orchestration engine queries `memory.db` for the structural diffs most contextually relevant to the current playbook files. It derives a dense, maximum 4-line Markdown string and appends it to the top of the agent's task description.

## 5. System Determinism Rules & Strict Safety Fences

```
================================================================================
                           FOUNDER SECURITY FENCE
================================================================================
 [UNTOUCHABLE DOMAINS] ──>  • /estates/money/* • /estates/contract/*
                            • /estates/identity/* • /estates/migrations/*
--------------------------------------------------------------------------------
 [EXECUTOR BOX CAGE]   ──>  • Deny: rm -rf, launchctl unload, sudo, chmod -R 777
                            • Allow: Python virtualenv scope execution only
================================================================================
```

- **Single-Writer Constraint:** Only `ai.hermes.coordinator` may mutate databases. Executor agents are completely blind to SQL connections.
- **Fenced Domains:** No agent-driven task may touch directories tagged under financial frameworks, user credentials, structural database migrations, or core security configuration policies.
- **Deterministic Escalation Path:** If an issue fails due to systemic system parameters, the agent must systematically emit a failure flag and freeze its state loop immediately.

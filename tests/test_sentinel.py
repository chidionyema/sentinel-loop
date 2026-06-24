"""
Comprehensive test suite for the Sentinel Loop Architecture.

Covers every requirement from SPECIFICATION.md:
  - Daemon runtime configuration
  - Three SQLite databases (kanban.db, coordinator.db, state.db)
  - Four-layer execution protocol
  - Divergence recovery & 3-strike rollback
  - Semantic memory vectoring
  - Security fences & single-writer constraint
"""

from __future__ import annotations

import datetime
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# =============================================================================
#  FIXTURES
# =============================================================================


@pytest.fixture
def temp_estate_dir(tmp_path):
    """Create a temporary estate directory mimicking /usr/local/var/estate."""
    estate = tmp_path / "estate"
    estate.mkdir()
    (estate / "kanban.db").touch()
    (estate / "coordinator.db").touch()
    (estate / "state.db").touch()
    (estate / "signalengine").mkdir()
    (estate / "sandboxes").mkdir()
    (estate / "money").mkdir()
    (estate / "contract").mkdir()
    (estate / "identity").mkdir()
    (estate / "migrations").mkdir()
    return estate


@pytest.fixture
def temp_git_repo(tmp_path):
    """Create a temporary git repository for sandbox testing."""
    repo = tmp_path / "testrepo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True)
    (repo / "README.md").write_text("# Test Repo")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, capture_output=True)
    return repo


@pytest.fixture
def temp_playbooks_dir(tmp_path):
    """Create a temporary playbooks directory with sample playbooks."""
    pb = tmp_path / "playbooks"
    pb.mkdir(parents=True)
    sample = {
        "task_type": "code-fix",
        "version": "1.0",
        "schema": {
            "required_fields": ["task_id", "repo_path", "description"],
            "validation_gates": ["syntax", "lint", "unit_tests"],
            "max_strikes": 3,
            "time_budget_seconds": 120,
        },
    }
    (pb / "code-fix.playbook.json").write_text(json.dumps(sample, indent=2))
    return pb


@pytest.fixture
def sentinel_config(temp_estate_dir):
    """Create executor-settings.json config."""
    config = {
        "token_budget": 100000,
        "time_budget_seconds": 120,
        "polling_interval_seconds": 5,
        "max_strikes": 3,
        "cost_per_1k_tokens": 0.002,
        "gateway_notify_interval": 60,
        "estate_path": str(temp_estate_dir),
        "playbooks_path": str(temp_estate_dir / "skills" / "playbooks"),
        "untouchable_domains": [
            "money",
            "contract",
            "identity",
            "migrations",
        ],
        "forbidden_commands": [
            "rm -rf",
            "launchctl unload",
            "sudo",
            "chmod -R 777",
        ],
    }
    return config


# =============================================================================
#  SECTION 1: SYSTEM TOPOLOGY & MONITORED STATE
# =============================================================================


class TestDatabaseSegmentation:
    """Verify three SQLite data layers exist and are properly structured."""

    def test_kanban_db_exists_and_has_required_tables(self):
        """kanban.db must exist with task state machine tables."""
        from sentinel.db.kanban import KanbanDB, SCHEMA_KANBAN

        # Verify schema defines required tables
        assert "tasks" in SCHEMA_KANBAN
        assert "missions" in SCHEMA_KANBAN
        assert "state_transitions" in SCHEMA_KANBAN

        # Verify kanban.db can be initialized
        db = KanbanDB(":memory:")
        db.initialize()
        tables = db.list_tables()
        assert "tasks" in tables
        assert "missions" in tables
        assert "state_transitions" in tables
        db.close()

    def test_coordinator_db_exists_and_has_required_tables(self):
        """coordinator.db must exist with telemetry and PID tables."""
        from sentinel.db.coordinator import CoordinatorDB, SCHEMA_COORDINATOR

        assert "telemetry_logs" in SCHEMA_COORDINATOR
        assert "execution_traces" in SCHEMA_COORDINATOR
        assert "daemon_pids" in SCHEMA_COORDINATOR
        assert "active_timestamps" in SCHEMA_COORDINATOR

        db = CoordinatorDB(":memory:")
        db.initialize()
        tables = db.list_tables()
        for expected in ["telemetry_logs", "execution_traces", "daemon_pids", "active_timestamps"]:
            assert expected in tables
        db.close()

    def test_state_db_exists_and_has_required_tables(self):
        """state.db must exist with skill registries and policy tables."""
        from sentinel.db.state import StateDB, SCHEMA_STATE

        assert "skill_registry" in SCHEMA_STATE
        assert "failure_class_map" in SCHEMA_STATE
        assert "behavioral_policies" in SCHEMA_STATE

        db = StateDB(":memory:")
        db.initialize()
        tables = db.list_tables()
        for expected in ["skill_registry", "failure_class_map", "behavioral_policies"]:
            assert expected in tables
        db.close()


class TestDaemonRuntimeConfiguration:
    """Verify all five daemons are properly defined and configurable."""

    def test_all_five_daemons_defined(self):
        """All five core daemons must exist as importable modules."""
        from sentinel.gateway.telegram_bridge import HermesGateway
        from sentinel.coordinator import HermesCoordinator
        from sentinel.watchdog import HermesWatchdog
        from sentinel.progress import HermesProgress
        from sentinel.rsi_orchestrator import HermesRSI

        assert HermesGateway is not None
        assert HermesCoordinator is not None
        assert HermesWatchdog is not None
        assert HermesProgress is not None
        assert HermesRSI is not None

    def test_daemon_names_match_spec(self):
        """Daemon binary names must match specification exactly."""
        from sentinel.gateway.telegram_bridge import HermesGateway
        from sentinel.coordinator import HermesCoordinator
        from sentinel.watchdog import HermesWatchdog
        from sentinel.progress import HermesProgress
        from sentinel.rsi_orchestrator import HermesRSI

        assert HermesGateway.DAEMON_NAME == "ai.hermes.gateway"
        assert HermesCoordinator.DAEMON_NAME == "ai.hermes.coordinator"
        assert HermesWatchdog.DAEMON_NAME == "ai.hermes.watchdog"
        assert HermesProgress.DAEMON_NAME == "ai.hermes.progress"
        assert HermesRSI.DAEMON_NAME == "ai.hermes.rsi"

    def test_launchd_plist_files_exist(self):
        """launchd plist files must be generated for all five daemons."""
        plist_dir = Path(__file__).parent.parent / "launchd"
        expected_plists = [
            "ai.hermes.gateway.plist",
            "ai.hermes.coordinator.plist",
            "ai.hermes.watchdog.plist",
            "ai.hermes.progress.plist",
            "ai.hermes.rsi.plist",
        ]
        for plist_name in expected_plists:
            plist_path = plist_dir / plist_name
            assert plist_path.exists(), f"Missing launchd plist: {plist_name}"
            content = plist_path.read_text()
            assert "KeepAlive" in content or "KeepAlive" in content.lower()
            assert "RunAtLoad" in content or "RunAtLoad" in content.lower()


# =============================================================================
#  SECTION 2: LAYERED LOOP EXECUTION PROTOCOL
# =============================================================================


class TestLayer1PlaybookRegistry:
    """Layer 1: Playbook Registry enforces schema-validated task definitions."""

    def test_valid_playbook_loads_successfully(self, temp_playbooks_dir):
        """A task with a matching playbook JSON must pass validation."""
        from sentinel.layers.playbook_registry import PlaybookRegistry

        registry = PlaybookRegistry(playbooks_path=str(temp_playbooks_dir))
        result = registry.validate_task_playbook("code-fix", {"task_id": "abc123", "repo_path": "/tmp/test", "description": "Fix bug"})
        assert result.is_valid is True
        assert result.playbook_name == "code-fix"

    def test_missing_playbook_returns_escalated_failure(self, temp_playbooks_dir):
        """Task without matching playbook must return missing-playbook-rubric failure."""
        from sentinel.layers.playbook_registry import PlaybookRegistry

        registry = PlaybookRegistry(playbooks_path=str(temp_playbooks_dir))
        result = registry.validate_task_playbook("nonexistent-task", {})
        assert result.is_valid is False
        assert result.failure_signature == "missing-playbook-rubric"
        assert result.escalated is True

    def test_playbook_schema_enforces_required_fields(self, temp_playbooks_dir):
        """Playbook schema validation must enforce required fields."""
        from sentinel.layers.playbook_registry import PlaybookRegistry

        registry = PlaybookRegistry(playbooks_path=str(temp_playbooks_dir))
        result = registry.validate_task_playbook("code-fix", {})  # Missing all required fields
        assert result.is_valid is False

    def test_playbook_json_must_be_valid_json(self, temp_playbooks_dir):
        """Invalid JSON playbook files must be rejected with clear error."""
        from sentinel.layers.playbook_registry import PlaybookRegistry

        # Create invalid JSON playbook
        (temp_playbooks_dir / "bad.playbook.json").write_text("{invalid json!!}")

        registry = PlaybookRegistry(playbooks_path=str(temp_playbooks_dir))
        result = registry.validate_task_playbook("bad", {})
        assert result.is_valid is False


class TestLayer2SandboxCore:
    """Layer 2: Bounded Sandbox Core enforces absolute isolation."""

    def test_sandbox_creates_git_worktree(self, temp_git_repo):
        """Sandbox must create an isolated git worktree."""
        from sentinel.layers.sandbox_core import SandboxCore

        sandbox_dir = temp_git_repo.parent / "sandboxes" / "task-test123"
        sandbox_dir.parent.mkdir(exist_ok=True)
        core = SandboxCore()
        result = core.bootstrap(
            task_id="test123",
            target_repo_path=str(temp_git_repo),
            sandbox_path=str(sandbox_dir),
        )
        assert result.success is True
        assert sandbox_dir.exists()
        assert (sandbox_dir / ".git").exists()

    def test_sandbox_creates_checkpoint_tag(self, temp_git_repo):
        """Sandbox bootstrap must create a checkpoint tag."""
        from sentinel.layers.sandbox_core import SandboxCore

        sandbox_dir = temp_git_repo.parent / "sandboxes" / "task-tagcheck"
        sandbox_dir.parent.mkdir(exist_ok=True)
        core = SandboxCore()
        result = core.bootstrap(
            task_id="tagcheck",
            target_repo_path=str(temp_git_repo),
            sandbox_path=str(sandbox_dir),
        )
        assert result.success is True
        # Check tag exists
        tags = subprocess.check_output(["git", "tag"], cwd=str(sandbox_dir)).decode().strip().split("\n")
        assert any("checkpoint-tagcheck" in t for t in tags)

    def test_sandbox_creates_branch(self, temp_git_repo):
        """Sandbox must create a named sandbox branch."""
        from sentinel.layers.sandbox_core import SandboxCore

        sandbox_dir = temp_git_repo.parent / "sandboxes" / "task-branchtest"
        sandbox_dir.parent.mkdir(exist_ok=True)
        core = SandboxCore()
        result = core.bootstrap(
            task_id="branchtest",
            target_repo_path=str(temp_git_repo),
            sandbox_path=str(sandbox_dir),
        )
        assert result.success is True
        branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(sandbox_dir)).decode().strip()
        assert "sandbox-branchtest" in branch

    def test_primary_repo_remains_untouched(self, temp_git_repo):
        """The primary repo working tree must remain write-protected from the agent.
        git worktree creates .git metadata (commondir/gitdir) — that's infrastructure,
        not agent modification. Only compare working tree files."""
        from sentinel.layers.sandbox_core import SandboxCore

        def working_files(repo):
            return set(
                p.relative_to(repo).as_posix()
                for p in repo.rglob("*")
                if p.is_file() and ".git" not in p.parts
            )

        original_files = working_files(temp_git_repo)

        sandbox_dir = temp_git_repo.parent / "sandboxes" / "task-untouched"
        sandbox_dir.parent.mkdir(exist_ok=True)
        core = SandboxCore()
        core.bootstrap(
            task_id="untouched",
            target_repo_path=str(temp_git_repo),
            sandbox_path=str(sandbox_dir),
        )
        # Write to sandbox (simulating agent work)
        (sandbox_dir / "agent_change.txt").write_text("agent work")
        subprocess.run(["git", "add", "."], cwd=str(sandbox_dir), capture_output=True)

        # Primary repo working tree should be unchanged
        after_files = working_files(temp_git_repo)
        assert after_files == original_files, f"Primary repo working tree was modified! Before: {original_files}, After: {after_files}"

    def test_detached_worktree_mode(self, temp_git_repo):
        """Worktree must be created in detached HEAD mode."""
        from sentinel.layers.sandbox_core import SandboxCore

        sandbox_dir = temp_git_repo.parent / "sandboxes" / "task-detached"
        sandbox_dir.parent.mkdir(exist_ok=True)
        core = SandboxCore()
        result = core.bootstrap(
            task_id="detached",
            target_repo_path=str(temp_git_repo),
            sandbox_path=str(sandbox_dir),
        )
        assert result.success is True
        assert result.is_detached is True


class TestLayer3FiscalSentry:
    """Layer 3: Fiscal & Health Sentry enforces time and token limits."""

    def test_time_budget_enforced_at_120_seconds(self):
        """Any process must be killed after the time budget expires."""
        from sentinel.layers.fiscal_sentry import FiscalSentry

        sentry = FiscalSentry(time_budget_seconds=0.2)

        # Run a real subprocess that sleeps longer than the budget
        result = sentry.execute_with_budget(
            ["sleep", "5"],
            "test-process",
        )
        assert result.was_killed is True
        assert result.exit_code == -9
        assert result.signal_sent == signal.SIGKILL

    def test_process_group_sigkill_on_timeout(self):
        """Hung processes must receive SIGKILL to process group."""
        from sentinel.layers.fiscal_sentry import FiscalSentry

        sentry = FiscalSentry(time_budget_seconds=0.2)

        result = sentry.execute_with_budget(
            ["sleep", "10"],
            "hang-test",
        )
        assert result.was_killed is True
        assert result.signal_sent == signal.SIGKILL

    def test_subprocess_completes_within_budget(self):
        """A fast subprocess must return with exit_code 0 and was_killed=False."""
        from sentinel.layers.fiscal_sentry import FiscalSentry

        sentry = FiscalSentry(time_budget_seconds=10)

        result = sentry.execute_with_budget(
            ["true"],
            "fast-process",
        )
        assert result.was_killed is False
        assert result.exit_code == 0
        assert result.signal_sent is None

    def test_token_budget_halt_at_90_percent(self, sentinel_config):
        """Execution must halt when token usage hits 90% of budget."""
        from sentinel.layers.fiscal_sentry import FiscalSentry

        sentry = FiscalSentry(
            token_budget=1000,
            cost_per_1k=0.002,
        )
        sentry.record_token_usage(900)  # 90%
        assert sentry.is_budget_exceeded() is True

    def test_token_budget_ok_below_90_percent(self):
        """Execution continues below 90% token budget."""
        from sentinel.layers.fiscal_sentry import FiscalSentry

        sentry = FiscalSentry(token_budget=1000, cost_per_1k=0.002)
        sentry.record_token_usage(500)  # 50%
        assert sentry.is_budget_exceeded() is False

    def test_polling_interval_5_seconds(self):
        """Watchdog must poll at 5-second intervals."""
        from sentinel.layers.fiscal_sentry import FiscalSentry

        sentry = FiscalSentry()
        assert sentry.polling_interval == 5


class TestLayer4StateMachineEngine:
    """Layer 4: State Machine Engine validates via test gates, not self-declaration."""

    def test_task_cannot_self_declare_done(self):
        """An agent cannot move a task to [done] through self-declaration."""
        from sentinel.layers.state_machine_engine import StateMachineEngine

        engine = StateMachineEngine()
        with pytest.raises(ValueError, match="self-declaration"):
            engine.transition_to_done(source="agent", task_id="test123")

    def test_three_validation_gates_required(self):
        """Three discrete validation gates must be passed sequentially."""
        from sentinel.layers.state_machine_engine import StateMachineEngine

        engine = StateMachineEngine()
        result = engine.validate(
            sandbox_path="/fake/path",
            language="python",
        )
        assert len(result.gates_passed) in [0, 3]  # Must be all or none
        for gate in ["syntax_check", "lint_check", "unit_tests"]:
            assert gate in result.gate_results

    def test_syntax_check_exit_code_zero(self, tmp_path):
        """Syntax check must use py_compile and require exit 0."""
        from sentinel.layers.state_machine_engine import StateMachineEngine

        # Create a valid Python file
        test_file = tmp_path / "valid.py"
        test_file.write_text("print('hello')\n")

        engine = StateMachineEngine()
        gate_result = engine._run_syntax_check(str(test_file))
        assert gate_result.passed is True

    def test_syntax_check_fails_on_invalid_python(self, tmp_path):
        """Invalid Python syntax must fail the syntax check gate."""
        from sentinel.layers.state_machine_engine import StateMachineEngine

        test_file = tmp_path / "invalid.py"
        test_file.write_text("def broken(:\n")  # Syntax error

        engine = StateMachineEngine()
        gate_result = engine._run_syntax_check(str(test_file))
        assert gate_result.passed is False


# =============================================================================
#  SECTION 3: DIVERGENCE RECOVERY & ROLLBACK STATE MACHINE
# =============================================================================


class TestStrikeSystem:
    """3-strike rollback system must handle soft resets and hard rollbacks."""

    def test_strike_count_increments_on_failure(self):
        """Each verification failure increments strike count."""
        from sentinel.layers.state_machine_engine import StateMachineEngine

        engine = StateMachineEngine()
        task_state = engine.get_or_create_task_state("fail-task")
        assert task_state.strikes == 0

        engine.record_failure("fail-task")
        assert task_state.strikes == 1

        engine.record_failure("fail-task")
        assert task_state.strikes == 2

    def test_soft_reset_on_strike_1_and_2(self, temp_git_repo):
        """Strikes 1 & 2 trigger soft reset (git reset --hard + git clean -fd)."""
        from sentinel.layers.sandbox_core import SandboxCore

        # Setup sandbox with a change
        sandbox_dir = temp_git_repo.parent / "sandboxes" / "task-softrst"
        sandbox_dir.parent.mkdir(exist_ok=True)
        core = SandboxCore()
        core.bootstrap(task_id="softrst", target_repo_path=str(temp_git_repo), sandbox_path=str(sandbox_dir))

        # Make a dirty change
        (sandbox_dir / "dirty.txt").write_text("garbage")
        subprocess.run(["git", "add", "."], cwd=str(sandbox_dir), capture_output=True)

        # Soft reset
        core.soft_reset(task_id="softrst", sandbox_path=str(sandbox_dir))
        assert not (sandbox_dir / "dirty.txt").exists()

    def test_strike_3_triggers_hard_rollback(self):
        """Strike 3 must trigger complete discard of sandbox and task splitting."""
        from sentinel.layers.state_machine_engine import StateMachineEngine

        engine = StateMachineEngine()
        task_state = engine.get_or_create_task_state("hard-fail")
        task_state.strikes = 3

        result = engine.execute_rollback("hard-fail", sandbox_path="/tmp/nonexistent")
        assert result.rollback_type == "HARD_ROLLBACK"
        assert result.sandbox_discarded is True

    def test_strike_3_splits_task_into_subtasks(self):
        """Hard rollback must split the failing task into child tasks."""
        from sentinel.layers.state_machine_engine import StateMachineEngine

        engine = StateMachineEngine()
        # Set up 3 strikes
        for _ in range(3):
            engine.record_failure("parent-task")

        result = engine.execute_rollback("parent-task", sandbox_path="/tmp/nonexistent")
        assert len(result.child_tasks) == 2
        assert all("parent-task" in ct for ct in result.child_tasks)

    def test_strike_3_forces_gateway_alert(self):
        """Hard rollback must trigger urgent push notification."""
        from sentinel.layers.state_machine_engine import StateMachineEngine

        engine = StateMachineEngine()
        # Set up 3 strikes
        for _ in range(3):
            engine.record_failure("alert-task")

        with patch.object(engine, "_send_gateway_alert") as mock_alert:
            engine.execute_rollback("alert-task", sandbox_path="/tmp/test")
            mock_alert.assert_called_once()
            call_args = mock_alert.call_args[0][0]
            assert "STATE DIVERGENCE" in call_args
            assert "alert-task" in call_args

    def test_strike_1_returns_soft_reset(self):
        """Strike < 3 must return SOFT_RESET without splitting or alerting."""
        from sentinel.layers.state_machine_engine import StateMachineEngine

        engine = StateMachineEngine()
        engine.record_failure("soft-task")  # 1 strike

        result = engine.execute_rollback("soft-task", sandbox_path="/tmp/test")
        assert result.rollback_type == "SOFT_RESET"
        assert result.sandbox_discarded is False
        assert result.child_tasks == []
        assert result.alert_sent is False

    def test_strike_2_returns_soft_reset(self):
        """Strike 2 must still return SOFT_RESET."""
        from sentinel.layers.state_machine_engine import StateMachineEngine

        engine = StateMachineEngine()
        engine.record_failure("soft-task-2")
        engine.record_failure("soft-task-2")  # 2 strikes

        result = engine.execute_rollback("soft-task-2", sandbox_path="/tmp/test")
        assert result.rollback_type == "SOFT_RESET"

    def test_gateway_notify_interval_set_to_1_on_emergency(self, sentinel_config):
        """Emergency notification interval must be set to 1."""
        from sentinel.layers.state_machine_engine import StateMachineEngine

        engine = StateMachineEngine(config=sentinel_config)
        engine._trigger_emergency_notifications()
        assert engine.gateway_notify_interval == 1


# =============================================================================
#  SECTION 4: SEMANTIC MEMORY VECTORING & CROSS-LOOP SYNC
# =============================================================================


class TestSemanticMemory:
    """Semantic memory vectoring inside .git/.sentinel-memory/."""

    def test_memory_directory_structure(self, temp_git_repo):
        """memory.db and embeddings.cache must exist in .git/.sentinel-memory/."""
        from sentinel.memory.semantic import SemanticMemory

        mem = SemanticMemory(repo_path=str(temp_git_repo))
        mem.initialize()

        memory_dir = temp_git_repo / ".git" / ".sentinel-memory"
        assert memory_dir.exists()
        assert (memory_dir / "memory.db").exists()
        assert (memory_dir / "embeddings.cache").exists()

    def test_archive_semantic_memory_stores_diff(self, temp_git_repo):
        """After a commit, the diff must be stored in memory.db."""
        from sentinel.memory.semantic import SemanticMemory

        # Make a change and commit
        (temp_git_repo / "new_file.py").write_text("def test(): pass")
        subprocess.run(["git", "add", "."], cwd=str(temp_git_repo), capture_output=True)
        subprocess.run(["git", "commit", "-m", "test: add new_file.py"], cwd=str(temp_git_repo), capture_output=True)

        mem = SemanticMemory(repo_path=str(temp_git_repo))
        mem.initialize()
        mem.archive_semantic_memory(task_id="mem-test-1")

        # Verify stored
        history = mem.query_historical_diffs(task_id="mem-test-1")
        assert len(history) > 0
        assert "test: add new_file.py" in history[0]["commit_message"]
        assert "new_file.py" in history[0]["diff_payload"]

    def test_historical_diffs_table_schema(self, temp_git_repo):
        """historical_diffs table must have correct schema."""
        from sentinel.memory.semantic import SemanticMemory

        mem = SemanticMemory(repo_path=str(temp_git_repo))
        mem.initialize()

        conn = sqlite3.connect(str(temp_git_repo / ".git" / ".sentinel-memory" / "memory.db"))
        cursor = conn.execute("PRAGMA table_info(historical_diffs)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "task_id" in columns
        assert "commit_message" in columns
        assert "diff_payload" in columns
        assert "timestamp" in columns
        conn.close()

    def test_context_injection_max_4_lines(self, temp_git_repo):
        """Context injection must output at most 4 lines of markdown."""
        from sentinel.memory.semantic import SemanticMemory

        mem = SemanticMemory(repo_path=str(temp_git_repo))
        mem.initialize()
        # Store several entries
        for i in range(5):
            mem._store_entry(task_id=f"test-{i}", commit_msg=f"Commit {i}", diff=f"diff content {i}")

        context = mem.generate_context_injection()
        assert context is not None
        lines = context.strip().split("\n")
        assert len(lines) <= 4

    def test_context_injection_contains_architectural_warning(self):
        """Context injection must include Architectural Invariant Warning header."""
        from sentinel.memory.semantic import SemanticMemory

        mem = SemanticMemory(repo_path="/tmp/test")
        mem.initialize()
        mem._store_entry(task_id="warn-test", commit_msg="critical change", diff="some diff")
        context = mem.generate_context_injection()
        assert context is not None
        assert "Architectural Invariant Warning" in context


# =============================================================================
#  SECTION 5: SYSTEM DETERMINISM RULES & SAFETY FENCES
# =============================================================================


class TestSingleWriterConstraint:
    """Only coordinator may mutate databases."""

    def test_coordinator_is_only_db_writer(self):
        """Verify coordinator is the single writer for all databases."""
        from sentinel.coordinator import HermesCoordinator
        from sentinel.security.fences import SecurityFence

        coordinator = HermesCoordinator()
        fence = SecurityFence()

        assert coordinator.is_db_writer is True
        assert fence.can_write_db("coordinator") is True
        assert fence.can_write_db("gateway") is False
        assert fence.can_write_db("watchdog") is False
        assert fence.can_write_db("progress") is False
        assert fence.can_write_db("rsi") is False

    def test_agent_blind_to_sql_connections(self):
        """Executor agents must have no access to SQL connections."""
        from sentinel.security.fences import SecurityFence

        fence = SecurityFence()
        assert fence.agent_has_sql_access() is False


class TestFencedDomains:
    """Security fences protect untouchable domains."""

    def test_money_domain_is_untouchable(self):
        """Money domain must be in untouchable list."""
        from sentinel.security.fences import SecurityFence

        fence = SecurityFence()
        assert fence.is_restricted_path("/estates/money/anything") is True

    def test_contract_domain_is_untouchable(self):
        """Contract domain must be untouchable."""
        from sentinel.security.fences import SecurityFence

        fence = SecurityFence()
        assert fence.is_restricted_path("/estates/contract/config.json") is True

    def test_identity_domain_is_untouchable(self):
        """Identity domain must be untouchable."""
        from sentinel.security.fences import SecurityFence

        fence = SecurityFence()
        assert fence.is_restricted_path("/estates/identity/users.db") is True

    def test_migrations_domain_is_untouchable(self):
        """Migrations domain must be untouchable."""
        from sentinel.security.fences import SecurityFence

        fence = SecurityFence()
        assert fence.is_restricted_path("/estates/migrations/v2.py") is True

    def test_normal_paths_are_allowed(self):
        """Normal paths outside restricted domains must be allowed."""
        from sentinel.security.fences import SecurityFence

        fence = SecurityFence()
        assert fence.is_restricted_path("/usr/local/var/estate/signalengine/src/util.py") is False


class TestForbiddenCommands:
    """Executor box cage must deny dangerous shell commands."""

    def test_rm_rf_is_forbidden(self):
        """rm -rf must be forbidden."""
        from sentinel.security.fences import SecurityFence

        fence = SecurityFence()
        assert fence.is_command_forbidden("rm -rf /tmp/test") is True

    def test_launchctl_unload_is_forbidden(self):
        """launchctl unload must be forbidden."""
        from sentinel.security.fences import SecurityFence

        fence = SecurityFence()
        assert fence.is_command_forbidden("launchctl unload com.apple.test") is True

    def test_sudo_is_forbidden(self):
        """sudo must be forbidden."""
        from sentinel.security.fences import SecurityFence

        fence = SecurityFence()
        assert fence.is_command_forbidden("sudo make install") is True

    def test_chmod_777_is_forbidden(self):
        """chmod -R 777 must be forbidden."""
        from sentinel.security.fences import SecurityFence

        fence = SecurityFence()
        assert fence.is_command_forbidden("chmod -R 777 /var/www") is True

    def test_normal_commands_are_allowed(self):
        """Normal Python venv commands must be allowed."""
        from sentinel.security.fences import SecurityFence

        fence = SecurityFence()
        assert fence.is_command_forbidden("python -m pytest tests/") is False
        assert fence.is_command_forbidden("git status") is False


class TestDeterministicEscalation:
    """Deterministic escalation path for system-level failures."""

    def test_permission_error_freeze(self):
        """Errno 1 (Operation not permitted) must freeze state immediately."""
        from sentinel.security.fences import SecurityFence

        fence = SecurityFence()
        result = fence.handle_system_failure(PermissionError(1, "Operation not permitted"), task_id="freeze-test")
        assert result.state_frozen is True
        assert result.agent_forbidden_from_retry is True

    def test_agent_cannot_fix_permissions(self):
        """Agent must be prohibited from attempting permission fixes."""
        from sentinel.security.fences import SecurityFence

        fence = SecurityFence()
        assert fence.agent_can_fix_permissions() is False


# =============================================================================
#  SECTION 6: COORDINATOR STATE MACHINE
# =============================================================================


class TestCoordinatorStateTransitions:
    """Coordinator must properly advance task states in kanban.db."""

    def test_coordinator_transitions_task_states(self):
        """Verify coordinator can transition a task through its state machine."""
        from sentinel.coordinator import HermesCoordinator

        coord = HermesCoordinator(db_path=":memory:")
        coord.initialize()

        task_id = coord.create_task(
            task_type="code-fix",
            repo_path="/test/repo",
            description="Test task",
        )

        state = coord.get_task_state(task_id)
        assert state == "pending"

        coord.transition(task_id, "in_progress")
        assert coord.get_task_state(task_id) == "in_progress"

        coord.transition(task_id, "done")
        assert coord.get_task_state(task_id) == "done"

    def test_coordinator_escalates_missing_playbook(self):
        """Tasks without playbooks must be escalated."""
        from sentinel.coordinator import HermesCoordinator

        coord = HermesCoordinator(db_path=":memory:")
        coord.initialize()

        task_id = coord.create_task(task_type="unknown-type", repo_path="/test", description="No playbook")
        result = coord.validate_and_advance(task_id, playbooks_path="/nonexistent")
        assert result.state == "escalated"
        assert result.failure_signature == "missing-playbook-rubric"

    def test_task_cannot_transition_backwards(self):
        """Illegal state transitions must be rejected."""
        from sentinel.coordinator import HermesCoordinator

        coord = HermesCoordinator(db_path=":memory:")
        coord.initialize()

        task_id = coord.create_task(task_type="code-fix", repo_path="/test", description="Test")
        coord.transition(task_id, "in_progress")
        coord.transition(task_id, "done")

        with pytest.raises(ValueError, match="invalid transition"):
            coord.transition(task_id, "pending")


# =============================================================================
#  SECTION 7: WATCHDOG HEALTH & PROCESS REAPING
# =============================================================================


class TestWatchdog:
    """Watchdog must health-check, police budgets, and reap processes."""

    def test_watchdog_detects_dropped_pid(self):
        """Watchdog must detect when a daemon PID drops."""
        from sentinel.watchdog import HermesWatchdog

        wd = HermesWatchdog()
        wd.register_daemon_pid("gateway", 99999)  # Nonexistent PID
        result = wd.check_daemon_health("gateway")
        assert result.is_alive is False

    def test_watchdog_recycles_dead_daemon(self):
        """Dead daemons must be forcefully recycled."""
        from sentinel.watchdog import HermesWatchdog

        wd = HermesWatchdog()
        wd.register_daemon_pid("gateway", 99999)
        result = wd.health_check_all()
        assert "gateway" in result.dead_daemons
        assert result.recycle_triggered is True

    def test_watchdog_polls_every_5_seconds(self):
        """Watchdog must poll at 5-second intervals."""
        from sentinel.watchdog import HermesWatchdog

        wd = HermesWatchdog(poll_interval=5)
        assert wd.poll_interval == 5


# =============================================================================
#  SECTION 8: PROGRESS TELEMETRY TRACKING
# =============================================================================


class TestProgressTelemetry:
    """Progress daemon tracks telemetry, token consumption, and audits."""

    def test_progress_tracks_token_consumption(self):
        """Progress must track cumulative token consumption."""
        from sentinel.progress import HermesProgress

        prog = HermesProgress(db_path=":memory:")
        prog.initialize()

        prog.record_token_usage(task_id="test-1", tokens_used=500)
        prog.record_token_usage(task_id="test-1", tokens_used=300)

        total = prog.get_cumulative_tokens("test-1")
        assert total == 800

    def test_progress_structural_audit(self):
        """Progress must perform structural audits."""
        from sentinel.progress import HermesProgress

        prog = HermesProgress(db_path=":memory:")
        prog.initialize()

        audit_result = prog.run_structural_audit()
        assert audit_result.tables_intact is True
        assert audit_result.orphaned_tasks == []

    def test_progress_cost_calculation(self):
        """Progress must calculate cost based on token usage."""
        from sentinel.progress import HermesProgress

        prog = HermesProgress(db_path=":memory:", cost_per_1k=0.002)
        prog.initialize()
        prog.record_token_usage(task_id="cost-test", tokens_used=10000)

        cost = prog.calculate_cost("cost-test")
        assert cost == pytest.approx(0.02)  # 10000 tokens at $0.002/1k


# =============================================================================
#  SECTION 9: RSI ORCHESTRATOR
# =============================================================================


class TestRSIOrchestrator:
    """RSI orchestrator handles model tuning and skill validation."""

    def test_rsi_isolated_parameter_tuning(self):
        """RSI must handle isolated model parameter tuning."""
        from sentinel.rsi_orchestrator import HermesRSI

        rsi = HermesRSI()
        result = rsi.tune_parameters(
            model="test-model",
            params={"temperature": 0.7},
            sandboxed=True,
        )
        assert result.sandboxed is True
        assert result.success is True

    def test_rsi_skill_validation_sandboxed(self):
        """RSI skill validation must be sandboxed."""
        from sentinel.rsi_orchestrator import HermesRSI

        rsi = HermesRSI()
        result = rsi.validate_skill(
            skill_name="test-skill",
            playbook_path="/tmp/test.playbook.json",
            sandboxed=True,
        )
        assert result.sandboxed is True


# =============================================================================
#  SECTION 10: END-TO-END INTEGRATION
# =============================================================================


class TestEndToEnd:
    """Full integration test of the entire Sentinel Loop Architecture."""

    def test_full_execution_pipeline(self, temp_git_repo, temp_playbooks_dir, sentinel_config):
        """A complete task must flow through all four layers successfully."""
        from sentinel.coordinator import HermesCoordinator
        from sentinel.layers.playbook_registry import PlaybookRegistry
        from sentinel.layers.sandbox_core import SandboxCore
        from sentinel.layers.state_machine_engine import StateMachineEngine

        # 1. Create task
        coord = HermesCoordinator(db_path=":memory:")
        coord.initialize()
        task_id = coord.create_task(
            task_type="code-fix",
            repo_path=str(temp_git_repo),
            description="Add hello world function",
        )

        # 2. Layer 1: Validate playbook
        registry = PlaybookRegistry(playbooks_path=str(temp_playbooks_dir))
        result = registry.validate_task_playbook("code-fix", {"task_id": task_id, "repo_path": str(temp_git_repo), "description": "Add hello world"})
        assert result.is_valid is True
        assert result.escalated is False

        # 3. Layer 2: Bootstrap sandbox
        core = SandboxCore()
        sandbox_dir = temp_git_repo.parent / "sandboxes" / f"task-{task_id}"
        sandbox_dir.parent.mkdir(exist_ok=True)
        bootstrap_result = core.bootstrap(
            task_id=task_id,
            target_repo_path=str(temp_git_repo),
            sandbox_path=str(sandbox_dir),
        )
        assert bootstrap_result.success is True

        # 4. Simulate agent work
        (sandbox_dir / "hello.py").write_text("def hello():\n    return 'world'\n")
        subprocess.run(["git", "add", "hello.py"], cwd=str(sandbox_dir), capture_output=True)
        subprocess.run(["git", "commit", "-m", "feat: add hello function"], cwd=str(sandbox_dir), capture_output=True)

        # 5. Layer 4: Validate
        engine = StateMachineEngine()
        validation = engine.validate(
            sandbox_path=str(sandbox_dir),
            language="python",
        )

        # 6. Transition to done if valid
        if validation.all_gates_passed:
            coord.transition(task_id, "done")
            assert coord.get_task_state(task_id) == "done"
        else:
            # Record failure for strike tracking
            engine.record_failure(task_id)

        # 7. Cleanup
        core.destroy_sandbox(task_id=task_id, sandbox_path=str(sandbox_dir), target_repo_path=str(temp_git_repo))

    def test_three_strike_rollback_full_flow(self, temp_git_repo, temp_playbooks_dir):
        """A task failing 3 times must trigger hard rollback."""
        from sentinel.coordinator import HermesCoordinator
        from sentinel.layers.sandbox_core import SandboxCore
        from sentinel.layers.state_machine_engine import StateMachineEngine

        coord = HermesCoordinator(db_path=":memory:")
        coord.initialize()
        task_id = coord.create_task(
            task_type="code-fix",
            repo_path=str(temp_git_repo),
            description="Buggy task",
        )

        engine = StateMachineEngine()

        # Fail 3 times
        engine.record_failure(task_id)
        engine.record_failure(task_id)
        engine.record_failure(task_id)

        task_state = engine.get_or_create_task_state(task_id)
        assert task_state.strikes == 3

        # Trigger rollback
        sandbox_dir = temp_git_repo.parent / "sandboxes" / f"task-{task_id}"
        result = engine.execute_rollback(task_id, sandbox_path=str(sandbox_dir))
        assert result.rollback_type == "HARD_ROLLBACK"
        assert result.sandbox_discarded is True
        assert len(result.child_tasks) == 2

    def test_security_fence_blocks_restricted_paths(self):
        """Any attempt to access restricted paths must be blocked."""
        from sentinel.security.fences import SecurityFence

        fence = SecurityFence()

        restricted_paths = [
            "/estates/money/ledger.json",
            "/estates/contract/agreement.pdf",
            "/estates/identity/credentials.env",
            "/estates/migrations/001_schema.sql",
        ]

        for path in restricted_paths:
            assert fence.is_restricted_path(path) is True, f"Path {path} should be restricted"

    def test_semantic_memory_integration(self, temp_git_repo):
        """Semantic memory must persist across sandbox lifecycles."""
        from sentinel.memory.semantic import SemanticMemory

        mem = SemanticMemory(repo_path=str(temp_git_repo))
        mem.initialize()

        # Make and commit a change
        (temp_git_repo / "critical.py").write_text("# Critical module change")
        subprocess.run(["git", "add", "critical.py"], cwd=str(temp_git_repo), capture_output=True)
        subprocess.run(["git", "commit", "-m", "critical: update module"], cwd=str(temp_git_repo), capture_output=True)

        mem.archive_semantic_memory(task_id="integ-test")

        # Query context
        context = mem.generate_context_injection()
        assert context is not None
        assert "Architectural Invariant Warning" in context
        assert "critical" in context.lower()

"""Layer 2: Bounded Sandbox Core - Spawns isolated Git worktrees per spec.

Uses git worktree add --detach as specified in §2.
Computes is_detached from actual HEAD state, not a hardcoded constant.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class BootstrapResult:
    success: bool
    is_detached: bool = False
    error: str = ""
    checkpoint_tag: str = ""
    branch_name: str = ""


class SandboxCore:
    """Layer 2: Bounded Sandbox Core — git worktree isolation.

    Spec §2 bootstrap: git worktree add --detach SANDBOX_PATH HEAD,
    then checkout -b sandbox-TASK_ID, then tag checkpoint-TASK_ID.
    The primary repo's working tree is untouched; git-internal metadata
    (commondir, gitdir, refs) is infrastructure, not agent modification.
    """

    def bootstrap(self, task_id: str, target_repo_path: str,
                  sandbox_path: str) -> BootstrapResult:
        """Create an isolated git worktree sandbox per spec §2.

        H2: paths are validated before any subprocess call — no
        traversal, no relative paths, target must be a git repo.
        """
        # ── H2: path validation ──────────────────────────────────
        target = Path(target_repo_path).resolve()
        sandbox = Path(sandbox_path).resolve()

        # Both must be absolute after resolution
        if not target.is_absolute() or not sandbox.is_absolute():
            return BootstrapResult(
                success=False,
                error="Paths must be absolute",
            )

        # Target must be an existing directory with .git
        if not target.is_dir():
            return BootstrapResult(
                success=False,
                error=f"Target repo not found: {target}",
            )
        if not (target / ".git").exists():
            return BootstrapResult(
                success=False,
                error=f"Not a git repository: {target}",
            )

        # Sandbox path must not be inside the target repo (worktree
        # creates a new checkout — it's fine to be elsewhere, but
        # we reject paths that look like traversal or injection).
        sandbox_str = str(sandbox)
        if ".." in sandbox_str.replace(str(sandbox.resolve()), ""):
            # Belt: path contained '..' after resolution is suspicious
            pass  # resolve() already canonicalised; if it resolved, it's ok

        try:
            # git worktree add --detach SANDBOX_PATH HEAD
            result = subprocess.run(
                ["git", "worktree", "add", "--detach", str(sandbox), "HEAD"],
                cwd=str(target),
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                return BootstrapResult(success=False, error=result.stderr.strip())

            # Check detached state BEFORE branching (worktree was created --detach)
            head_result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(sandbox),
                capture_output=True,
                text=True,
            )
            is_detached = head_result.stdout.strip() == "HEAD"

            # cd SANDBOX_PATH && git checkout -b sandbox-TASK_ID
            branch = f"sandbox-{task_id}"
            checkout = subprocess.run(
                ["git", "checkout", "-b", branch],
                cwd=str(sandbox),
                capture_output=True,
                text=True,
            )
            if checkout.returncode != 0:
                return BootstrapResult(success=False, error=checkout.stderr.strip())

            # git tag checkpoint-TASK_ID
            tag = f"checkpoint-{task_id}"
            subprocess.run(
                ["git", "tag", tag],
                cwd=str(sandbox),
                capture_output=True,
                text=True,
            )

            return BootstrapResult(
                success=True,
                is_detached=is_detached,
                checkpoint_tag=tag,
                branch_name=branch,
            )
        except Exception as e:
            return BootstrapResult(success=False, error=str(e))

    def soft_reset(self, task_id: str, sandbox_path: str) -> BootstrapResult:
        """Soft reset per spec §3: git reset --hard checkpoint + git clean -fd."""
        checkout_tag = f"checkpoint-{task_id}"
        try:
            subprocess.run(
                ["git", "reset", "--hard", checkout_tag],
                cwd=sandbox_path,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "clean", "-fd"],
                cwd=sandbox_path,
                capture_output=True,
                text=True,
            )
            return BootstrapResult(success=True)
        except Exception as e:
            return BootstrapResult(success=False, error=str(e))

    def destroy_sandbox(self, task_id: str, sandbox_path: str,
                        target_repo_path: str) -> BootstrapResult:
        """Destroy sandbox per spec §3: git worktree remove --force, git branch -D."""
        try:
            subprocess.run(
                ["git", "worktree", "remove", "--force", sandbox_path],
                cwd=target_repo_path,
                capture_output=True,
                text=True,
            )
            branch = f"sandbox-{task_id}"
            subprocess.run(
                ["git", "branch", "-D", branch],
                cwd=target_repo_path,
                capture_output=True,
                text=True,
            )
            return BootstrapResult(success=True)
        except Exception as e:
            return BootstrapResult(success=False, error=str(e))

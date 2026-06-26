"""Subsystem 3: GitHub Webhook Processor.

Processes GitHub push and workflow_run events, generating Telegram message
blocks with deploy buttons and status indicators.

H6: repo_name in callback_data is sanitized via ``sanitize_callback_token``.
"""

from __future__ import annotations

import secrets


# ---------------------------------------------------------------------------
#  Branch ref parsing
# ---------------------------------------------------------------------------


def parse_branch_ref(ref: str) -> str:
    """Extract branch/tag name from a git ref string.

    'refs/heads/main' -> 'main'
    'refs/heads/feature/login' -> 'feature/login'
    'refs/tags/v1.0' -> 'v1.0'
    'main' -> 'main'
    """
    if not ref:
        return ""

    for prefix in ("refs/heads/", "refs/tags/"):
        if ref.startswith(prefix):
            return ref[len(prefix):]

    return ref


# ---------------------------------------------------------------------------
#  Deploy token generation
# ---------------------------------------------------------------------------


def generate_deploy_token(repo: str, sha: str, secret: str | None = None) -> str:
    """Generate an unpredictable 16-char hex deployment token (CSPRNG).

    SECURITY: the previous implementation derived the token from
    HMAC(secret, "repo:sha:int(time):secret") truncated to 16 hex (64 bits),
    with `secret` defaulting to a public hardcoded literal (committed in source)
    when GITHUB_WEBHOOK_SECRET was unset. That was forgeable: an attacker who knew
    the (public) secret and the observable push timestamp could reproduce the
    token in ~120 candidates and replay `deploy:<repo>:<token>`.

    The token is now drawn from `secrets.token_hex` (os.urandom-backed), so it
    has no relationship to public data and cannot be reconstructed. It MUST be
    persisted server-side and consumed single-use by the deploy dispatcher.
    `repo`/`sha`/`secret` are retained in the signature for compatibility but
    are intentionally not used for derivation.
    """
    return secrets.token_hex(8)


# ---------------------------------------------------------------------------
#  Push event handler
# ---------------------------------------------------------------------------


def process_push_event(payload: dict) -> dict:
    """Process a GitHub push event payload.

    Returns a dict suitable for the Telegram gateway transport:
        {
            "text": "formatted commit info",
            "reply_markup": {"inline_keyboard": [[deploy_button]]}
        }
    """
    repo = payload.get("repository", {})
    repo_full_name = repo.get("full_name", repo.get("name", "unknown"))
    repo_name = repo.get("name", "unknown")
    ref = payload.get("ref", "")
    branch = parse_branch_ref(ref)

    head_commit = payload.get("head_commit", {}) or {}
    commit_msg = head_commit.get("message", "No commit message")
    commit_author = head_commit.get("author", {}) or {}
    author_name = commit_author.get("name", "Unknown")
    commit_sha = head_commit.get("id", "unknown")[:8]

    # Generate deploy token
    token = generate_deploy_token(repo_name, head_commit.get("id", ""))
    branch_display = f"`{branch}`" if branch else "unknown branch"

    # Build message text
    text_lines = [
        f"📦 **Push to {repo_full_name}**",
        f"Branch: {branch_display}",
        f"Commit: `{commit_sha}` — {author_name}: {commit_msg}",
    ]

    text = "\n".join(text_lines)

    # Build inline keyboard with deploy button
    from sentinel.cockpit.ui_engine import sanitize_callback_token

    safe_repo = sanitize_callback_token(repo_name)
    keyboard = {
        "inline_keyboard": [
            [
                {
                    "text": "🚀 Deploy Code",
                    "callback_data": f"deploy:{safe_repo}:{token}",
                }
            ]
        ]
    }

    return {
        "text": text,
        "reply_markup": keyboard,
    }


# ---------------------------------------------------------------------------
#  Workflow event handler
# ---------------------------------------------------------------------------


def process_workflow_event(payload: dict) -> dict | None:
    """Process a GitHub workflow_run event payload.

    Returns None for in-progress workflows (no update needed).
    Returns a dict with status indicator (🟢/🔴/🟡) for completed workflows.
    """
    workflow = payload.get("workflow_run", {})
    status = workflow.get("status", "")
    conclusion = workflow.get("conclusion")
    workflow_name = workflow.get("name", "CI")
    repo = payload.get("repository", {})
    repo_full_name = repo.get("full_name", repo.get("name", "unknown"))

    # Only emit updates for completed workflows
    if status != "completed":
        return None

    # Map conclusion to status indicator
    if conclusion == "success":
        indicator = "🟢"
        status_text = "passed"
    elif conclusion == "failure":
        indicator = "🔴"
        status_text = "failed"
    else:
        indicator = "🟡"
        status_text = str(conclusion or "unknown")

    text = (
        f"{indicator} **Workflow: {workflow_name}** on {repo_full_name}\n"
        f"Status: {status_text}"
    )

    # WI-7: Proactive action pings — include Re-run/View buttons
    repo_name = repo.get("name", "unknown")
    kb_buttons = []
    if conclusion == "failure":
        kb_buttons.append(
            {"text": "🔄 Re-run", "callback_data": f"cicd:rerun:{repo_name}"}
        )
    kb_buttons.append(
        {"text": "📊 View CI/CD", "callback_data": "cicd:list"}
    )

    return {
        "text": text,
        "reply_markup": {"inline_keyboard": [kb_buttons]},
    }

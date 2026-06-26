#!/usr/bin/env python3
"""Bridge: cockpit → full hermes agent (memory, tools, verification, soul).

Runs inside the hermes venv (Python 3.11). Takes prompt as argv, prints
response to stdout. Errors go to stderr (not swallowed).

Usage:
    ~/.hermes/hermes-agent/venv/bin/python bridge.py "prompt"
"""

import os
import sys
import time


def main() -> int:
    if len(sys.argv) < 2:
        prompt = sys.stdin.read().strip()
    else:
        prompt = " ".join(sys.argv[1:]).strip()

    if not prompt:
        print("ERROR: no prompt", file=sys.stderr)
        return 1

    hermes_root = os.path.expanduser("~/.hermes")
    os.chdir(hermes_root)

    # Load .env
    env_path = os.path.join(hermes_root, ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    os.environ["HERMES_YOLO_MODE"] = "1"
    os.environ["HERMES_ACCEPT_HOOKS"] = "1"

    # ----- Step 1: Config -----
    from hermes_cli.config import load_config
    cfg = load_config()

    # ----- Step 2: Model resolution -----
    model_cfg = cfg.get("model") or {}
    cfg_model = model_cfg.get("default") if isinstance(model_cfg, dict) else model_cfg
    effective_model = cfg_model or "deepseek-v4-pro"

    from hermes_cli.models import detect_provider_for_model
    from hermes_cli.runtime_provider import resolve_runtime_provider

    cfg_provider = model_cfg.get("provider", "") if isinstance(model_cfg, dict) else ""
    detected = detect_provider_for_model(effective_model, cfg_provider or "auto")
    if detected:
        effective_provider, effective_model = detected
    else:
        effective_provider = cfg_provider or "deepseek"

    runtime = resolve_runtime_provider(
        requested=effective_provider,
        target_model=effective_model or None,
    )

    # ----- Step 3: Toolsets -----
    from hermes_cli.tools_config import _get_platform_tools
    toolsets = sorted(_get_platform_tools(cfg, "cli"))

    # ----- Step 4: Create Agent (NO stderr redirect!) -----
    from run_agent import AIAgent

    agent = AIAgent(
        api_key=runtime.get("api_key"),
        base_url=runtime.get("base_url"),
        provider=runtime.get("provider"),
        api_mode=runtime.get("api_mode"),
        model=effective_model,
        enabled_toolsets=toolsets,
        quiet_mode=True,
        platform="cli",
        load_soul_identity=True,   # ← THE SOUL
    )

    # ----- Step 5: Run -----
    response = agent.chat(prompt)
    if response:
        print(response)
        return 0
    else:
        print("(no response)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

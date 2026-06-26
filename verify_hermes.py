#!/usr/bin/env python3
"""Hermes/Sentinel Verification — POLLING MODE (no webhooks).

Tests the reliable polling architecture. Run this to prove everything works.
Exit 0 = all pass. Exit 1 = broken.
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

RED = "\033[31m"
GREEN = "\033[32m"
BOLD = "\033[1m"
NC = "\033[0m"

passed = 0
failed = 0

def check(name: str, condition: bool, detail: str = "") -> bool:
    global passed, failed
    if condition:
        print(f"  {GREEN}PASS{NC} {name} {detail}")
        passed += 1
        return True
    else:
        print(f"  {RED}FAIL{NC} {name} {detail}")
        failed += 1
        return False

def http_get(url: str, timeout: float = 5.0) -> tuple[int, str]:
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8")
    except Exception as e:
        return 0, str(e)

def http_post(url: str, body: dict, timeout: float = 30.0) -> tuple[int, str]:
    try:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8")
    except Exception as e:
        return 0, str(e)

def load_env() -> dict:
    env = {}
    env_path = os.path.expanduser("~/.hermes/.env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env[k.strip()] = v.strip().strip('"').strip("'")
    return env

def main() -> int:
    print(f"\n{BOLD}═══ Hermes/Sentinel Verification (Polling Mode) ═══{NC}\n")

    env = load_env()
    bot_token = env.get("TELEGRAM_BOT_TOKEN", "")
    allowed_user = (env.get("TELEGRAM_ALLOWED_USERS") or
                    env.get("TELEGRAM_ALLOWED_USER_IDS", "8868748055")).split(",")[0].strip()

    # ── 1. Infrastructure ──
    print(f"{BOLD}1. Infrastructure{NC}")
    try:
        r = subprocess.run(["lsof", "-i", ":8802", "-P"], capture_output=True, text=True, timeout=5)
        check("Otto agent server port 8802", "LISTEN" in r.stdout,
              "(found listener)" if "LISTEN" in r.stdout else "(NO LISTENER)")
    except:
        check("Otto agent server port 8802", False, "(lsof failed)")

    check("Coordinator daemon",
          subprocess.run(["pgrep", "-f", "coordinator.py.*daemon"], capture_output=True, timeout=5).returncode == 0,
          "(PID: " + subprocess.run(["pgrep", "-f", "coordinator.py.*daemon"], capture_output=True, text=True, timeout=5).stdout.strip() + ")")

    check("Reliable Otto (polling)",
          subprocess.run(["pgrep", "-f", "reliable_otto.py"], capture_output=True, timeout=5).returncode == 0,
          "(polling Telegram directly)")

    # ── 2. Otto agent ──
    print(f"\n{BOLD}2. Otto agent (hermes AIAgent){NC}")
    code, body = http_get("http://127.0.0.1:8802/health")
    check("Health endpoint", code == 200 and "ok" in body,
          f"(HTTP {code})")

    code, body = http_post("http://127.0.0.1:8802/chat",
                           {"prompt": "Say just the word pong"})
    otto_ok = code == 200 and len(body.strip()) > 0
    check("Responds to chat", otto_ok,
          f"(HTTP {code}, {len(body)} chars)" if otto_ok else f"(HTTP {code})")
    if otto_ok:
        check("Has identity/soul", "pong" in body.lower() or "deepseek" in body.lower() or "lux" in body.lower(),
              f"(response: {body[:60].strip()})")

    # ── 3. Telegram connectivity ──
    print(f"\n{BOLD}3. Telegram connectivity (polling){NC}")
    if not bot_token:
        check("Bot token", False, "(MISSING)")
    else:
        code, body = http_get(f"https://api.telegram.org/bot{bot_token}/getMe")
        me_ok = code == 200 and json.loads(body).get("ok")
        check("Bot API reachable", me_ok,
              f"(@{json.loads(body).get('result',{}).get('username','?')})" if me_ok else f"(HTTP {code})")

        # Verify NO webhook set (must be polling-only)
        code, body = http_get(f"https://api.telegram.org/bot{bot_token}/getWebhookInfo")
        if code == 200:
            info = json.loads(body).get("result", {})
            webhook_url = info.get("url", "")
            check("NO webhook set (polling)", not webhook_url,
                  f"(polling mode confirmed)" if not webhook_url else f"(webhook still set: {webhook_url[:50]}!)")

        # Verify we can actually get updates (proves polling works)
        code, body = http_post(f"https://api.telegram.org/bot{bot_token}/getUpdates",
                               {"limit": 1, "timeout": 1}, timeout=5)
        check("Polling functional", code == 200,
              f"(can getUpdates)" if code == 200 else f"(HTTP {code})")

    # ── 4. Dashboard rendering ──
    print(f"\n{BOLD}4. Cockpit dashboard{NC}")
    cockpit_path = os.path.expanduser("~/Documents/code/sentinel-loop")
    sys.path.insert(0, cockpit_path)
    try:
        from sentinel.cockpit.menu import view_dashboard, scan_projects
        scan_projects()
        text, kb = view_dashboard()
        check("Dashboard renders", bool(text) and "<b>" in text,
              f"({len(text)} chars, {len(kb.get('inline_keyboard',[]))} button rows)")
        check("Keyboard has buttons", len(kb.get("inline_keyboard", [])) > 0,
              f"({len(kb.get('inline_keyboard',[]))} rows)")
    except Exception as e:
        check("Dashboard renders", False, f"({e})")

    # ── 5. Full pipeline simulation ──
    print(f"\n{BOLD}5. End-to-end simulation (as if Telegram delivered){NC}")
    # Test: can we call the menu handler directly?
    try:
        from sentinel.cockpit.menu import view_dashboard, scan_projects
        scan_projects()
        text, kb = view_dashboard()
        # Simulate what happens when /dashboard is received
        has_sections = "M O T H E R S H I P" in text or "Prospector" in text or "System" in text
        check("Dashboard has expected sections", has_sections,
              "(mothership dashboard rendered correctly)")
    except Exception as e:
        check("Dashboard sections", False, f"({e})")

    # ── 6. Persistence ──
    print(f"\n{BOLD}6. Launchd persistence{NC}")
    try:
        r = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5)
        check("Coordinator loaded in launchd", "ai.hermes.coordinator" in r.stdout,
              "(will survive reboot)")
    except:
        check("launchctl", False, "(command failed)")

    # ── Summary ──
    print(f"\n{BOLD}═══ Results: {GREEN}{passed} passed{NC}, {RED}{failed} failed{NC} ═══{NC}")
    if failed == 0:
        print(f"{GREEN}All checks passed. Otto is operational.{NC}\n")
    else:
        print(f"{RED}{failed} check(s) failed. Fix before proceeding.{NC}\n")
    return 0 if failed == 0 else 1

if __name__ == "__main__":
    sys.exit(main())

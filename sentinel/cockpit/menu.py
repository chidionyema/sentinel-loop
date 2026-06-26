"""Mothership v4 — premium mission control. Designed for impact."""

from __future__ import annotations

import json, os, re, subprocess
from pathlib import Path
from typing import Any, Tuple


# ═══════════════════ helpers ═══════════════════════════════════════

def _p(p: str) -> Path: return Path(p).expanduser().resolve()
def _t() -> str: return os.environ.get("TELEGRAM_BOT_TOKEN", "")

def _api(method: str, body: dict) -> bool:
    import urllib.request as _ur
    t = _t()
    if not t: return False
    try:
        url = f"https://api.telegram.org/bot{t}/{method}"
        req = _ur.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        return json.loads(_ur.urlopen(req, timeout=10).read()).get("ok", False)
    except: return False

def answer(cbq_id: str) -> bool:
    return _api("answerCallbackQuery", {"callback_query_id": cbq_id})

def send(chat_id: str, text: str, kb: dict | None = None) -> bool:
    body: dict[str, Any] = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if kb: body["reply_markup"] = kb
    return _api("sendMessage", body)

# HTML helpers
def H(s: str) -> str: return f"<b>{s}</b>"
def C(s: str) -> str: return f"<code>{s}</code>"
def D() -> str: return "─" * 28  # divider
def S(icon: str, label: str, value: str) -> str: return f"{icon} {H(label)}  {C(value)}"
def R(label: str, *values: str) -> str: return f"<b>{label}</b>  {' · '.join(values)}"

# Data
CFG = {
    "hb": "~/Documents/code/prospector/store/scheduler/heartbeat.json",
    "diag": "~/Documents/code/prospector/store/scheduler/DIAGNOSTICS_LATEST.txt",
    "alerts": "~/Documents/code/prospector/store/scheduler/ALERT.txt",
    "log": "~/Documents/code/prospector/store/scheduler/launchd.err.log",
    "int": 7200,
    "label": "com.prospector.scheduler",
}

def _rjson(p: str) -> dict:
    try: return json.loads(_p(p).read_text()) if _p(p).exists() else {}
    except: return {}

def _rtxt(p: str) -> str:
    try: return _p(p).read_text() if _p(p).exists() else ""
    except: return ""

def _age(ts: str) -> str:
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        s = (datetime.now(timezone.utc) - dt).total_seconds()
        if s < 60: return f"{int(s)}s ago"
        if s < 3600: return f"{int(s/60)}m ago"
        if s < 86400: return f"{int(s/3600)}h ago"
        return f"{int(s/86400)}d ago"
    except: return ts[:16]


# ═══════════════════════════════════════════════════════════════════
#  WI-1 — Persistent nav bar (ReplyKeyboardMarkup)
# ═══════════════════════════════════════════════════════════════════

# Map nav button labels → handler actions for free-text dispatch
_NAV_BUTTON_MAP: dict[str, str] = {
    "🏠 Home":       "nv:dash:",
    "🛰 Projects":   "nv:projects:",
    "🏛 Estate":     "estate:refresh",
    "✅ Tasks":      "task:list",
    "🚀 Deploy":     "nv:deploy:",
    "🔄 CI/CD":      "cicd:list",
    "➕ Request":    "nv:request:",
}

# Stateful intake: when user taps ➕ Request, their NEXT text message
# is captured as a feature request instead of being relayed to Otto.
_PENDING_INTAKE: dict[str, bool] = {}
_DEPLOY_TOKENS: dict[str, str] = {}


def _reply_keyboard_markup() -> dict:
    """WI-1: Return a Telegram ReplyKeyboardMarkup for persistent nav bar.

    This is attached to /start and /dashboard responses. Telegram persists
    the keyboard across subsequent messages until replaced — no need to
    attach it to every single message.
    """
    return {
        "keyboard": [
            [{"text": "🏠 Home"}, {"text": "🛰 Projects"}, {"text": "🏛 Estate"}],
            [{"text": "✅ Tasks"}, {"text": "🚀 Deploy"}, {"text": "🔄 CI/CD"}],
            [{"text": "➕ Request"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def _home_kb() -> dict:
    """WI-2: Return a minimal inline keyboard with a Home button.

    Every leaf screen MUST append or include this so the user
    can always get back to the dashboard.
    """
    return {"inline_keyboard": [
        [{"text": "🏠 Home", "callback_data": "nv:dash:"}],
    ]}

def _ps(label: str) -> dict:
    try:
        r = subprocess.run(["bash", "-c",
            f"pid=$(launchctl list 2>/dev/null | grep '{label}' | awk '{{print $1}}' | head -1); "
            f"if [ -n \"$pid\" ] && [ \"$pid\" != \"-\" ]; then "
            f"echo \"pid=$pid\"; ps -p $pid -o lstart=,pcpu=,pmem=,etime= --no-headers 2>/dev/null; "
            f"else echo dead; fi"],
            capture_output=True, text=True, timeout=5)
        out = r.stdout.strip()
        if out == "dead": return {}
        lines = out.splitlines()
        d = {}
        for line in lines:
            if line.startswith("pid="): d["pid"] = line[4:]
            else:
                p = line.split()
                if len(p) >= 5:
                    d["start"] = f"{p[0]} {p[1]} {p[2]}"; d["cpu"] = p[3]; d["mem"] = p[4]; d["up"] = p[5]
        return d
    except: return {}

def _sys() -> dict:
    s = {}
    try:
        r = subprocess.run(["uptime"], capture_output=True, text=True)
        u = r.stdout.strip()
        s["up"] = u.split("up ")[-1].split(",")[0].strip() if "up " in u else "?"
        s["load"] = u.split("load averages:")[-1].strip() if "load" in u else "?"
    except: s["up"] = s["load"] = "?"
    try:
        r = subprocess.run(["df", "-h", "/"], capture_output=True, text=True)
        p = r.stdout.splitlines()[-1].split()
        s["disk"] = f"{p[4]}" if len(p) >= 5 else "?"; s["disk_total"] = p[1] if len(p) >= 2 else "?"
    except: s["disk"] = s["disk_total"] = "?"
    try:
        r = subprocess.run(["ps", "aux"], capture_output=True, text=True)
        s["procs"] = str(len(r.stdout.splitlines()))
    except: s["procs"] = "?"
    return s

def scan_projects() -> list[str]:
    """Public alias for _projects."""
    return _projects()


def _projects() -> list[str]:
    r = _p("~/Documents/code")
    return sorted(d.name for d in r.iterdir() if d.is_dir() and (d/".git").exists() and not d.name.startswith(".") and "worktree" not in d.name)


# ═══════════════════════════════════════════════════════════════════
#  VIEWS
# ═══════════════════════════════════════════════════════════════════

def view_dashboard() -> tuple[str, dict]:
    s = _sys()
    hb = _rjson(CFG["hb"])
    alerts = _rtxt(CFG["alerts"]).strip()
    projects = _projects()

    # D1: load risk_class from ~/.hermes/projects.json for fenced markers
    risk_map = {}
    try:
        proj_cfg = _rjson("~/.hermes/projects.json")
        for p in proj_cfg.get("projects", []):
            risk_map[p.get("key", "")] = p.get("risk_class", "low")
    except Exception:
        pass

    lines = [
        f"<b>◆  M O T H E R S H I P</b>",
        "",
        f"  <b>System</b>    {C(s['up'])} up  ·  load {C(s['load'])}  ·  disk {C(s['disk'])} of {C(s['disk_total'])}",
        f"            {C(s['procs'])} processes",
        "",
        D(),
    ]

    if hb:
        phase = hb.get("phase","?")
        batch = hb.get("batch_size","?")
        pid = hb.get("pid","?")
        ts = _age(hb.get("ts",""))
        cyc = hb.get("cycles","?")
        icon = {"generating":"⚡","idle":"💤","sleeping":"💤","evaluating":"🔍"}.get(phase,"○")
        lines.append(f"  {icon} <b>Prospector</b>    {phase}  ·  batch {C(str(batch))}  ·  pid {C(str(pid))}")
        lines.append(f"            cycle {C(str(cyc))}  ·  {C(ts)}")

    if alerts:
        for line in alerts.splitlines()[:2]:
            clean = line.split("] ",1)[-1] if "] " in line else line
            lines.append(f"  ⚠  {clean[:90]}")

    lines.append("")
    lines.append(D())
    lines.append(f"  <b>Projects</b>    {C(str(len(projects)))} repos  ·  tap to inspect")

    # D1: risk summary
    money_projs = [k for k, v in risk_map.items() if v == "money"]
    identity_projs = [k for k, v in risk_map.items() if v == "identity"]
    if money_projs or identity_projs:
        parts = []
        if money_projs:
            parts.append(f"🔒 money: {', '.join(money_projs)}")
        if identity_projs:
            parts.append(f"🔒 identity: {', '.join(identity_projs)}")
        lines.append(f"  {'  ·  '.join(parts)}")

    lines.append("")

    # Keyboard: 3 columns with risk markers
    kb = {"inline_keyboard": []}
    for i in range(0, len(projects), 3):
        row = []
        for j in range(3):
            if i+j < len(projects):
                n = projects[i+j][:18]
                # D1: add risk markers for money/identity projects
                rc = risk_map.get(projects[i+j], "low")
                marker = ""
                if rc == "money":
                    marker = " 🔒"
                elif rc == "identity":
                    marker = " 🔒"
                row.append({"text": f"{n}{marker}", "callback_data": f"nv:{projects[i+j]}:"})
        kb["inline_keyboard"].append(row)
    kb["inline_keyboard"].append([
        {"text": "📊 Refresh", "callback_data": "nv:dash:"},
        {"text": "🔍 Rescan", "callback_data": "ac:rescan:"},
    ])
    return "\n".join(lines), kb


def view_project(name: str) -> tuple[str, dict]:
    proot = _p(f"~/Documents/code/{name}")
    is_git = proot.is_dir() and (proot/".git").exists()
    is_prospector = (name == "prospector")

    lines = [f"<b>📁 {name}</b>", ""]

    # Git section
    if is_git:
        try:
            r = subprocess.run(["git","-C",str(proot),"status","--short"], capture_output=True, text=True, timeout=5)
            br = subprocess.run(["git","-C",str(proot),"branch","--show-current"], capture_output=True, text=True, timeout=5)
            branch = br.stdout.strip()
            lines.append(f"  <b>branch</b>  {C(branch)}")
            if r.stdout.strip():
                for line in r.stdout.splitlines()[:6]:
                    st = line[:2].strip()
                    fn = line[3:][:38]
                    s = {"M":"~","??":"+","A":"+","D":"−"}.get(st," ")
                    lines.append(f"  {s} {fn}")
            else:
                lines.append(f"  ✓ clean")
        except:
            lines.append(f"  error reading git")
        lines.append("")

    # Daemon section for prospector
    if is_prospector:
        ps = _ps(CFG["label"])
        hb = _rjson(CFG["hb"])
        if ps:
            lines.append(f"  ● <b>running</b>  pid {C(ps.get('pid','?'))}  cpu {C(ps.get('cpu','?'))}%  mem {C(ps.get('mem','?'))}%  up {C(ps.get('up','?'))}")
        elif hb:
            lines.append(f"  ○ <b>{hb.get('phase','?')}</b>  batch {C(str(hb.get('batch_size','?')))}  {C(_age(hb.get('ts','')))}")
        lines.append("")

    # Buttons
    kb = []
    if is_git:
        kb.append([{"text": "📊 Status", "callback_data": f"gs:{name}:"}, {"text": "⬇ Pull", "callback_data": f"gp:{name}:main"}, {"text": "📜 Log", "callback_data": f"gl:{name}:"}])
    if is_prospector:
        kb.append([{"text": "📋 Dashboard", "callback_data": "dx:0"}, {"text": "💀 Killed", "callback_data": "dk:0"}])
        kb.append([{"text": "🔍 Search", "callback_data": "dz:0"}, {"text": "📜 Logs", "callback_data": "dl:0:0"}])
        kb.append([{"text": "▶️ Run 3", "callback_data": "dr:3"}, {"text": "♥ Beat", "callback_data": "dh:0"}])
    kb.append([{"text": "← Back", "callback_data": "nv:back:"}])
    return "\n".join(lines), {"inline_keyboard": kb}


def view_daemon() -> tuple[str, dict]:
    ps = _ps(CFG["label"])
    hb = _rjson(CFG["hb"])
    diag = _rtxt(CFG["diag"])
    alerts = _rtxt(CFG["alerts"]).strip()
    interval = CFG["int"]

    lines = [H("⚡ Prospector Scheduler"), ""]

    # STATUS
    lines.append(H("─── Status"))
    if ps:
        lines.append(f"  ● RUNNING  pid {C(ps.get('pid','?'))}")
        lines.append(f"  cpu {C(ps.get('cpu','?'))}%  mem {C(ps.get('mem','?'))}%  up {C(ps.get('up','?'))}")
        if ps.get("start"): lines.append(f"  since {C(ps['start'])}")
    else:
        lines.append("  ○ STOPPED")

    if hb:
        lines.append(f"  ♥ {C(_age(hb.get('ts','')))}  ·  {C(hb.get('phase','?'))}  ·  cycle {C(str(hb.get('cycles','?')))}  ·  every {interval//3600}h")
    lines.append("")

    # BATCH
    if diag:
        lines.append(H("─── Batch"))
        gm = re.search(r"generated=(\d+)", diag); nm = re.search(r"novelty_selected=(\d+)", diag)
        vm = re.search(r"vetted=(\d+)", diag); pm = re.search(r"PASS\s+(\d+)", diag); km = re.search(r"KILL\s+(\d+)", diag)
        if gm:
            lines.append(f"  {C(gm.group(1))} gen  →  {C(nm.group(1) if nm else '?')} novel  →  {C(vm.group(1) if vm else '?')} vetted")
            p,k = (pm.group(1) if pm else "?"), (km.group(1) if km else "?")
            lines.append(f"  {H(f'PASS {p}')}  ·  KILL {C(k)}  ·  survival {C(f'{int(p)*100//max(int(k),1)}%' if p!='?' else '?')}")

        gates = re.findall(r"(\w+)=(\d+)", diag)
        gn = {"min_composite","incumbency","adversarial_decisive","value_durability","pain_reality","payer_solvency","distribution"}
        gs = [f"{g}:{c}" for g,c in gates if g in gn]
        if gs: lines.append(f"  gates  {' · '.join(gs[:7])}")

        unv = re.search(r"unverifiable_pct[:\s=]+([\d.]+)", diag)
        web = re.search(r"web_calls[:\s=]+(\d+)", diag)
        if unv:
            pct = float(unv.group(1))
            tag = "🔴" if pct > 70 else "🟡" if pct > 40 else "🟢"
            lines.append(f"  {tag} {pct}% unverifiable  ·  {C(web.group(1) if web else '?')} web calls")
        lines.append("")

    # ALERTS
    if alerts:
        lines.append(H("─── Alerts"))
        for line in alerts.splitlines()[:2]:
            clean = line.split("] ",1)[-1] if "] " in line else line
            lines.append(f"  ⚠ {clean[:100]}")
        lines.append("")

    kb = {"inline_keyboard": [
        [{"text": "📜 Logs", "callback_data": "dl:0:0"}, {"text": "🔄 Refresh", "callback_data": "dx:0"}],
        [{"text": "🏠 Home", "callback_data": "nv:dash:"}, {"text": "← Back", "callback_data": "nv:prospector:"}],
    ]}
    return "\n".join(lines), kb


def view_killed() -> tuple[str, dict]:
    import glob as _g
    ddir = _p("~/Documents/code/prospector/store/dossiers")
    files = sorted(_g.glob(str(ddir/"*.kill.json")), key=os.path.getmtime, reverse=True)[:5]
    if not files: return "No killed dossiers.", _home_kb()

    lines = [H("💀 Recently Killed"), ""]
    for i,f in enumerate(files,1):
        try:
            d = json.loads(Path(f).read_text())
            c = d.get("candidate",{})
            title = c.get("title","?")[:55]
            gate = d.get("gate_fired","?")
            score = d.get("dense_reward",0)
            reason = d.get("reason","")[:90]
            lines.append(f"<b>{i}.</b> {title}")
            lines.append(f"    {H(gate)}  score {C(f'{score:.2f}')}")
            lines.append(f"    {reason}")
            lines.append("")
        except: pass

    return "\n".join(lines), {"inline_keyboard": [
        [{"text": "🔍 Investigate", "callback_data": "di:0"}],
        [{"text": "🏠 Home", "callback_data": "nv:dash:"}, {"text": "← Back", "callback_data": "nv:prospector:"}],
    ]}


def view_investigate() -> Tuple[str, dict | None]:
    diag = _rtxt(CFG["diag"])
    if not diag: return "No diagnostics.", _home_kb()
    lines = [H("🔍 Generator Investigation"), ""]
    gm = re.search(r"generated=(\d+)", diag); pm = re.search(r"PASS\s+(\d+)", diag); km = re.search(r"KILL\s+(\d+)", diag)
    unv = re.search(r"unverifiable_pct[:\s=]+([\d.]+)", diag); web = re.search(r"web_calls[:\s=]+(\d+)", diag)
    if gm: lines.append(f"  funnel  {C(gm.group(1))} gen → {C(pm.group(1) if pm else '?')} pass / {C(km.group(1) if km else '?')} kill")
    if unv: lines.append(f"  verify  {C(unv.group(1)+'%')} unverifiable  ·  {C(web.group(1) if web else '?')} web calls")
    lines.append("")
    gates = re.findall(r"(\w+)=(\d+)", diag)
    for g,c in gates:
        if g in ("min_composite","incumbency","adversarial_decisive","value_durability"):
            lines.append(f"  {g}  {C(c)}")
    lines.append("")
    if unv and float(unv.group(1)) > 70 and web and int(web.group(1)) == 0:
        lines.append("🔴 <b>SEARCH BROKEN</b> — zero web calls with high unverifiability")
    return "\n".join(lines), {"inline_keyboard": [
        [{"text": "💀 Killed", "callback_data": "dk:0"}, {"text": "📋 Dashboard", "callback_data": "dx:0"}],
        [{"text": "🏠 Home", "callback_data": "nv:dash:"}, {"text": "← Back", "callback_data": "nv:prospector:"}],
    ]}


def view_search() -> tuple[str, dict]:
    lines = [H("🔍 Search Health"), ""]
    key = os.environ.get("EXA_API_KEY","")
    if not key: lines.append("❌ EXA_API_KEY not in environment")
    else:
        lines.append(f"✅ key {C(key[:8]+'...')}")
        try:
            import sys; sys.path.insert(0, os.path.expanduser("~/Documents/code/prospector"))
            from prospector.retrieval import ExaSearchProvider
            results = ExaSearchProvider().search("test", k=2)
            if results:
                lines.append(f"✅ live test: {C(str(len(results)))} results")
                for r in results[:2]: lines.append(f"   {C(r.url[:55])}")
            else: lines.append("⚠ zero results")
        except Exception as e: lines.append(f"❌ {C(str(e)[:80])}")
    brave = os.environ.get("BRAVE_API_KEY","")
    lines.append(f"Brave: {'✅ configured' if brave else '❌ not configured'}")
    return "\n".join(lines), _home_kb()


def view_heartbeat() -> tuple[str, dict]:
    hb = _rjson(CFG["hb"])
    if not hb: return "No heartbeat", _home_kb()
    return "\n".join([H("♥ Heartbeat"), "",
        f"  time   {C(hb.get('ts','')[:19].replace('T',' '))}",
        f"  phase  {C(hb.get('phase','?'))}",
        f"  pid    {C(str(hb.get('pid','?')))}",
        f"  cycles {C(str(hb.get('cycles','?')))}",
        f"  batch  {C(str(hb.get('batch_size','?')))}",
    ]), _home_kb()


def view_schedule() -> tuple[str, dict]:
    hb = _rjson(CFG["hb"])
    last = _age(hb.get("ts","")) if hb else "?"
    ival = CFG["int"]
    return "\n".join([H("⏱ Schedule"), "",
        f"  interval  {C(f'{ival}s ({ival//3600}h)')}",
        f"  last run  {C(last)}",
    ]), _home_kb()


def view_alerts() -> tuple[str, dict]:
    a = _rtxt(CFG["alerts"]).strip()
    if not a: return H("🚨 Alerts") + "\n\nNo active alerts", _home_kb()
    return H("🚨 Alerts") + "\n\n" + "".join(f"  ⚠ {line.split('] ',1)[-1][:100]}\n" for line in a.splitlines()[:3]), _home_kb()


def view_projects() -> tuple[str, dict]:
    """Project picker — text header + 3-column inline grid of all repos.

    Shown when the user taps the 📁 Projects reply-keyboard button. Inline
    buttons survive here (the persistent reply keyboard at the bottom of the
    chat is independent of the inline buttons on this message).
    """
    projects = _projects()
    if not projects:
        return "No projects discovered.", {"inline_keyboard": []}

    lines = [f"<b>📁 Projects</b>  {C(str(len(projects)))} repos  ·  tap to inspect", ""]
    kb_rows: list[list[dict]] = []
    for i in range(0, len(projects), 3):
        row: list[dict] = []
        for j in range(3):
            if i + j < len(projects):
                name = projects[i + j][:18]
                row.append({"text": name, "callback_data": f"nv:{projects[i + j]}:"})
        kb_rows.append(row)
    kb_rows.append([{"text": "🏠 Home", "callback_data": "nv:dash:"}])
    return "\n".join(lines), {"inline_keyboard": kb_rows}


def view_log(page: int = 0) -> tuple[str, dict]:
    p = _p(CFG["log"])
    if not p.exists(): return "No log file.", _bk()
    lines = p.read_text().splitlines()
    total = len(lines)
    per_page = 50
    start = max(0, total - (page+1)*per_page)
    end = min(total, start+per_page)
    chunk = lines[start:end]
    text = "\n".join(chunk) if chunk else "(empty)"
    kb = {"inline_keyboard": []}
    btns = []
    if end < total: btns.append({"text":"⏫ Newer","callback_data":f"dl:{page+1}:0"})
    if start > 0: btns.append({"text":"⏬ Older","callback_data":f"dl:{page-1}:0"})
    if btns: kb["inline_keyboard"].append(btns)
    kb["inline_keyboard"].append([
        {"text":"🔄 Refresh","callback_data":f"dl:{page}:0"},
        {"text":"🏠 Home","callback_data":"nv:dash:"},
        {"text":"← Back","callback_data":"nv:prospector:"},
    ])
    hdr = f"<b>📜 Log</b>  [{start+1}–{end} of {total}]"
    return f"{hdr}\n<pre>{text[:3500]}</pre>", kb


def trigger_gen(count: int) -> None:
    subprocess.Popen(
        [os.path.expanduser("~/Documents/code/prospector/.venv/bin/python"),
         "-m","prospector.scheduler.run_scheduled","--once",f"--candidates={count}","--config=config.yaml"],
        cwd=os.path.expanduser("~/Documents/code/prospector"),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _bk() -> dict:
    return {"inline_keyboard": [[{"text":"← Back","callback_data":"nv:prospector:"}]]}


# ═══════════════════════════════════════════════════════════════════
#  ROUTER
# ═══════════════════════════════════════════════════════════════════

async def handle_callback(data: str, chat_id: str, cbq_id: str) -> None:
    answer(cbq_id)  # always dismiss spinner immediately

    if data.startswith("nv:"):
        t = data[3:].rstrip(":")
        if t == "back" or t == "dash":
            text, kb = view_dashboard()
            send(chat_id, text, kb)
        else:
            text, kb = view_project(t)
            send(chat_id, text, kb)

    elif data.startswith("ac:"):
        if data[3:].rstrip(":") == "rescan":
            text, kb = view_dashboard()
            send(chat_id, text, kb)

    elif data.startswith("dx:"):
        text, kb = view_daemon()
        send(chat_id, text, kb)
    elif data.startswith("dh:"):
        text, kb = view_heartbeat()
        send(chat_id, text, kb)
    elif data.startswith("dg:"):
        text, kb = view_heartbeat()  # shortcut
        send(chat_id, text, kb)
    elif data.startswith("da:"):
        text, kb = view_alerts()
        send(chat_id, text, kb)
    elif data.startswith("ds:"):
        text, kb = view_schedule()
        send(chat_id, text, kb)
    elif data.startswith("dk:"):
        text, kb = view_killed()
        send(chat_id, text, kb)
    elif data.startswith("di:"):
        text, kb = view_investigate()
        if kb: send(chat_id, text, kb)
        else: send(chat_id, text)
    elif data.startswith("dz:"):
        text, _ = view_search()
        send(chat_id, text, {"inline_keyboard": [
            [{"text":"🔄 Retest","callback_data":"dz:0"},{"text":"▶️ Run 3","callback_data":"dr:3"}],
            [{"text":"🏠 Home","callback_data":"nv:dash:"},{"text":"← Back","callback_data":"nv:prospector:"}],
        ]})
    elif data.startswith("dl:"):
        parts = data.split(":")
        page = int(parts[1]) if len(parts)>1 and parts[1].lstrip("-").isdigit() else 0
        text, kb = view_log(page)
        send(chat_id, text, kb)
    elif data.startswith("dr:"):
        count = int(data.split(":")[1]) if len(data.split(":"))>1 else 3
        trigger_gen(count)
        send(chat_id, f"▶️ Started {count} candidates — check Dashboard for progress")

    # ── WI-3 — Deploy handler ──────────────────────────────────────
    elif data.startswith("deploy:"):
        parts = data.split(":", 2)
        if len(parts) < 3:
            send(chat_id, "⚠️ Invalid deploy action.")
            return
        repo = parts[1]
        token = parts[2] if len(parts) > 2 else ""

        risk = _risk_for(repo)

        if risk in ("money", "identity"):
            # FENCE: money/identity deploy → approval gate, Claude-only execution
            # Per spec §0.2: never execute deploy for money/identity from cockpit
            import sys as _sys, os as _os
            _SCRIPTS = _os.path.expanduser("~/.hermes/scripts")
            if _SCRIPTS not in _sys.path:
                _sys.path.insert(0, _SCRIPTS)
            import coordinator as C
            conn = C.connect()
            try:
                task_id = C.open_task(conn,
                    title=f"Deploy {repo} (🔒 {risk})",
                    body=f"Deploy requested via cockpit for {repo} (risk_class={risk}).\n"
                         f"Awaiting Claude approval per founder fence.",
                    kind="deploy_request")
                send(chat_id,
                     f"🔒 Deploy for `{repo}` ({risk}) requires Claude approval.\n"
                     f"Task `{task_id[:8]}` created — pending Claude review.\n"
                     f"This task CANNOT be approved from the cockpit.",
                     _home_kb())
            finally:
                conn.close()
        else:
            # Low-risk: two-step confirm
            send(chat_id,
                 f"🚀 Deploy `{repo}`?\n\n"
                 f"This will trigger the deployment pipeline. Proceed?",
                 {"inline_keyboard": [[
                     {"text": "✅ Confirm deploy", "callback_data": f"deploy_confirm:{repo}:{token}"},
                     {"text": "✗ Cancel", "callback_data": "nv:dash:"},
                 ]]})

    elif data.startswith("deploy_confirm:"):
        parts = data.split(":", 2)
        repo = parts[1] if len(parts) > 1 else "?"
        token = parts[2] if len(parts) > 2 else ""
        # Validate deploy token (prevent replay attacks)
        expected = _DEPLOY_TOKENS.pop(repo, None)
        if not expected or token != expected:
            send(chat_id, "⚠️ Deploy token invalid or expired. Request a new deploy.", _home_kb())
            return
        # Re-check risk at confirm time (belt-and-suspenders)
        if _risk_for(repo) in ("money", "identity"):
            send(chat_id, f"🔒 Deploy for `{repo}` blocked — requires Claude approval.", _home_kb())
            return
        import subprocess as _sp
        send(chat_id, f"🚀 Deploying `{repo}`…")
        try:
            if repo == "prospector":
                send(chat_id, f"✅ `{repo}` — CI-only project, no deploy target. Pipeline will run on push.")
            elif repo == "haworks-platform":
                # Trigger via gh workflow dispatch or direct
                proc = _sp.run(
                    ["gh", "workflow", "run", "deploy.yml", "-R", f"chidionyema/{repo}"],
                    capture_output=True, text=True, timeout=30,
                )
                ok = proc.returncode == 0
                send(chat_id,
                     f"{'✅' if ok else '⚠️'} Deploy triggered for `{repo}`"
                     f"{' — check CI/CD for status.' if ok else f' (rc={proc.returncode})'}")
            else:
                send(chat_id, f"⚠️ No deploy target configured for `{repo}`.")
        except Exception as e:
            send(chat_id, f"⚠️ Deploy failed: {e}")

    # ── WI-4 — CI/CD screen ─────────────────────────────────────────
    elif data.startswith("cicd:"):
        action = data.split(":", 1)[1] if ":" in data else "list"
        if action == "list" or action == "":
            _view_cicd(chat_id, send)
        elif action.startswith("rerun:"):
            repo = action.split(":", 1)[1] if ":" in action else ""
            risk_map = _load_risk_map()
            risk = risk_map.get(repo, "low")
            if risk in ("money", "identity"):
                send(chat_id,
                     f"🔒 Re-run CI for `{repo}` ({risk}) requires Claude approval.",
                     _home_kb())
            else:
                send(chat_id, f"🔄 Re-running CI for `{repo}`…")
                import subprocess as _sp
                try:
                    proc = _sp.run(
                        ["gh", "workflow", "run", "ci.yml", "-R", f"chidionyema/{repo}"],
                        capture_output=True, text=True, timeout=30,
                    )
                    ok = proc.returncode == 0
                    send(chat_id,
                         f"{'✅' if ok else '⚠️'} CI re-triggered for `{repo}`",
                         _home_kb())
                except Exception as e:
                    send(chat_id, f"⚠️ Re-run failed: {e}", _home_kb())

    # ── WI-6 — Unified project detail views ──────────────────────────
    elif data.startswith("nv:"):
        t = data[3:].rstrip(":")
        if t == "back" or t == "dash":
            text, kb = view_dashboard()
            send(chat_id, text, kb)
        elif t == "projects:":
            text, kb = view_projects()
            send(chat_id, text, kb)
        elif t == "deploy:" or t == "cicd:":
            # Redirected from nav buttons — handled above
            if t == "cicd:":
                _view_cicd(chat_id, send)
            else:
                send(chat_id, "🚀 Deploy — tap a project's deploy button from its detail screen.", _home_kb())
        else:
            # WI-6: unified project detail view for any project
            text, kb = view_project(t)
            send(chat_id, text, kb)


# ═══════════════════════════════════════════════════════════════════
#  ESTATE / TASK / PROMPT HANDLERS (A1 routing stubs — fleshed out in A2/A3/A4)
# ═══════════════════════════════════════════════════════════════════


def _load_risk_map() -> dict[str, str]:
    """WI-4/6: Load project → risk_class from projects.json.

    FAIL-CLOSED: if projects.json is missing/unreadable, every repo
    defaults to 'money' (deny) — never 'low' (allow). The fence must
    fail safe, not fail open.
    """
    risk_map: dict[str, str] = {}
    _default_risk = "money"  # fail-closed: unknown = deny
    try:
        proj_cfg = _rjson("~/.hermes/projects.json")
        projects = proj_cfg.get("projects", [])
        if not projects:
            # Empty projects.json — treat as fail-closed
            return {"__loaded__": "empty"}
        for p in projects:
            risk_map[p.get("key", "")] = p.get("risk_class", _default_risk)
        risk_map["__loaded__"] = "ok"
    except Exception:
        # Unreadable projects.json — fail-closed: mark as not loaded
        risk_map["__loaded__"] = "missing"
    return risk_map


def _risk_for(repo: str) -> str:
    """Get risk_class for a repo. Fail-closed: unknown → 'money'."""
    risk_map = _load_risk_map()
    loaded = risk_map.pop("__loaded__", "ok")
    if loaded != "ok":
        return "money"  # fail-closed: config missing → deny all
    return risk_map.get(repo, "money")


def _view_cicd(chat_id: str, send_fn) -> None:
    """WI-4: Render CI/CD status for all projects via gh CLI."""
    import subprocess as _sp
    projects = _projects()
    if not projects:
        send_fn(chat_id, "No projects found.", _home_kb())
        return

    lines = ["🔄 CI/CD Status:", ""]
    risk_map = _load_risk_map()
    kb_rows = []

    for proj in projects[:6]:  # top 6 to avoid rate limits
        try:
            proc = _sp.run(
                ["gh", "run", "list", "-R", f"chidionyema/{proj}", "-L", "3",
                 "--json", "status,conclusion,displayTitle,headBranch"],
                capture_output=True, text=True, timeout=15,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                import json as _json
                runs = _json.loads(proc.stdout)
                lines.append(f"<b>{proj}</b>:")
                for r in runs[:2]:
                    status = r.get("conclusion") or r.get("status", "?")
                    icon = {"success": "🟢", "failure": "🔴", "cancelled": "⚪",
                            "in_progress": "🟡", "queued": "🟡"}.get(status, "⚪")
                    title = r.get("displayTitle", "?")[:60]
                    lines.append(f"  {icon} {title}")

                risk = risk_map.get(proj, "low")
                if risk in ("money", "identity"):
                    kb_rows.append([{"text": f"🔒 {proj} (approval)", "callback_data": f"cicd:rerun:{proj}"}])
                else:
                    kb_rows.append([{"text": f"🔄 Re-run {proj}", "callback_data": f"cicd:rerun:{proj}"}])
            else:
                lines.append(f"<b>{proj}</b>: no recent runs")
        except Exception:
            lines.append(f"<b>{proj}</b>: gh CLI unavailable")

    kb_rows.append([{"text": "🏠 Home", "callback_data": "nv:dash:"}])
    send_fn(chat_id, "\n".join(lines), {"inline_keyboard": kb_rows})


def _handle_intake_request(chat_id: str, text: str, send_fn) -> None:
    """WI-5: Open a coordinator task from a feature request.

    Intake only — coordinator.fence_class() routes money/identity to
    awaiting_approval. The approve write stays Claude-only.
    """
    import sys as _sys, os as _os
    _SCRIPTS = _os.path.expanduser("~/.hermes/scripts")
    if _SCRIPTS not in _sys.path:
        _sys.path.insert(0, _SCRIPTS)
    import coordinator as C

    title = text[:80]
    body = text
    conn = C.connect()
    try:
        task_id = C.open_task(conn, title=title, body=body, kind="injected")
        send_fn(chat_id,
                f"✅ Request filed: `{task_id[:8]}`\n"
                f"The coordinator will diagnose and draft a plan.\n"
                f"Status updates will follow.")
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════
#  ESTATE / TASK / PROMPT HANDLERS

async def handle_estate_callback(data: str, chat_id: str, cbq_id: str) -> None:
    """Handle estate: actions (pause/resume/refresh/restart/logs/fuel/list_active).

    Ported from dead gateway: telegram.py:4207-4360 + _status_keyboard:6240-6260.
    """
    import sys, os
    _SCRIPTS = os.path.expanduser("~/.hermes/scripts")
    if _SCRIPTS not in sys.path:
        sys.path.insert(0, _SCRIPTS)
    import coordinator as C

    action = data.split(":", 1)[1] if ":" in data else data

    if action == "refresh":
        paused = C.estate_paused()
        state = "⏸ PAUSED" if paused else "▶️ RUNNING"
        send(chat_id, f"🏛 Estate: {state}\nTap a button below to control.",
             _estate_keyboard(paused))

    elif action == "pause":
        C.set_estate_paused(True)
        send(chat_id, "⏸ Estate PAUSED — no new work/spend until resumed.",
             _estate_keyboard(True))

    elif action == "resume":
        C.set_estate_paused(False)
        send(chat_id, "▶️ Estate RESUMED — work and spend re-enabled.",
             _estate_keyboard(False))

    elif action == "view_logs":
        log_path = os.path.expanduser("~/.hermes/logs/coordinator.log")
        if os.path.exists(log_path):
            with open(log_path, "r") as f:
                lines = f.readlines()[-30:]
            log_text = "".join(lines)[-3500:]
            send(chat_id, f"🪵 Coordinator Logs (last {len(lines)} lines):\n<pre>{log_text}</pre>")
        else:
            send(chat_id, "⚠️ Log file not found at ~/.hermes/logs/coordinator.log", _home_kb())

    elif action == "list_active":
        conn = C.connect()
        try:
            active = C.list_active(conn)
            if not active:
                send(chat_id, "🗂️ No active tasks in flight.", _home_kb())
            else:
                lines = [f"🗂️ Active Tasks ({len(active)}):"]
                for t in active:
                    short = t["id"][:8]
                    title = (t.get("title") or "(no title)")[:40]
                    status = t.get("status", "?")
                    lines.append(f"• `{short}` [{status}] {title}")
                send(chat_id, "\n".join(lines))
        finally:
            conn.close()

    elif action == "system_fuel":
        conn = C.connect()
        try:
            metrics = C.autonomy_ratio(conn)
            used = C.tasks_today(conn)
            budget = getattr(C, "DAILY_TASK_BUDGET", "?")
            ratio_pct = int(metrics.get("autonomy_ratio", 0) * 100)
            cost = metrics.get("total_cost", 0.0)
            tokens_in = metrics.get("tokens_input", 0)
            tokens_out = metrics.get("tokens_output", 0)
            avg_lat = metrics.get("avg_duration_seconds", 0.0)
            send(chat_id,
                 f"⛽ System Fuel &amp; Health\n"
                 f"• Daily Budget: {used}/{budget} tasks used\n"
                 f"• Autonomy Yield: {ratio_pct}%\n"
                 f"• Total cost (7d): ${cost:.4f}\n"
                 f"• Input tokens: {tokens_in}\n"
                 f"• Output tokens: {tokens_out}\n"
                 f"• Avg latency: {avg_lat}s")
        finally:
            conn.close()

    elif action == "cron":
        # B2: read ~/.hermes/cron/jobs.json and render each job
        import json
        cron_path = os.path.expanduser("~/.hermes/cron/jobs.json")
        try:
            with open(cron_path, "r") as f:
                data = json.load(f)
            jobs = data.get("jobs", [])
            if not jobs:
                send(chat_id, "📋 No cron jobs configured.", _home_kb())
            else:
                lines = [f"📋 Cron Jobs ({len(jobs)}):"]
                for j in jobs:
                    name = j.get("name", "(unnamed)")[:50]
                    schedule = j.get("schedule", {}).get("display", "?")
                    enabled = j.get("enabled", False)
                    marker = "✅" if enabled else "⏸"
                    lines.append(f"{marker} `{j['id'][:8]}` {name} — {schedule}")
                send(chat_id, "\n".join(lines))
        except Exception as e:
            send(chat_id, f"⚠️ Failed to read cron jobs: {e}")

    elif action == "restart":
        send(chat_id,
             "♻️ Restart coordinator?\n\n"
             "This SIGKILLs the daemon and drops in-flight executors "
             "(they re-submit on the next tick). Proceed?",
             {"inline_keyboard": [[
                 {"text": "✅ Confirm restart", "callback_data": "estate:restart_confirm"},
                 {"text": "✗ Cancel", "callback_data": "estate:refresh"},
             ]]})

    elif action == "restart_confirm":
        import subprocess
        label = f"gui/{os.getuid()}/ai.hermes.coordinator"
        proc = subprocess.run(
            ["launchctl", "kickstart", "-k", label],
            capture_output=True, text=True, timeout=30,
        )
        ok = proc.returncode == 0
        if ok:
            send(chat_id,
                 "♻️ Coordinator restart issued — daemon SIGKILLed and relaunched.\n"
                 "Tap 🔄 Refresh to confirm a fresh heartbeat.",
                 _estate_keyboard(C.estate_paused()))
        else:
            detail = (proc.stderr or proc.stdout or "no output").strip()
            send(chat_id, f"⚠️ Restart failed (rc={proc.returncode})\n<pre>{detail[:500]}</pre>")

    elif action == "daemons":
        # B4: list daemons via launchctl; gateway is excluded from start targets
        import subprocess
        try:
            proc = subprocess.run(
                ["launchctl", "list"],
                capture_output=True, text=True, timeout=10,
            )
            out = proc.stdout if proc.returncode == 0 else ""
        except Exception:
            out = ""

        # Known safe daemons (gateway is FENCED — never a start target)
        safe_labels = [
            ("cockpit", "ai.hermes.cockpit"),
            ("ngrok", "ai.hermes.ngrok"),
            ("otto", "ai.hermes.otto"),
            ("coordinator", "ai.hermes.coordinator"),
            ("prospector", "com.prospector.scheduler"),
        ]

        lines = ["🖥 Daemon Status:"]
        buttons = []
        for name, label in safe_labels:
            alive = label in out
            marker = "🟢" if alive else "🔴"
            cb_data = f"estate:daemon_stop:{name}" if alive else f"estate:daemon_start:{name}"
            btn_label = "⏹ Stop" if alive else "▶️ Start"
            lines.append(f"{marker} {name}")
            buttons.append([{"text": f"{btn_label} {name}", "callback_data": cb_data}])

        send(chat_id, "\n".join(lines), {"inline_keyboard": buttons})

    elif action.startswith("daemon_start:"):
        # B4: start a safe daemon via launchctl kickstart
        import subprocess
        name = action.split(":", 1)[1]
        label_map = {
            "cockpit": "ai.hermes.cockpit",
            "ngrok": "ai.hermes.ngrok",
            "otto": "ai.hermes.otto",
            "coordinator": "ai.hermes.coordinator",
            "prospector": "com.prospector.scheduler",
        }
        label = label_map.get(name)
        if not label:
            send(chat_id, f"⚠️ Unknown daemon: {name}")
        else:
            try:
                proc = subprocess.run(
                    ["launchctl", "kickstart", f"gui/{os.getuid()}/{label}"],
                    capture_output=True, text=True, timeout=15,
                )
                ok = proc.returncode == 0
                send(chat_id,
                     f"▶️ Started {name}" if ok else
                     f"⚠️ Failed to start {name} (rc={proc.returncode})")
            except Exception as e:
                send(chat_id, f"⚠️ Start error: {e}")

    elif action.startswith("daemon_stop:"):
        # B4: stop a safe daemon via launchctl kill
        import subprocess
        name = action.split(":", 1)[1]
        label_map = {
            "cockpit": "ai.hermes.cockpit",
            "ngrok": "ai.hermes.ngrok",
            "otto": "ai.hermes.otto",
            "coordinator": "ai.hermes.coordinator",
            "prospector": "com.prospector.scheduler",
        }
        label = label_map.get(name)
        if not label:
            send(chat_id, f"⚠️ Unknown daemon: {name}")
        elif name == "cockpit":
            send(chat_id, "⚠️ Cannot stop cockpit — you would cut your own webhook.")
        else:
            try:
                proc = subprocess.run(
                    ["launchctl", "kill", "SIGTERM", f"gui/{os.getuid()}/{label}"],
                    capture_output=True, text=True, timeout=15,
                )
                ok = proc.returncode == 0
                send(chat_id,
                     f"⏹ Stopped {name}" if ok else
                     f"⚠️ Failed to stop {name} (rc={proc.returncode})")
            except Exception as e:
                send(chat_id, f"⚠️ Stop error: {e}")

    else:
        send(chat_id, f"⚠️ Unknown estate action: {action}", _home_kb())


def _estate_keyboard(paused: bool) -> dict:
    """Control-panel inline keyboard for the estate view.

    Ported from dead gateway telegram.py:_status_keyboard at 6240-6260.
    """
    pause_btn = (
        {"text": "▶️ Resume", "callback_data": "estate:resume"}
        if paused else
        {"text": "⏸ Pause", "callback_data": "estate:pause"}
    )
    return {"inline_keyboard": [
        [
            {"text": "🔄 Refresh", "callback_data": "estate:refresh"},
            pause_btn,
            {"text": "♻️ Restart", "callback_data": "estate:restart"},
        ],
        [
            {"text": "📋 Active", "callback_data": "estate:list_active"},
            {"text": "🪵 Logs", "callback_data": "estate:view_logs"},
            {"text": "⛽ Fuel", "callback_data": "estate:system_fuel"},
        ],
    ]}


async def handle_task_callback(data: str, chat_id: str, cbq_id: str) -> None:
    """Handle task: actions (list + cancel; approve is Claude-only fence).

    Data shape: task:<choice>[:<task_id>]
    - task:list — list all escalated tasks
    - task:cancel:<id> — cancel an escalated task (status → cancelled)
    - task:approve:<id> — FENCED: Claude-only, risk-gated (see spec §0.2)

    Reference impl: dead gateway telegram.py:4082-4205.
    """
    import sys, os
    _SCRIPTS = os.path.expanduser("~/.hermes/scripts")
    if _SCRIPTS not in sys.path:
        sys.path.insert(0, _SCRIPTS)
    import coordinator as C

    parts = data.split(":", 2)
    if len(parts) < 2:
        send(chat_id, "⚠️ Unknown task action.", _home_kb())
        return

    choice = parts[1]  # list, cancel, approve
    task_prefix = parts[2] if len(parts) > 2 else ""

    if choice == "list":
        conn = C.connect()
        try:
            rows = conn.execute(
                "SELECT id, status, title FROM tasks WHERE status = 'escalated' ORDER BY id"
            ).fetchall()
            if not rows:
                send(chat_id, "🗂️ No escalated tasks waiting.", _home_kb())
            else:
                lines = [f"🗂️ *Escalated Tasks ({len(rows)}):*"]
                for r in rows:
                    short = r["id"][:8]
                    title = (r["title"] or "(no title)")[:50]
                    lines.append(f"• `{short}` [{r['status']}] {title}")
                send(chat_id, "\n".join(lines), _home_kb())
        finally:
            conn.close()
        return

    if not task_prefix:
        send(chat_id, "⚠️ Task action requires a task id.", _home_kb())
        return

    conn = C.connect()
    try:
        # Resolve short id prefix → full id (gateway pattern: telegram.py:4105)
        rows = conn.execute(
            "SELECT id, status, title FROM tasks WHERE id LIKE ? LIMIT 2",
            (task_prefix + "%",)
        ).fetchall()

        if len(rows) == 0:
            send(chat_id, f"⚠️ No task found matching `{task_prefix}`.", _home_kb())
            return
        if len(rows) > 1:
            send(chat_id, f"⚠️ Ambiguous prefix `{task_prefix}` matches multiple tasks.", _home_kb())
            return

        full_id = rows[0]["id"]
        current_status = rows[0]["status"]

        if choice == "cancel":
            if current_status != "escalated":
                send(chat_id, f"⚠️ Task `{task_prefix}` is not escalated (status: {current_status}).", _home_kb())
                return
            # FENCE: check if this is a money/identity task before cancelling
            title = (rows[0]["title"] or "").lower()
            body_text = ""
            kind = ""
            try:
                body_text = (rows[0]["body"] or "").lower()
                kind = (rows[0]["kind"] or "").lower()
            except (KeyError, IndexError):
                pass  # older task rows may not have these columns
            is_fenced = any(w in title or w in body_text or w in kind
                          for w in ("money", "identity", "signalengine", "introduction-exchange",
                                    "deploy_request", "🔒"))
            if is_fenced:
                send(chat_id,
                     f"🔒 Task `{task_prefix}` is money/identity-gated. "
                     f"Cancellation requires Claude review.",
                     _home_kb())
                return
            C._set(conn, full_id, status="cancelled")
            C.add_event(conn, full_id, "cancelled", "by cockpit button")
            send(chat_id, f"❌ Cancelled task `{task_prefix}` — archived.", _home_kb())

        elif choice == "approve":
            # FENCE: Claude-only — approve releases money/identity tasks.
            # Do NOT call C.approve() here. The founder gate (spec §0.2) reserves
            # this for Claude with risk-class + proof-gate checks.
            send(chat_id,
                 "🔒 Approve is handled by Claude (risk-gated). Not enabled here yet.\n"
                 f"Task `{task_prefix}` remains escalated.")

        else:
            send(chat_id, f"⚠️ Unknown task action: {choice}", _home_kb())
    finally:
        conn.close()


async def handle_prompt_callback(data: str, chat_id: str, cbq_id: str) -> None:
    """Handle update_prompt: actions (y/n).

    Reference: dead gateway telegram.py:4503+. Writes response to
    ~/.hermes/.update_response for the RSI orchestrator to consume.

    TODO(Claude): wire pending-prompt store if not reachable from cockpit.
    """
    answer_choice = data.split(":", 1)[1] if ":" in data else data
    if answer_choice not in ("y", "n"):
        send(chat_id, f"⚠️ Unknown prompt answer: {answer_choice}")
        return

    label = "Yes" if answer_choice == "y" else "No"
    send(chat_id, f"⚕ Prompt update answered: {label}")

    # Write response file for RSI orchestrator (dead gateway pattern)
    try:
        import os, tempfile
        home = os.path.expanduser("~/.hermes")
        response_path = os.path.join(home, ".update_response")
        tmp = response_path + ".tmp"
        with open(tmp, "w") as f:
            f.write(answer_choice)
        os.replace(tmp, response_path)
    except Exception:
        pass  # non-fatal if write fails

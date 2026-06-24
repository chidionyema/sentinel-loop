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
    lines.append("")

    # Keyboard: 3 columns
    kb = {"inline_keyboard": []}
    for i in range(0, len(projects), 3):
        row = []
        for j in range(3):
            if i+j < len(projects):
                n = projects[i+j][:18]
                row.append({"text": n, "callback_data": f"nv:{projects[i+j]}:"})
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
        [{"text": "← Back", "callback_data": "nv:prospector:"}],
    ]}
    return "\n".join(lines), kb


def view_killed() -> tuple[str, dict]:
    import glob as _g
    ddir = _p("~/Documents/code/prospector/store/dossiers")
    files = sorted(_g.glob(str(ddir/"*.kill.json")), key=os.path.getmtime, reverse=True)[:5]
    if not files: return "No killed dossiers.", _bk()

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
        [{"text": "🔍 Investigate", "callback_data": "di:0"}, {"text": "← Back", "callback_data": "nv:prospector:"}],
    ]}


def view_investigate() -> Tuple[str, dict | None]:
    diag = _rtxt(CFG["diag"])
    if not diag: return "No diagnostics.", None
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
        [{"text": "← Back", "callback_data": "nv:prospector:"}],
    ]}


def view_search() -> str:
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
    return "\n".join(lines)


def view_heartbeat() -> str:
    hb = _rjson(CFG["hb"])
    if not hb: return "No heartbeat"
    return "\n".join([H("♥ Heartbeat"), "",
        f"  time   {C(hb.get('ts','')[:19].replace('T',' '))}",
        f"  phase  {C(hb.get('phase','?'))}",
        f"  pid    {C(str(hb.get('pid','?')))}",
        f"  cycles {C(str(hb.get('cycles','?')))}",
        f"  batch  {C(str(hb.get('batch_size','?')))}",
    ])


def view_schedule() -> str:
    hb = _rjson(CFG["hb"])
    last = _age(hb.get("ts","")) if hb else "?"
    ival = CFG["int"]
    return "\n".join([H("⏱ Schedule"), "",
        f"  interval  {C(f'{ival}s ({ival//3600}h)')}",
        f"  last run  {C(last)}",
    ])


def view_alerts() -> str:
    a = _rtxt(CFG["alerts"]).strip()
    if not a: return H("🚨 Alerts") + "\n\nNo active alerts"
    return H("🚨 Alerts") + "\n\n" + "".join(f"  ⚠ {line.split('] ',1)[-1][:100]}\n" for line in a.splitlines()[:3])


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
    kb["inline_keyboard"].append([{"text":"🔄 Refresh","callback_data":f"dl:{page}:0"},{"text":"← Back","callback_data":"nv:prospector:"}])
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
    elif data.startswith("dh:"): send(chat_id, view_heartbeat())
    elif data.startswith("dg:"): send(chat_id, view_heartbeat())  # shortcut
    elif data.startswith("da:"): send(chat_id, view_alerts())
    elif data.startswith("ds:"): send(chat_id, view_schedule())
    elif data.startswith("dk:"):
        text, kb = view_killed()
        send(chat_id, text, kb)
    elif data.startswith("di:"):
        text, kb = view_investigate()
        if kb: send(chat_id, text, kb)
        else: send(chat_id, text)
    elif data.startswith("dz:"):
        send(chat_id, view_search(), {"inline_keyboard": [
            [{"text":"🔄 Retest","callback_data":"dz:0"},{"text":"▶️ Run 3","callback_data":"dr:3"}],
            [{"text":"← Back","callback_data":"nv:prospector:"}],
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

#!/usr/bin/env python3
"""Persistent Otto server — threaded, handles concurrent requests."""
import json, os, sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

HERMES_ROOT = os.path.expanduser("~/.hermes")
os.chdir(HERMES_ROOT)

with open(os.path.join(HERMES_ROOT, ".env")) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
os.environ["HERMES_YOLO_MODE"] = "1"
os.environ["HERMES_ACCEPT_HOOKS"] = "1"

from hermes_cli.config import load_config; cfg = load_config()
mc = cfg.get("model") or {}
cm = mc.get("default") if isinstance(mc, dict) else mc
em = cm or "deepseek-v4-pro"
from hermes_cli.models import detect_provider_for_model
from hermes_cli.runtime_provider import resolve_runtime_provider
cp = mc.get("provider","") if isinstance(mc,dict) else ""
d = detect_provider_for_model(em, cp or "auto")
ep, em = (d if d else (cp or "deepseek", em))
rt = resolve_runtime_provider(requested=ep, target_model=em or None)
from hermes_cli.tools_config import _get_platform_tools
ts = sorted(_get_platform_tools(cfg, "cli"))
from run_agent import AIAgent
agent = AIAgent(api_key=rt.get("api_key"), base_url=rt.get("base_url"),
    provider=rt.get("provider"), api_mode=rt.get("api_mode"),
    model=em, enabled_toolsets=ts, quiet_mode=True, platform="cli",
    load_soul_identity=True)
print("[otto] Agent ready.", file=sys.stderr, flush=True)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200); self.send_header("Content-Type","application/json")
            self.end_headers(); self.wfile.write(b'{"status":"ok"}')
        else: self.send_error(404)
    def do_POST(self):
        if self.path != "/chat": self.send_error(404); return
        try:
            length = int(self.headers.get("Content-Length","0"))
            body = json.loads(self.rfile.read(length))
            prompt = body.get("prompt","").strip()
        except: self.send_error(400); return
        if not prompt: self.send_error(400); return
        try: response = agent.chat(prompt) or "(no response)"
        except Exception as e: response = f"Error: {e}"
        self.send_response(200); self.send_header("Content-Type","text/plain; charset=utf-8")
        self.end_headers(); self.wfile.write(response.encode("utf-8"))
    def log_message(self, *a): pass

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8802
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"[otto] Listening on 127.0.0.1:{port}", file=sys.stderr, flush=True)
    srv.serve_forever()

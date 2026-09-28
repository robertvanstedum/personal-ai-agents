"""Stand-in for `node /app/openclaw.mjs gateway [--bind loopback]` in
tests/cos/test_start_with_mc.py. Serves /readyz on 127.0.0.1:$MINIMOI_MC_PORT,
logs each start (bind mode, which config) to $FAKE_LOG, exits 0 on SIGTERM.
On the LAN bind it exits by itself after $FAKE_LAN_SECONDS (so the test ends)."""
import http.server
import json
import os
import signal
import sys
import threading

args = sys.argv[1:]
bind = "loopback" if "--bind" in args and args[args.index("--bind") + 1] == "loopback" else "lan"
config = json.loads(open(os.environ["OPENCLAW_CONFIG_PATH"]).read())
agents = ",".join(sorted(config.get("agents", {}).get("entries", {})))
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(f"start bind={bind} agents={agents}\n")
# FAKE_CRASH_AGENTS: the gateway exits at once when these agents are configured
# (a config OpenClaw cannot start).
if os.environ.get("FAKE_CRASH_AGENTS") == agents:
    sys.exit(1)


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path in ("/readyz", "/healthz") else 404)
        self.end_headers()

    def log_message(self, *a):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", int(os.environ["MINIMOI_MC_PORT"])), Handler)
signal.signal(signal.SIGTERM, lambda *a: os._exit(0))
if bind == "lan":
    threading.Timer(float(os.environ.get("FAKE_LAN_SECONDS", "2")), lambda: os._exit(0)).start()
server.serve_forever()

"""Stand-in for `node /app/openclaw.mjs gateway [--bind loopback]` in
tests/test_mc_start_script.py: serves /readyz on 127.0.0.1:$MINIMOI_MC_PORT,
logs each start to $FAKE_LOG, exits 0 on SIGTERM. FAKE_GATEWAY_EXIT=loopback
makes the loopback start exit at once (a start that fails under contention).
On the LAN bind it exits by itself after $FAKE_LAN_SECONDS."""
import http.server
import os
import signal
import sys
import threading

args = sys.argv[1:]
bind = "loopback" if "--bind" in args and args[args.index("--bind") + 1] == "loopback" else "lan"
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(f"start bind={bind}\n")
if os.environ.get("FAKE_GATEWAY_EXIT") == bind:
    sys.exit(1)
if os.environ.get("FAKE_GATEWAY_KILLED") == bind:
    os.kill(os.getpid(), signal.SIGKILL)       # as the OOM killer would
# FAKE_GATEWAY_EXIT_ONCE: the first loopback start exits (a lease still held).
marker = os.environ["FAKE_LOG"] + ".exited-once"
if os.environ.get("FAKE_GATEWAY_EXIT_ONCE") and bind == "loopback" and not os.path.exists(marker):
    open(marker, "w").close()
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
    threading.Timer(float(os.environ.get("FAKE_LAN_SECONDS", "5")), lambda: os._exit(0)).start()
server.serve_forever()

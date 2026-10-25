"""A05 end to end on the real admission path (spec v1.2 §7.1 hard gate; Codex M1 review P1-4).

Everything that decides admission is real: the portal's own OpenClaw backend code (streamed and buffered-internal
paths), the pinned relay (docker/mc-agent/relay_v2.mjs), the installed native OpenClaw gateway with the minimoi-tools
plugin (tests/mc_tools/openclaw_admission_gateway.mjs, fake model provider), the tool listener and ToolService, on a
disposable PostgreSQL. Between the plugin and the listener sits a holding proxy: it can keep a tool callback that is
already in flight and deliver it later, so an old callback really arrives while a later turn is live, after Stop,
after a gateway restart, or after its lease ran out.

Off unless MC_NATIVE_ADMISSION=1 (it starts a gateway: run it under heavy_job.py through run_postgres.sh). Evidence
JSON goes to $PROBE_EVIDENCE when set.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from test_tool_path_postgres import Base, CONV, DSN, EPOCH, OWNER  # noqa: F401
from core.mc_tools import binding
from minimoi_portal.guild_ui.mc import TurnRequest
from minimoi_portal.guild_ui.mc.openclaw import OpenClawMasterCraftsman
from minimoi_portal.guild_ui.mc.stream import Delta, RunStarted
from services.mc_tools.listener import create_app, serve

REPO = Path(__file__).resolve().parents[2]
OPENCLAW = os.environ.get("PROBE_OPENCLAW", "/opt/homebrew/lib/node_modules/openclaw/openclaw.mjs")
ENABLED = os.environ.get("MC_NATIVE_ADMISSION") == "1" and shutil.which("node") and Path(OPENCLAW).exists()


def _port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class HoldingProxy:
    """Plugin -> proxy -> listener. While ``hold`` is set, tool calls are kept (the plugin's request stays open) until
    ``release()``; the announcement of a gateway process always passes at once."""

    def __init__(self, listener_port):
        self.listener_port, self.hold, self.held, self.results, self.instances = listener_port, False, [], [], []
        self._released = threading.Event()
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                headers = {k: v for k, v in self.headers.items()
                           if k.lower() in ("authorization", "content-type") or k.lower().startswith("x-mc-")}
                call = {"path": self.path, "run": headers.get("X-MC-Run"), "instance": headers.get("X-MC-Instance"),
                        "headers": headers, "body": body}
                if self.path.endswith("/instance"):
                    proxy.instances.append(json.loads(body)["instance"])
                elif proxy.hold:
                    proxy.held.append(call)
                    proxy._released.wait(120)
                status, answer = proxy.forward(self.path, headers, body)
                if not self.path.endswith("/instance"):
                    proxy.results.append({**call, "answer": answer})
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    out = json.dumps(answer).encode()
                    self.send_header("Content-Length", str(len(out)))
                    self.end_headers()
                    self.wfile.write(out)
                except OSError:
                    pass                                       # the plugin's side is gone (Stop, restart): retained anyway

        self.port = _port()
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def forward(self, path, headers, body):
        conn = http.client.HTTPConnection("127.0.0.1", self.listener_port, timeout=30)
        try:
            conn.request("POST", path, body=body, headers=headers)
            r = conn.getresponse()
            return r.status, json.loads(r.read() or b"{}")
        except OSError:
            return 502, {"error": "listener_unreachable"}            # e.g. an announcement before the listener is up
        finally:
            conn.close()

    def release(self):
        self._released.set()

    def reset(self):
        self.hold, self.held, self._released = False, [], threading.Event()


@unittest.skipUnless(DSN and ENABLED, "needs MC_NATIVE_ADMISSION=1, node, the installed OpenClaw and a disposable DSN")
class NativeAdmissionProbe(Base, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp_dir = os.path.realpath(tempfile.mkdtemp(prefix="mc-admission-"))   # the plugin refuses symlinked paths
        cls.executor = secrets.token_urlsafe(32)
        token_file = Path(cls.tmp_dir) / "token"
        token_file.write_text(cls.executor + "\n")
        token_file.chmod(0o600)
        cls.listener_port = _port()
        cls.proxy = HoldingProxy(cls.listener_port)
        cls.gateway = subprocess.Popen(["node", str(REPO / "tests/mc_tools/openclaw_admission_gateway.mjs"),
                                        f"http://127.0.0.1:{cls.proxy.port}/internal/mc/v1/tools", str(token_file),
                                        OPENCLAW], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        cls.gw = cls._ready()
        cls.caller = secrets.token_urlsafe(24)
        cls.relay_port = _port()
        cls.relay = subprocess.Popen(["node", str(REPO / "docker/mc-agent/relay_v2.mjs")], env={
            "PATH": os.environ["PATH"], "MC_RELAY_PORT": str(cls.relay_port),
            "MC_RELAY_TARGET": f"http://127.0.0.1:{cls.gw['port']}", "MC_RELAY_TOKEN": cls.caller,
            "MC_OPENCLAW_GATEWAY_TOKEN": cls.gw["token"], "MC_RELAY_DEADLINE_MS": "90000", "MC_RELAY_IDLE_MS": "60000",
            "MC_RELAY_STATE_DIR": cls.tmp_dir}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.evidence = {"openclaw": OPENCLAW, "relay": str(REPO / "docker/mc-agent/relay_v2.mjs"), "cases": {}}

    @classmethod
    def _ready(cls):
        line = cls.gateway.stdout.readline()
        gw = json.loads(line or '{"ready": false}')
        if not gw.get("ready"):
            raise RuntimeError("gateway did not start: " + str(gw.get("log", ""))[-1500:])
        return gw

    @classmethod
    def tearDownClass(cls):
        for proc in (cls.relay, cls.gateway):
            try:
                if proc is cls.gateway:
                    proc.stdin.write("quit\n")
                    proc.stdin.flush()
                    proc.wait(60)
                else:
                    proc.terminate()
                    proc.wait(10)
            except Exception:
                proc.kill()
        cls.proxy.server.shutdown()
        if os.environ.get("PROBE_EVIDENCE"):
            Path(os.environ["PROBE_EVIDENCE"]).write_text(json.dumps(cls.evidence, indent=1, default=str))
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.finish_turn(self.ledger_id)                          # the fixture's own turn: not part of this probe
        if not getattr(type(self), "_serving", False):
            type(self)._service_ref = [self.service]
            serve(create_app(_Delegate(type(self)._service_ref),
                             token_sha256=hashlib.sha256(self.executor.encode()).hexdigest()),
                  host="127.0.0.1", port=self.listener_port)
            type(self)._serving = True
        type(self)._service_ref[0] = self.service
        if self.proxy.instances:                                   # this test's fresh tables: the live process again
            self.service.register_instance(self.proxy.instances[-1])
        self.backend = OpenClawMasterCraftsman(f"http://127.0.0.1:{self.relay_port}/v1", self.caller,
                                               chat_model="openclaw/mc-chat")
        self.proxy.reset()

    # ── the portal's side ────────────────────────────────────────────────────────────────────────────────────
    def turn(self, note, text, *, attach=True, attach_delay=0.0):
        """Claim and record the owner's turn as the portal does, then stream it; the run is attached on its opening
        event. Returns (thread, state) with state filled as the stream runs."""
        ledger_id = self.claim(note)
        with self.connect() as conn:
            binding.record(conn, ledger_id=ledger_id, claim_generation=1, principal=OWNER, conversation_id=CONV,
                           note_request_id=note, session_user=self.backend.session_user(f"guild:{OWNER}:{CONV}"),
                           runtime_agent="mc-chat", environment="dev", portal_epoch=EPOCH)
        req = TurnRequest(conversation_id=f"guild:{OWNER}:{CONV}", text=text, note_request_id=note,
                          correlation_id=secrets.token_hex(16))
        state = {"ledger_id": ledger_id, "req": req, "run": None, "text": "", "events": []}

        def go():
            for event in self.backend.stream_turn(req):
                state["events"].append(type(event).__name__)
                if isinstance(event, RunStarted) and state["run"] is None:
                    state["run"] = event.run_id
                    if attach:
                        time.sleep(attach_delay)
                        with self.connect() as conn:
                            state["attached"] = binding.attach_run(conn, ledger_id=ledger_id, claim_generation=1,
                                                                   run_id=event.run_id, environment="dev")
                elif isinstance(event, Delta):
                    state["text"] += event.text
        thread = threading.Thread(target=go, daemon=True)
        thread.start()
        return thread, state

    def wait_held(self, n=1, timeout=60):
        end = time.monotonic() + timeout
        while len(self.proxy.held) < n and time.monotonic() < end:
            time.sleep(0.05)
        self.assertEqual(len(self.proxy.held), n, "the tool callback never reached the proxy")

    def answers(self, run, n=1, timeout=30):
        """The listener's answers to this run's calls, waiting for the proxy to finish forwarding (the portal's stream
        can end before a retained callback is delivered)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            found = [r["answer"] for r in self.proxy.results if r["run"] == run]
            if len(found) >= n:
                return found
            time.sleep(0.05)
        return [r["answer"] for r in self.proxy.results if r["run"] == run]

    def record(self, case, **facts):
        self.evidence["cases"][case] = facts

    # ── the cases ────────────────────────────────────────────────────────────────────────────────────────────
    def test_1_a_streamed_turn_admits_its_own_call(self):
        thread, state = self.turn("n-happy", "Any guest requests? TOOL")
        thread.join(90)
        seen = self.answers(state["run"])
        self.assertEqual(len(seen), 1, json.dumps({"events": state["events"], "text": state["text"][:500],
                                                   "run": state["run"], "attached": state.get("attached"),
                                                   "instances": self.proxy.instances,
                                                   "results": [r["run"] for r in self.proxy.results]}))
        [answer] = seen
        self.assertEqual((answer["status"], answer["code"]), ("done", "listed"), answer)
        self.assertTrue(state["text"].startswith("ANSWER"))
        self.record("streamed_turn_admitted", run=state["run"], answer=answer["code"], instance=self.proxy.instances[-1])

    def test_2_a_buffered_turn_streams_internally_and_admits_its_own_call(self):
        from minimoi_portal.guild_ui.mc.internal_stream import collect
        ledger_id = self.claim("n-buffered")
        with self.connect() as conn:
            binding.record(conn, ledger_id=ledger_id, claim_generation=1, principal=OWNER, conversation_id=CONV,
                           note_request_id="n-buffered",
                           session_user=self.backend.session_user(f"guild:{OWNER}:{CONV}"), runtime_agent="mc-chat",
                           environment="dev", portal_epoch=EPOCH)
        runs = []

        def attach(run_id):
            runs.append(run_id)
            with self.connect() as conn:
                binding.attach_run(conn, ledger_id=ledger_id, claim_generation=1, run_id=run_id, environment="dev")
        req = TurnRequest(conversation_id=f"guild:{OWNER}:{CONV}", text="Guests? TOOL", note_request_id="n-buffered",
                          correlation_id=secrets.token_hex(16))
        result = collect(self.backend, req, on_run=attach, record_usage=lambda **kw: None)
        self.assertEqual(result.status, "answered")
        self.assertTrue(result.text.startswith("ANSWER"))
        self.assertEqual([a["code"] for a in self.answers(runs[0])], ["listed"])
        self.record("buffered_turn_admitted", run=runs[0])

    def test_3_a_call_before_the_run_is_attached_waits_for_that_run_and_never_falls_back(self):
        thread, state = self.turn("n-late", "TOOL please", attach_delay=1.0)
        thread.join(90)
        self.assertEqual([a["code"] for a in self.answers(state["run"])], ["listed"])
        thread, state = self.turn("n-never", "TOOL please", attach=False)
        thread.join(90)
        [answer] = self.answers(state["run"])
        self.assertEqual((answer["status"], answer["code"]), ("refused", "tool_run_not_bound"))
        self.record("tool_before_registration", late_attach="admitted", never_attached=answer["code"])

    def test_4_stop_refuses_a_callback_that_arrives_while_the_runtime_aborts(self):
        """Codex's boundary: Stop has been pressed, the runtime has not finished aborting, the turn is still claimed."""
        self.proxy.hold = True
        thread, a = self.turn("n-stop-a", "TOOL now")
        self.wait_held()
        with self.connect() as conn:                               # StreamRun.stop: revoke first, then the runtime
            binding.revoke(conn, ledger_id=a["ledger_id"], claim_generation=1)
        self.assertEqual(self.one("SELECT state FROM guild.mc_turn_ledger WHERE id=%s", (a["ledger_id"],))[0], "claimed")
        self.proxy.release()                                       # the late call lands before anything else ends
        self.assertEqual(self.answers(a["run"])[0]["code"], "tool_turn_stopped")
        self.backend.stop(a["req"].correlation_id)
        thread.join(90)
        self.finish_turn(a["ledger_id"])
        self.record("stop_boundary", a=self.answers(a["run"])[0]["code"])

    def test_4b_an_old_callback_delivered_while_the_next_turn_is_claimed_is_refused_and_that_turn_admitted(self):
        self.proxy.hold = True
        thread, a = self.turn("n-old-a", "TOOL first")
        self.wait_held()
        with self.connect() as conn:
            binding.revoke(conn, ledger_id=a["ledger_id"], claim_generation=1)
        self.backend.stop(a["req"].correlation_id)
        thread.join(90)
        self.finish_turn(a["ledger_id"])                           # A is over; its callback is still in flight
        self.proxy.hold = False
        b_thread, b = self.turn("n-old-b", "TOOL second", attach_delay=0.5)
        self.proxy.release()                                       # A's old callback lands while B is claimed
        b_thread.join(90)
        self.assertEqual(self.answers(a["run"])[0]["code"], "tool_turn_not_live")
        self.assertEqual([x["code"] for x in self.answers(b["run"])], ["listed"])
        self.record("old_callback_during_next_turn", a=self.answers(a["run"])[0]["code"],
                    b=self.answers(b["run"])[0]["code"])

    def test_5_forged_or_missing_identity_is_refused_on_the_real_listener(self):
        self.proxy.hold = True
        thread, a = self.turn("n-forge", "TOOL forge")
        self.wait_held()
        held = self.proxy.held[0]["headers"]

        def post(**change):
            headers = {**held, **change}
            headers = {k: v for k, v in headers.items() if v is not None}
            return self.proxy.forward("/internal/mc/v1/tools/minimoi_guest_requests", headers,
                                      json.dumps({"arguments": {}}).encode())
        out = {
            "no_run": post(**{"X-MC-Run": None})[1].get("code"),
            "unknown_run": post(**{"X-MC-Run": "chatcmpl_00000000-0000-4000-8000-000000000000"})[1].get("code"),
            "other_instance": post(**{"X-MC-Instance": "f" * 32})[1].get("code"),
            "other_agent": post(**{"X-MC-Agent": "main", "X-MC-Session": held["X-MC-Session"].replace("mc-chat", "main")})[1].get("code"),
            "no_credential": post(**{"Authorization": "Bearer " + "x" * 40})[0],
        }
        self.proxy.release()
        thread.join(90)
        self.assertEqual(out, {"no_run": "tool_run_missing", "unknown_run": "tool_run_not_bound",
                               "other_instance": "tool_runtime_replaced", "other_agent": "tool_session_invalid",
                               "no_credential": 401})
        self.assertEqual(self.answers(a["run"])[-1]["code"], "listed")     # the genuine call still admitted
        self.record("forged_or_missing_identity", **out)

    def test_6_a_retained_callback_after_its_lease_ran_out_is_refused(self):
        self.proxy.hold = True
        thread, a = self.turn("n-lease", "TOOL lease")
        self.wait_held()
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE guild.mc_turn_ledger SET lease_until = clock_timestamp() - interval '1 second' "
                        "WHERE id=%s", (a["ledger_id"],))
        self.proxy.release()
        thread.join(90)
        self.assertEqual(self.answers(a["run"])[0]["code"], "tool_turn_not_live")
        self.record("lease_expired", a=self.answers(a["run"])[0]["code"])

    def test_7_after_a_gateway_restart_a_retained_callback_is_refused_with_a_live_lease(self):
        self.proxy.hold = True
        thread, a = self.turn("n-restart-a", "TOOL restart")
        self.wait_held()
        before = list(self.proxy.instances)
        self.gateway.stdin.write("restart\n")
        self.gateway.stdin.flush()
        self._ready()
        end = time.monotonic() + 60
        while len(self.proxy.instances) == len(before) and time.monotonic() < end:
            time.sleep(0.1)                                        # the new process announces itself at start
        self.assertNotEqual(self.proxy.instances[-1], before[-1] if before else None)
        live = self.one("SELECT state='claimed' AND lease_until > clock_timestamp() FROM guild.mc_turn_ledger "
                        "WHERE id=%s", (a["ledger_id"],))[0]
        self.assertTrue(live)
        self.proxy.release()
        thread.join(90)
        self.assertEqual(self.answers(a["run"])[0]["code"], "tool_runtime_replaced")
        self.finish_turn(a["ledger_id"])
        self.proxy.hold = False
        b_thread, b = self.turn("n-restart-b", "TOOL after restart")
        b_thread.join(90)
        self.assertEqual([x["code"] for x in self.answers(b["run"])], ["listed"])
        self.assertEqual(self.proxy.results[-1]["instance"], self.proxy.instances[-1])
        self.record("restart", old_instance=before[-1] if before else None, new_instance=self.proxy.instances[-1],
                    a=self.answers(a["run"])[0]["code"], b=self.answers(b["run"])[0]["code"])


    def test_8_a_policy_change_refuses_a_retained_callback_and_the_next_turn_runs_under_the_new_policy(self):
        self.assertEqual(self.call_direct_ok(), "listed")                          # the policy in force is active
        self.proxy.hold = True
        thread, a = self.turn("n-policy-a", "TOOL policy")
        self.wait_held()
        old = self.policy.current()[0].version
        self.install_policy(revision="probe-2")                                    # the operator installs a new one
        self.proxy.release()
        thread.join(90)
        self.assertEqual(self.answers(a["run"])[0]["code"], "tool_policy_changed")
        self.finish_turn(a["ledger_id"])
        self.proxy.hold = False
        b_thread, b = self.turn("n-policy-b", "TOOL after policy")
        b_thread.join(90)
        self.assertEqual([x["code"] for x in self.answers(b["run"])], ["listed"])
        self.record("policy_change", old=old, new=self.policy.current()[0].version,
                    a=self.answers(a["run"])[0]["code"], b=self.answers(b["run"])[0]["code"])

    def call_direct_ok(self):
        thread, state = self.turn("n-policy-warm", "TOOL warm")
        thread.join(90)
        code = self.answers(state["run"])[0]["code"]
        self.finish_turn(state["ledger_id"])
        return code


class _Delegate:
    """The listener is started once for the class; each test's fresh ToolService is swapped in behind it."""

    def __init__(self, ref):
        self.ref = ref

    def call(self, *a, **kw):
        return self.ref[0].call(*a, **kw)

    def register_instance(self, instance):
        return self.ref[0].register_instance(instance)


if __name__ == "__main__":
    unittest.main()

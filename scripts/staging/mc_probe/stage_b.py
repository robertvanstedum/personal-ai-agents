#!/usr/bin/env python3
"""No-spend stage B probe: a synthetic turn through the one-way relay (MC spec
v0.9 §4, §6). THROWAWAY containers only; staging is never touched.

  python3 scripts/staging/mc_probe/stage_b.py --mc-image IMAGE:TAG --portal-image minimoi-staging/portal:<sha7> [--out DIR]

Topology, as on staging, under throwaway names:
  mcp-front (internal, no host address): a portal-side client + mc-relay
  mcp-mc    (internal, no host address): mc-relay + mc-agent + a stand-in model endpoint
MC and the relay run from the real docker-compose.mc.yml. The model endpoint
is the capture server (capture-server.mjs) under the alias model-gateway: it
answers from a script, so MC "answers" with no provider and no spend. The
portal side is the portal image running THIS branch's adapter
(minimoi_portal/guild_ui/mc/openclaw.py, mounted read-only): the exact code
the Shop floor's /mc/turns route calls.

Checks: health before any answer ("connected, no answer yet"); a synthetic
turn answered with its correlation id echoed and a runtime response id; the
header "live" only after that; a refused model key (the staging case) shows
"unavailable · its model key was refused"; the relay refuses every other
path, model alias and caller; MC cannot resolve or reach the portal side, and
reaches the relay only without a caller token; no token in the relay's logs.

Streaming (streaming spec v0.2 §2 and §10, S1): the relay runs THIS branch's
relay.mjs (mounted over the image's), and the portal side calls the adapter's
stream_turn():
  P4  a streamed turn: text, finish, usage, and the timings to the first
      delta, the finish and the end;
  P1  an upstream failure mid-stream reaches the Shop floor as a failure;
  P2  Stop mid-stream: the portal side gets "stopped", and whether OpenClaw
      then closes its own call to the model endpoint (the capture server
      logs "aborted" or "finished") and whether any further call starts;
  --  a caller that leaves mid-stream does not stop the run (finished).
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
P = "mcpb"
NET_MC, NET_FRONT = f"{P}-mc", f"{P}-front"
MC, RELAY, CAP, PORTAL = f"{P}-mc-agent", f"{P}-mc-relay", f"{P}-capture", f"{P}-portal-side"
VOLS = (f"{P}-mc-state", f"{P}-mc-auth")
RESULTS: list[dict] = []
ISOLATED = ["-o", "com.docker.network.bridge.gateway_mode_ipv4=isolated"]


def record(check, ok, detail=""):
    RESULTS.append({"check": check, "pass": bool(ok), "detail": detail})
    print(f"  {'PASS' if ok else 'FAIL'} {check}" + (f" — {detail}" if detail else ""), flush=True)


def sh(*cmd, env=None, timeout=900, check=False):
    return subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout, check=check,
                          env={**os.environ, **(env or {})})


def docker(*a, **kw):
    return sh("docker", *a, **kw)


PORTAL_SIDE = r"""
import json, sys
sys.path.insert(0, "/src")
from minimoi_portal.guild_ui.mc.openclaw import OpenClawMasterCraftsman
from minimoi_portal.guild_ui.mc.backend import TurnRequest, view
import os
b = OpenClawMasterCraftsman("http://mc-relay:8790/v1", os.environ["MC_RUNTIME_TOKEN"])
out = {}
h = b.health(); out["before"] = [h.state, h.reason, h.reachable, view(h, notes_ok=True, turns_on=True)["header"]]
r = b.turn(TurnRequest("guild:robert:probe", "What is stuck on #12?", "n-probe-1", correlation_id=sys.argv[1]))
out["turn"] = [r.status, r.failure_class, r.text, (r.trace or {}).get("correlation_echo") == sys.argv[1], bool((r.trace or {}).get("response_id"))]
h = b.health(); out["after"] = [h.state, view(h, notes_ok=True, turns_on=True)["header"]]
if len(sys.argv) > 2:
    r2 = b.turn(TurnRequest("guild:robert:probe", "second", "n-probe-2", correlation_id=sys.argv[2]))
    h = b.health(); out["refused"] = [r2.status, r2.failure_class, view(h, notes_ok=True, turns_on=True)["header"]]
print(json.dumps(out))
"""

ERROR_SURFACE = r"""
import json, os, sys
sys.path.insert(0, "/src")
from minimoi_portal.guild_ui.mc.openclaw import OpenClawMasterCraftsman
from minimoi_portal.guild_ui.mc.backend import TurnRequest
b = OpenClawMasterCraftsman("http://mc-relay:8790/v1", os.environ["MC_RUNTIME_TOKEN"])
r = b.turn(TurnRequest("guild:robert:probe", "hello", "n-err", correlation_id=sys.argv[1]))
import urllib.request as u
body = json.dumps({"model": "openclaw/mc-agent", "user": "guild-mc:err", "messages": [{"role": "user", "content": "again"}]}).encode()
try:
    raw = u.urlopen(u.Request("http://mc-relay:8790/v1/chat/completions", data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["MC_RUNTIME_TOKEN"]}), timeout=120).read().decode()
except u.HTTPError as e:
    raw = e.read().decode()
print(json.dumps({"status": r.status, "failure_class": r.failure_class, "text": r.text, "relay_status": (r.trace or {}).get("relay_status"), "runtime_body": raw[:300]}))
"""

RELAY_RULES = r"""
import json, os, urllib.request as u
base = "http://mc-relay:8790"; tok = os.environ["MC_RUNTIME_TOKEN"]
def code(path, method="GET", body=None, token=True):
    h = {"Content-Type": "application/json"}
    if token: h["Authorization"] = "Bearer " + tok
    try: return u.urlopen(u.Request(base + path, data=body, headers=h, method=method), timeout=10).status
    except u.HTTPError as e: return e.code
    except Exception as e: return type(e).__name__
chat = lambda m: json.dumps({"model": m, "user": "guild-mc:x", "messages": [{"role": "user", "content": "x"}]}).encode()
print(json.dumps({"readyz": code("/readyz"), "no_token": code("/readyz", token=False),
  "tools_invoke": code("/tools/invoke", "POST", b"{}"), "embeddings": code("/v1/embeddings", "POST", b"{}"),
  "models": code("/v1/models"), "alias_default": code("/v1/chat/completions", "POST", chat("openclaw/default")),
  "alias_cos": code("/v1/chat/completions", "POST", chat("openclaw/cos-agent-a")),
  "stream_string": code("/v1/chat/completions", "POST", json.dumps({"model": "openclaw/mc-agent", "user": "guild-mc:x", "stream": "true", "messages": [{"role": "user", "content": "x"}]}).encode()),
  "stream_options": code("/v1/chat/completions", "POST", json.dumps({"model": "openclaw/mc-agent", "user": "guild-mc:x", "stream": True, "stream_options": {"include_usage": False}, "messages": [{"role": "user", "content": "x"}]}).encode()),
  "stop_unknown": code("/v1/turns/stop", "POST", json.dumps({"correlation_id": "0" * 32}).encode())}))
"""

STREAM_SIDE = r"""
import json, os, sys, time
sys.path.insert(0, "/src")
from minimoi_portal.guild_ui.mc.openclaw import OpenClawMasterCraftsman
from minimoi_portal.guild_ui.mc.backend import TurnRequest
mode, corr = sys.argv[1], sys.argv[2]
b = OpenClawMasterCraftsman("http://mc-relay:8790/v1", os.environ["MC_RUNTIME_TOKEN"])
t0 = time.monotonic()
out = {"events": [], "t": {}}
it = b.stream_turn(TurnRequest("guild:robert:stream-probe", "Stream, please", "n-" + mode, correlation_id=corr))
for ev in it:
    ms = int((time.monotonic() - t0) * 1000)
    name = type(ev).__name__
    value = getattr(ev, "text", None) or getattr(ev, "reason", None) or getattr(ev, "cls", None)
    if name == "Usage":
        value = [ev.prompt_tokens, ev.completion_tokens]
    out["events"].append([name, value, ms])
    if name == "Delta" and "first_delta" not in out["t"]:
        out["t"]["first_delta"] = ms
        if mode == "stop":
            out["stopped"] = b.stop(corr)
        if mode == "leave":
            break
    if name == "Finish":
        out["t"]["finish"] = ms
it.close()
out["t"]["end"] = int((time.monotonic() - t0) * 1000)
print(json.dumps(out))
"""

FROM_MC = r"""
const net = require('net'), dns = require('dns');
const probe = (h, p) => new Promise(r => { const s = net.connect({ host: h, port: p, timeout: 3000 });
  s.on('connect', () => { s.destroy(); r('connected'); }); s.on('timeout', () => { s.destroy(); r('timeout'); }); s.on('error', e => r(e.code)); });
(async () => {
  const out = {};
  out.portal_side_dns = await new Promise(r => dns.lookup('%(portal)s', e => r(e ? e.code : 'resolved')));
  out.portal_side_ip = await probe('%(portal_ip)s', 5001);
  const res = await fetch('http://mc-relay:8790/v1/chat/completions', { method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ model: 'openclaw/mc-agent', user: 'guild-mc:x', messages: [{ role: 'user', content: 'x' }] }) }).then(r => r.status, e => e.name);
  out.relay_without_caller_token = res;
  console.log(JSON.stringify(out));
})();
"""


def _capture(path, body=None):
    js = (f"fetch('http://127.0.0.1:4001{path}'" + (f",{{method:'POST',body:JSON.stringify({json.dumps(body)})}}" if body else "")
          + ").then(r=>r.text()).then(t=>console.log(t))")
    return docker("exec", CAP, "node", "-e", js).stdout


def _stream_side(mode):
    corr = secrets.token_hex(16)
    out = docker("exec", PORTAL, "python", "-c", STREAM_SIDE, mode, corr, timeout=300)
    try:
        return corr, json.loads(out.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return corr, {"error": (out.stderr or out.stdout)[-400:], "events": []}


def _chat_log():
    try:
        return [e for e in json.loads(_capture("/log") or "[]") if e["path"].endswith("/chat/completions")]
    except json.JSONDecodeError:
        return []


def stream_probes(a):
    """P4, P1, P2 and a leaving caller (see the module doc). No spend: the model
    endpoint is the capture server."""
    words = ["Item ", "12 ", "is ", "in ", "build."]
    _capture("/script", {"model": "minimoi-mc-agent", "replace": True,
                         "steps": [{"chunks": words, "chunk_delay_ms": 300}]})
    _, res = _stream_side("happy")
    kinds = [e[0] for e in res.get("events", [])]
    text = "".join(e[1] for e in res.get("events", []) if e[0] == "Delta")
    record("P4 a streamed turn through the relay: its text, then finish 'stop'",
           text == "Item 12 is in build." and ["Finish", "stop"] in [e[:2] for e in res["events"]], json.dumps(res)[:300])
    usage = [e[1] for e in res.get("events", []) if e[0] == "Usage"]
    record("P4 the stream carries its usage (include_usage), provider-reported through OpenClaw",
           bool(usage) and all(isinstance(n, int) for n in usage[-1]), json.dumps(usage))
    record("P4 (timings, informational): to the first delta, the finish, and the end", True, json.dumps(res.get("t")))

    before = len(_chat_log())
    _capture("/script", {"model": "minimoi-mc-agent", "replace": True,
                         "steps": [{"chunks": words, "chunk_delay_ms": 200, "fail_after": 2}] * 6})
    _, res = _stream_side("p1")
    kinds = [e[:2] for e in res.get("events", [])]
    record("P1 an upstream failure mid-stream reaches the Shop floor as a failure, never an answer",
           ["Finish", "stop"] not in kinds and (kinds and kinds[-1][0] == "Failure" or "Finish" not in [k[0] for k in kinds]),
           json.dumps(kinds))
    record("P1 (informational): model calls OpenClaw made for that turn", True, str(len(_chat_log()) - before))

    before = len(_chat_log())
    _capture("/script", {"model": "minimoi-mc-agent", "replace": True,
                         "steps": [{"chunks": [f"w{i} " for i in range(20)], "chunk_delay_ms": 700}]})
    _, res = _stream_side("stop")
    kinds = [e[:2] for e in res.get("events", [])]
    record("P2 Stop mid-stream: the relay aborts and the portal side gets 'stopped'",
           res.get("stopped") is True and kinds and kinds[-1] == ["Failure", "stopped"], json.dumps(kinds)[:300])
    time.sleep(3)
    calls = _chat_log()[before:]
    outcome = calls[-1].get("outcome") if calls else None
    record("P2 (finding): after Stop, OpenClaw's own call to the model endpoint was "
           + ("closed early (cancelled)" if outcome == "aborted" else f"left to run ({outcome})"), True,
           f"{len(calls)} call(s); the UI line assumes it may have finished until this says 'cancelled'")

    before = len(_chat_log())
    _capture("/script", {"model": "minimoi-mc-agent", "replace": True,
                         "steps": [{"chunks": [f"v{i} " for i in range(6)], "chunk_delay_ms": 500}]})
    corr, res = _stream_side("leave")
    time.sleep(6)
    calls = _chat_log()[before:]
    logs = docker("logs", RELAY).stdout
    left = [json.loads(line) for line in logs.splitlines() if corr in line and '"event":"stream"' in line]
    record("a caller that leaves mid-stream does not stop the run: MC's call finished and the relay read it to the end",
           bool(calls) and calls[-1].get("outcome") == "finished" and bool(left) and left[-1].get("caller_gone") is True,
           json.dumps({"outcome": calls[-1].get("outcome") if calls else None, "relay": left[-1] if left else None}))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mc-image", required=True)
    ap.add_argument("--portal-image", required=True)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="mcpb-"))
    mc_token, relay_token = secrets.token_hex(32), secrets.token_hex(32)
    env = {"MC_OPENCLAW_GATEWAY_TOKEN": mc_token, "MC_RELAY_TOKEN": relay_token,
           "MC_IMAGE_REPO": a.mc_image.split(":")[0], "MINIMOI_IMAGE_TAG": a.mc_image.split(":")[1],
           "MC_CONTAINER_NAME": MC, "MC_RELAY_CONTAINER_NAME": RELAY, "MC_NET_NAME": NET_MC, "MC_FRONT_NAME": NET_FRONT,
           "MC_STATE_VOLUME": VOLS[0], "MC_AUTH_VOLUME": VOLS[1]}
    # This branch's relay (streaming S1), mounted over the image's copy.
    override = tmp / "relay-override.yml"
    override.write_text("services:\n  mc-relay:\n    volumes:\n"
                        f"      - {REPO / 'docker' / 'mc-agent' / 'relay.mjs'}:/opt/minimoi/mc-agent/relay.mjs:ro\n")
    compose = lambda *args: sh("docker", "compose", "-p", P, "-f", str(REPO / "docker-compose.mc.yml"),  # noqa: E731
                               "-f", str(override), *args, env=env)

    def cleanup():
        compose("down")
        for n in (MC, RELAY, CAP, PORTAL):
            docker("rm", "-f", n)
        for v in VOLS:
            docker("volume", "rm", "-f", v)
        for n in (NET_MC, NET_FRONT):
            docker("network", "rm", n)

    try:
        cleanup()
        for n in (NET_MC, NET_FRONT):
            docker("network", "create", "--internal", *ISOLATED, n, check=True)
        for v in VOLS:
            docker("volume", "create", v, check=True)
        docker("run", "-d", "--name", CAP, "--network", NET_MC, "--network-alias", "model-gateway", "--memory", "128m",
               "-e", "CAPTURE_MC_KEY=mc-placeholder-not-a-key", "-v", f"{HERE}:/probe:ro", "--entrypoint", "node",
               a.mc_image, "/probe/capture-server.mjs", check=True)
        docker("run", "-d", "--name", PORTAL, "--network", NET_FRONT, "--memory", "192m", "-e", f"MC_RUNTIME_TOKEN={relay_token}",
               "-v", f"{REPO}:/src:ro", "--entrypoint", "sh", a.portal_image, "-c", "while :; do sleep 3600; done", check=True)
        r = compose("up", "-d", "--no-build")
        if r.returncode != 0:
            raise RuntimeError(r.stderr[-400:])
        t0 = time.time()
        for _ in range(330):
            if docker("exec", MC, "cat", "/tmp/minimoi-mc/state").stdout.strip() == "serving":
                break
            time.sleep(2)
        record("MC and the relay start in MC's own project; MC serving after its self-check",
               docker("exec", MC, "cat", "/tmp/minimoi-mc/state").stdout.strip() == "serving", f"{round(time.time() - t0)}s")

        # The streaming probes first: the refused-key checks below make OpenClaw
        # disable MC's key for about a minute, which every later turn would hit.
        stream_probes(a)

        def portal_side(*ids):
            out = docker("exec", PORTAL, "python", "-c", PORTAL_SIDE, *ids, timeout=300)
            try:
                return json.loads(out.stdout.strip().splitlines()[-1])
            except (json.JSONDecodeError, IndexError):
                return {"error": (out.stderr or out.stdout)[-400:]}

        corr, corr2 = secrets.token_hex(16), secrets.token_hex(16)
        # The capture server answers the first MC request with text, the next ones with a 401 (the staging placeholder case).
        docker("exec", CAP, "node", "-e", "fetch('http://127.0.0.1:4001/script',{method:'POST',body:JSON.stringify({model:'minimoi-mc-agent',"
               "steps:[{text:'Item 12 is in build.'},{status:401},{status:401},{status:401},{status:401},{status:401},{status:401},{status:401},{status:401}]})})")
        res = portal_side(corr, corr2)
        record("before any answer: unavailable · connected, no answer yet (never live)",
               res.get("before") == ["unavailable", "not_verified", True, "Master Craftsman is unavailable · connected, no answer yet"],
               json.dumps(res.get("before")))
        turn = res.get("turn") or []
        record("a synthetic turn through the relay is answered, with its correlation id echoed and a runtime response id",
               turn == ["answered", None, "Item 12 is in build.", True, True], json.dumps(turn))
        record("live only after that answer", (res.get("after") or [None])[0] == "ready", json.dumps(res.get("after")))
        record("a refused model key (the staging case) shows 'unavailable · its model key was refused'",
               res.get("refused") == ["unavailable", "key_refused", "Master Craftsman is unavailable · its model key was refused"],
               json.dumps(res.get("refused")))
        log = json.loads(docker("exec", CAP, "node", "-e", "fetch('http://127.0.0.1:4001/log').then(r=>r.text()).then(t=>console.log(t))").stdout or "[]")
        chat = [e for e in log if e["path"].endswith("/chat/completions")]
        record("every model request came from MC on MC's key, and the stand-in endpoint was the only one (no provider)",
               chat and all(e["key"] == "mc-key" and e["model"] == "minimoi-mc-agent" for e in chat),
               f"{len(chat)} requests")
        # Error surface (#251 review, stage C entry): an upstream budget refusal, a
        # 500 and a 429 must reach the Shop floor as failures, never as a 200
        # answer carrying error text. OpenClaw retries transient errors, so each
        # case is scripted many times.
        for label, step, want in (
                ("budget 400", {"status": 400, "type": "budget_exceeded", "message": "Budget has been exceeded! Current cost: 15.2, Max budget: 15.0"}, "cap_reached"),
                ("upstream 500", {"status": 500, "type": "internal_error", "message": "upstream exploded"}, None),
                ("upstream 429", {"status": 429, "type": "rate_limit", "message": "Rate limit reached for rpm 10"}, None)):
            docker("exec", CAP, "node", "-e", "fetch('http://127.0.0.1:4001/script',{method:'POST',body:JSON.stringify({model:'minimoi-mc-agent',replace:true,"
                   "steps:Array(12).fill(" + json.dumps(step) + ")})})")
            out = docker("exec", PORTAL, "python", "-c", ERROR_SURFACE, secrets.token_hex(16), timeout=300)
            try:
                got = json.loads(out.stdout.strip().splitlines()[-1])
            except (json.JSONDecodeError, IndexError):
                got = {"error": (out.stderr or out.stdout)[-300:]}
            ok = got.get("status") != "answered" and got.get("text") is None
            record(f"error surface: an upstream {label} reaches the Shop floor as a failure, never an answer",
                   ok, json.dumps(got))
            if want and got.get("failure_class") != want:
                record(f"FINDING (informational): OpenClaw 9.6 collapses an upstream {label} into its own 500 'internal "
                       f"error', so the Shop floor cannot name it ({want}); it shows the honest generic failure", True,
                       f"class {got.get('failure_class')}")
        rules = json.loads(docker("exec", PORTAL, "python", "-c", RELAY_RULES).stdout.strip() or "{}")
        want = {"readyz": 200, "no_token": 401, "tools_invoke": 403, "embeddings": 403, "models": 403,
                "alias_default": 403, "alias_cos": 403, "stream_string": 403, "stream_options": 403, "stop_unknown": 404}
        record("from the portal side the relay passes only readiness and allowed turns", rules == want, json.dumps(rules))
        portal_ip = json.loads(docker("inspect", "-f", "{{json .NetworkSettings.Networks}}", PORTAL).stdout)[NET_FRONT]["IPAddress"]
        from_mc = json.loads(docker("exec", MC, "node", "-e", FROM_MC % {"portal": PORTAL, "portal_ip": portal_ip}).stdout.strip() or "{}")
        record("one way: MC cannot resolve or reach the portal side, and the relay refuses MC (no caller token)",
               from_mc.get("portal_side_dns") in ("ENOTFOUND", "EAI_AGAIN") and from_mc.get("portal_side_ip") in ("ENETUNREACH", "EHOSTUNREACH", "timeout")
               and from_mc.get("relay_without_caller_token") == 401, json.dumps(from_mc))
        logs = docker("logs", RELAY).stdout + docker("logs", RELAY).stderr
        record("the relay logged the correlation ids and no token or note text",
               corr in logs and corr2 in logs and mc_token not in logs and relay_token not in logs and "stuck on" not in logs)
        nets = docker("inspect", "-f", "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}", MC).stdout.split()
        record("MC is only on mc-net (never on the portal's side)", nets == [NET_MC], " ".join(nets))
    finally:
        out = a.out or Path(tempfile.mkdtemp(prefix="mc-stage-b-"))
        out.mkdir(parents=True, exist_ok=True)
        (out / "results.json").write_text(json.dumps(RESULTS, indent=2))
        if os.environ.get("MCPB_KEEP") == "1":          # debugging only: leave the throwaway stack up
            print(f"MCPB_KEEP=1: containers left running (project {P}); remove with docker compose -p {P} down")
        else:
            cleanup()
        shutil.rmtree(tmp, ignore_errors=True)
    failed = [r for r in RESULTS if not r["pass"]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())

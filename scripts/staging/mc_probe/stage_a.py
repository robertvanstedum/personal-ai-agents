#!/usr/bin/env python3
"""No-spend stage A probe for Master Craftsman's own container (MC spec v0.8
§3, v0.9 §3-§5, §7): boundary tests C1-C8, independence from CoS, start time
and memory. Runs THROWAWAY containers only; staging is never touched (its
containers' IPs are read, read-only, as C3 targets).

  python3 scripts/staging/mc_probe/stage_a.py --mc-image IMAGE:TAG \\
      --cos-image minimoi-staging/cos-scheduler:agent-a-<sha7> \\
      --gateway-image minimoi-staging/cos-scheduler:model-gateway-<sha7> [--out DIR]

Build the MC image first (as mc.sh build does):
  docker build -f docker/Dockerfile.mc-agent -t mc-probe/mc-agent:dev .

Setup: internal networks mcp-default and mcp-mc (no egress anywhere); MC runs
from the real docker-compose.mc.yml under project mcp-mc with throwaway names;
random tokens and placeholder keys in a mode-600 temp file, never printed; no
provider exists. Phase 1: a real LiteLLM gateway (the staging gateway image,
placeholder master key) on both networks, stand-ins for postgres,
cos-scheduler and portal. Phase 2: the gateway is replaced by the capture
server (so both agents can "answer" with no provider), and a real CoS Agent A
probe runs beside MC. Memory limits and oom_score_adj 1000 on every probe.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
P = "mcp"
NET_DEFAULT, NET_MC = f"{P}-default", f"{P}-mc"
MC_PROJECT, MC = f"{P}-mc", f"{P}-mc-agent"
MC_VOLS = (f"{P}-mc-state", f"{P}-mc-auth")
GW, COS, STANDINS, CLIENT_DEF, CLIENT_MC, CAP = (f"{P}-gateway", f"{P}-cos-agent-a", f"{P}-standins",
                                                 f"{P}-client-default", f"{P}-client-mc", f"{P}-capture")
RESULTS: list[dict] = []
COS_ENV_NAMES = {"MINIMOI_MODEL_GATEWAY_KEY", "COS_AGENT_A_GATEWAY_TOKEN", "ANTHROPIC_API_KEY", "XAI_API_KEY",
                 "OPENAI_API_KEY", "DATABASE_URL", "LITELLM_MASTER_KEY", "MINIMOI_MODEL_GATEWAY_RECEIPT_KEY"}


def record(gate, check, ok, detail=""):
    RESULTS.append({"gate": gate, "check": check, "pass": bool(ok), "detail": detail})
    print(f"  {'PASS' if ok else 'FAIL'} ({gate}) {check}" + (f" — {detail}" if detail else ""), flush=True)


def sh(*cmd, check=False, timeout=900, env=None):
    return subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout, check=check,
                          env={**os.environ, **(env or {})})


def docker(*args, **kw):
    return sh("docker", *args, **kw)


def started_at(name):
    return docker("inspect", "-f", "{{.State.StartedAt}}", name).stdout.strip()


def running(name):
    return docker("inspect", "-f", "{{.State.Running}}", name).stdout.strip() == "true"


def mc_state(name=MC):
    return docker("exec", name, "cat", "/tmp/minimoi-mc/state").stdout.strip()


def wait_for(fn, timeout, step=2.0):
    end = time.time() + timeout
    value = None
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(step)
    return value


def in_mc(js, name=MC, timeout=60):
    """Run a node snippet inside MC; returns stdout (one JSON line)."""
    r = docker("exec", name, "node", "-e", js, timeout=timeout)
    return (r.stdout or "").strip().splitlines()[-1] if r.stdout.strip() else json.dumps({"error": r.stderr[-200:]})


PROBE_JS = r"""
const net = require('net'), dns = require('dns');
const targets = %s;
const one = (t) => new Promise((res) => {
  if (t.kind === 'dns') return dns.lookup(t.host, (e, a) => res({ ...t, ok: !e, detail: e ? e.code : a }));
  if (t.kind === 'https') return fetch('https://' + t.host, { signal: AbortSignal.timeout(6000) })
    .then(r => res({ ...t, ok: true, detail: r.status }), e => res({ ...t, ok: false, detail: (e.cause && e.cause.code) || e.name }));
  const s = net.connect({ host: t.host, port: t.port, timeout: 4000 });
  s.on('connect', () => { s.destroy(); res({ ...t, ok: true, detail: 'connected' }); });
  s.on('timeout', () => { s.destroy(); res({ ...t, ok: false, detail: 'timeout' }); });
  s.on('error', (e) => res({ ...t, ok: false, detail: e.code }));
});
Promise.all(targets.map(one)).then(r => console.log(JSON.stringify(r)));
"""


def reach(targets, name=MC):
    out = in_mc(PROBE_JS % json.dumps(targets), name, timeout=120)
    try:
        value = json.loads(out)
    except json.JSONDecodeError:
        value = None
    return value if isinstance(value, list) else [{"error": str(out)[:300]}]


HTTP_JS = r"""
const reqs = %s;
Promise.all(reqs.map(q => fetch('http://model-gateway:4000' + q.path, { method: q.method || 'GET',
  headers: Object.assign({ 'content-type': 'application/json' }, q.key ? { authorization: 'Bearer ' + process.env.MC_MODEL_GATEWAY_KEY } : {}),
  body: q.body ? JSON.stringify(q.body) : undefined, signal: AbortSignal.timeout(15000) })
  .then(async r => { let type = ''; try { type = ((await r.json()).error || {}).type || ''; } catch (e) {}
    return { path: q.path, key: !!q.key, status: r.status, type }; }, e => ({ path: q.path, key: !!q.key, status: 'error:' + ((e.cause && e.cause.code) || e.name) }))))
  .then(r => console.log(JSON.stringify(r)));
"""


class Probe:
    def __init__(self, a):
        self.a = a
        self.tmp = Path(tempfile.mkdtemp(prefix="mcp-"))
        os.chmod(self.tmp, 0o700)
        self.share = Path(tempfile.mkdtemp(prefix=".mcp-share-", dir=Path.home()))   # visible to Colima
        self.mc_token = secrets.token_hex(32)
        self.cos_token = secrets.token_hex(32)
        self.cos_key = "cos-probe-key-" + secrets.token_hex(8)
        self.master = "probe-master-" + secrets.token_hex(8)
        self.mc_env = {"MC_OPENCLAW_GATEWAY_TOKEN": self.mc_token, "MC_IMAGE_REPO": a.mc_image.split(":")[0],
                       "MINIMOI_IMAGE_TAG": a.mc_image.split(":")[1], "MC_CONTAINER_NAME": MC, "MC_NET_NAME": NET_MC,
                       "MC_STATE_VOLUME": MC_VOLS[0], "MC_AUTH_VOLUME": MC_VOLS[1]}
        self.env_file = self.tmp / "cos.env"
        self.env_file.write_text(f"OPENCLAW_GATEWAY_TOKEN={self.cos_token}\nMINIMOI_MODEL_GATEWAY_KEY={self.cos_key}\n")
        os.chmod(self.env_file, 0o600)
        self.stats: dict[str, list[float]] = {}

    # ── lifecycle ─────────────────────────────────────────────────────────────
    def mc_compose(self, *args):
        return sh("docker", "compose", "-p", MC_PROJECT, "-f", str(REPO / "docker-compose.mc.yml"), *args,
                  env=self.mc_env, timeout=600)

    def cleanup(self):
        self.mc_compose("down")
        for name in (MC, f"{MC}-verdict", f"{MC}-oom", GW, COS, STANDINS, CLIENT_DEF, CLIENT_MC, CAP):
            docker("rm", "-f", name)
        for v in (*MC_VOLS, f"{P}-mc-verdict", f"{P}-mc-oom", f"{P}-cos-state", f"{P}-cos-auth"):
            docker("volume", "rm", "-f", v)
        for n in (NET_DEFAULT, NET_MC):
            docker("network", "rm", n)

    def setup(self):
        self.cleanup()
        docker("network", "create", "--internal", NET_DEFAULT, check=True)
        # The same options as staging's mc-net: internal and no host address on the bridge.
        docker("network", "create", "--internal", "-o", "com.docker.network.bridge.gateway_mode_ipv4=isolated",
               NET_MC, check=True)
        for v in (*MC_VOLS, f"{P}-cos-state", f"{P}-cos-auth"):
            docker("volume", "create", v, check=True)
        standin = ("const net=require('net');for(const p of [5432,8769,5001,18789])"
                   "net.createServer(s=>s.end('standin\\n')).listen(p);")
        docker("run", "-d", "--name", STANDINS, "--network", NET_DEFAULT, "--network-alias", "postgres",
               "--network-alias", "cos-scheduler", "--network-alias", "portal", "--network-alias", "cos-agent-a",
               "--memory", "64m", "--entrypoint", "node", self.a.mc_image, "-e", standin, check=True)
        docker("run", "-d", "--name", CLIENT_MC, "--network", NET_MC, "--memory", "128m", "--entrypoint", "sh",
               "-v", f"{HERE}:/probe:ro", "-e", f"OPENCLAW_GATEWAY_TOKEN={self.mc_token}", self.a.mc_image,
               "-c", "while :; do sleep 3600; done", check=True)
        docker("run", "-d", "--name", CLIENT_DEF, "--network", NET_DEFAULT, "--memory", "128m", "--entrypoint", "sh",
               "-v", f"{HERE}:/probe:ro", "--env-file", str(self.env_file), self.a.mc_image,
               "-c", "while :; do sleep 3600; done", check=True)

    def start_gateway(self):
        cfg = self.share / "litellm.staging.yaml"
        shutil.copy(REPO / "services/model_gateway/litellm.staging.yaml", cfg)
        os.chmod(cfg, 0o644)
        docker("run", "-d", "--name", GW, "--network", NET_DEFAULT, "--network-alias", "model-gateway",
               "--memory", "900m", "--oom-score-adj", "1000", "-v", f"{cfg}:/app/config.yaml:ro",
               "-e", f"LITELLM_MASTER_KEY={self.master}", "-e", "XAI_API_KEY=probe-not-a-key",
               "-e", "ANTHROPIC_API_KEY=probe-not-a-key",
               "-e", "MINIMOI_RECEIPT_ENDPOINT=http://cos-scheduler:8769/internal/model-gateway/receipt",
               "-e", "MINIMOI_RECEIPT_KEY=probe-not-a-key", self.a.gateway_image,
               "--config", "/app/config.yaml", "--port", "4000", "--num_workers", "1", check=True)
        docker("network", "connect", "--alias", "model-gateway", NET_MC, GW, check=True)

    def mc_up(self):
        t0 = time.time()
        r = self.mc_compose("up", "-d", "--no-build")
        if r.returncode != 0:
            raise RuntimeError(r.stderr[-400:])
        state = wait_for(lambda: (lambda s: s if s in ("serving", "selfcheck-failed", "check-inconclusive") else "")(mc_state()), 660)
        return state, round(time.time() - t0)

    def sample(self, names, stop):
        while not stop.is_set():
            r = docker("stats", "--no-stream", "--format", "{{.Name}} {{.MemUsage}}", *names)
            for line in r.stdout.splitlines():
                name, usage = line.split(" ", 1)
                value = usage.split("/")[0].strip()
                mib = float(value[:-3]) * (1024 if value.endswith("GiB") else 1) if value.endswith(("MiB", "GiB")) else 0
                self.stats.setdefault(name, []).append(mib)
            time.sleep(1)

    # ── phase 1 ───────────────────────────────────────────────────────────────
    def phase1(self):
        print("== phase 1: MC with a real LiteLLM gateway (placeholder master key, no provider)", flush=True)
        self.start_gateway()
        wait_for(lambda: docker("exec", GW, "python", "-c",
                                "import urllib.request;urllib.request.urlopen('http://127.0.0.1:4000/health/liveliness',timeout=3)").returncode == 0, 240)
        gw_started = started_at(GW)
        state, secs = self.mc_up()
        record("A", "MC starts alone (no CoS running in the probe) and passes its self-check", state == "serving",
               f"state={state} in {secs}s")
        self.start_seconds_alone = secs
        self.idle_alone = docker("stats", "--no-stream", "--format", "{{.MemUsage}}", MC).stdout.strip()

        # C1
        mounts = json.loads(docker("inspect", "-f", "{{json .Mounts}}", MC).stdout)
        kinds = sorted((m["Type"], m.get("Name") or m.get("Source"), m["Destination"]) for m in mounts)
        record("C1", "only MC's own two volumes; no bind, socket, repository or CoS volume",
               kinds == sorted([("volume", MC_VOLS[0], "/home/node/.openclaw"), ("volume", MC_VOLS[1], "/home/node/.config/openclaw")]),
               json.dumps(kinds))
        # C2
        env = docker("inspect", "-f", "{{json .Config.Env}}", MC).stdout
        names = {e.split("=", 1)[0] for e in json.loads(env)}
        record("C2", "no CoS or provider credential name in MC's environment", not names & COS_ENV_NAMES,
               f"{sorted(names & COS_ENV_NAMES)}")
        values = {e.split("=", 1)[0]: e.split("=", 1)[1] for e in json.loads(env)}
        h = lambda v: hashlib.sha256(v.encode()).hexdigest()  # noqa: E731
        differs = h(values["MC_MODEL_GATEWAY_KEY"]) not in {h(self.cos_key), h(self.cos_token), h(self.master)} \
            and h(values["OPENCLAW_GATEWAY_TOKEN"]) not in {h(self.cos_token), h(self.cos_key), h(self.master)}
        record("C2", "MC's key and token differ from CoS's key, CoS's token and the gateway master key (hashes)", differs, "differs")
        # C3 + C7(A)
        st = json.loads(docker("network", "inspect", NET_DEFAULT, "-f", "{{json .Containers}}").stdout)
        standin_ip = next(c["IPv4Address"].split("/")[0] for c in st.values() if c["Name"] == STANDINS)
        staging = json.loads(docker("network", "inspect", "minimoi-staging_default", "-f", "{{json .Containers}}").stdout or "{}")
        staging_ips = {c["Name"]: c["IPv4Address"].split("/")[0] for c in staging.values()}
        targets = [{"kind": "tcp", "host": h_, "port": p} for h_, p in
                   (("cos-agent-a", 18789), ("postgres", 5432), ("cos-scheduler", 8769), ("portal", 5001))]
        targets += [{"kind": "tcp", "host": standin_ip, "port": p} for p in (18789, 5432, 8769, 5001)]
        for name, port in (("minimoi-cos-agent-a", 18789), ("postgres-ai-agents", 5432),
                           ("minimoi-cos-scheduler", 8769), ("minimoi-portal", 5001)):
            if name in staging_ips:
                targets.append({"kind": "tcp", "host": staging_ips[name], "port": port, "label": name})
        res = reach(targets)
        reached = [r for r in res if r.get("ok") or "error" in r]
        record("C3", "no route to CoS, Postgres, cos-scheduler or the portal, by name or by IP (probe stand-ins and staging's own IPs)",
               not reached, f"{len(res)} targets; reached: {reached}")
        record("C7", "stage A: the portal is unreachable from MC (name and IP); the relay is stage B",
               all(not r.get("ok") for r in res if r.get("host") in ("portal",) or r.get("label") == "minimoi-portal" or r.get("port") == 5001))
        # C4
        vm_ip = self.a.vm_ip
        targets = [{"kind": "dns", "host": "example.com"}, {"kind": "https", "host": "example.com"},
                   {"kind": "tcp", "host": "host.docker.internal", "port": 443}]
        # mc-net's own bridge: with gateway_mode_ipv4=isolated it has no address;
        # test the IPAM gateway if one exists, and the subnet's first host either way.
        ipam = json.loads(docker("network", "inspect", NET_MC, "-f", "{{json .IPAM.Config}}").stdout or "[]")
        bridge_ips = sorted({c.get("Gateway") for c in ipam if c.get("Gateway")} |
                            {c["Subnet"].split("/")[0].rsplit(".", 1)[0] + ".1" for c in ipam if c.get("Subnet")})
        self.bridge_ips = bridge_ips
        for ip in ("172.17.0.1", vm_ip, "192.168.5.2", *bridge_ips):
            for port in (22, 53, 5001, 5432, 14000, 18790):
                targets.append({"kind": "tcp", "host": ip, "port": port})
        res = reach(targets)
        leaks = [r for r in res if r.get("ok") or "error" in r]
        record("C4", "no outbound path: public DNS, HTTPS, host.docker.internal, docker0, the VM's addresses and mc-net's own bridge "
               f"({', '.join(bridge_ips)}) on 22/53/5001/5432/14000/18790", not leaks, f"{len(res)} targets; reached: {leaks}")
        opts = json.loads(docker("network", "inspect", NET_MC, "-f", "{{json .Options}}").stdout or "{}")
        record("C4", "mc-net's bridge has no host address (gateway_mode_ipv4=isolated)",
               opts.get("com.docker.network.bridge.gateway_mode_ipv4") == "isolated"
               and not any(c.get("Gateway") for c in ipam), json.dumps({"options": opts, "ipam": ipam}))
        # Positive control: the refusals below come from authentication, not a broken gateway.
        ok = docker("exec", GW, "python", "-c", "import urllib.request,os,json;r=urllib.request.urlopen(urllib.request.Request("
                    "'http://127.0.0.1:4000/v1/models',headers={'Authorization':'Bearer '+os.environ['LITELLM_MASTER_KEY']}),timeout=10);"
                    "print(r.status, 'minimoi-cos-agent' in r.read().decode())")
        record("C5", "positive control: the master key gets 200 on /v1/models listing minimoi-cos-agent",
               ok.stdout.strip() == "200 True", ok.stdout.strip() or ok.stderr[-200:])
        # C5
        mc_reqs = [{"path": p, "key": True} for p in ("/v1/models", "/health", "/model/info", "/spend/logs", "/key/list",
                                                     "/key/info")]
        mc_reqs += [{"path": "/v1/embeddings", "method": "POST", "key": True, "body": {"model": "minimoi-mc-agent", "input": "x"}},
                    {"path": "/v1/responses", "method": "POST", "key": True, "body": {"model": "minimoi-cos-web-search", "input": "x"}},
                    {"path": "/v1/messages", "method": "POST", "key": True,
                     "body": {"model": "minimoi-mc-agent", "max_tokens": 1, "messages": [{"role": "user", "content": "x"}]}},
                    {"path": "/openai/v1/chat/completions", "method": "POST", "key": True,
                     "body": {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "x"}]}},
                    {"path": "/key/generate", "method": "POST", "key": True, "body": {}}]
        mc_reqs += [{"path": "/v1/chat/completions", "method": "POST", "key": True,
                     "body": {"model": m, "messages": [{"role": "user", "content": "x"}]}} for m in
                    ("minimoi-mc-agent", "minimoi-cos-agent", "minimoi-cos-web-search")]
        mc_reqs += [{"path": "/v1/chat/completions", "method": "POST", "key": True,
                     "body": {"model": "minimoi-mc-agent", "api_base": "http://cos-agent-a:18789/v1",
                              "messages": [{"role": "user", "content": "x"}]}},
                    {"path": "/anthropic/v1/messages", "method": "POST", "key": True,
                     "body": {"model": "claude-haiku-4-5-20251001", "max_tokens": 1, "messages": [{"role": "user", "content": "x"}]}},
                    {"path": "/v1/models"}]
        live = json.loads(in_mc(HTTP_JS % json.dumps([{"path": "/health/liveliness"}])))
        record("C5", "MC reaches the gateway (liveliness 200)", live and live[0]["status"] == 200, json.dumps(live))
        res = json.loads(in_mc(HTTP_JS % json.dumps(mc_reqs), timeout=180))
        # Refused = 401/403, or LiteLLM's 400 "no_db_connection": with no key
        # database (stage A) a non-master key cannot even be looked up, so it
        # is refused before routing. Anything else fails.
        refused = lambda r: r["status"] in (401, 403) or (r["status"] == 400 and r.get("type") == "no_db_connection")  # noqa: E731
        bad = [r for r in res if not refused(r)]
        kinds = sorted({f"{r['status']} {r.get('type')}" for r in res})
        record("C5", "stage-A placeholder key and no key: refused on every route, pass-through and CoS route (anything else fails)",
               not bad, f"{len(res)} requests, answers {kinds}; not refused: {bad}")
        # C6 (gateway side)
        docker("restart", "-t", "30", MC)
        wait_for(lambda: mc_state() == "serving", 660)
        docker("kill", MC)
        time.sleep(3)
        self.mc_compose("up", "-d", "--no-build")
        wait_for(lambda: mc_state() == "serving", 660)
        record("C6", "the gateway's StartedAt is unchanged while MC starts, restarts, is killed and restarted",
               started_at(GW) == gw_started, gw_started)
        # C8: verdict
        docker("volume", "create", f"{P}-mc-verdict")
        docker("run", "-d", "--name", f"{MC}-verdict", "--network", NET_MC, "--memory", "1200m", "--oom-score-adj", "1000",
               "--restart", "on-failure:3", "--cap-drop", "ALL", "-e", f"OPENCLAW_GATEWAY_TOKEN={self.mc_token}",
               "-e", "MC_MODEL_GATEWAY_KEY=mc-placeholder-not-a-key", "-e", "MINIMOI_MODEL_GATEWAY_KEY=injected-cos-name",
               "-v", f"{P}-mc-verdict:/home/node/.openclaw", self.a.mc_image, check=True)
        wait_for(lambda: mc_state(f"{MC}-verdict") == "selfcheck-failed", 180)
        time.sleep(30)
        info = docker("inspect", "-f", "{{.State.Running}} {{.RestartCount}}", f"{MC}-verdict").stdout.strip()
        record("C8", "a failed self-check (verdict) stays up, never serves, never restart-loops",
               mc_state(f"{MC}-verdict") == "selfcheck-failed" and info == "true 0", f"running/restarts={info}")
        docker("rm", "-f", f"{MC}-verdict")
        # C8: OOM
        docker("volume", "create", f"{P}-mc-oom")
        docker("run", "-d", "--name", f"{MC}-oom", "--network", NET_MC, "--memory", "200m", "--memory-swap", "200m",
               "--oom-score-adj", "1000", "--restart", "on-failure:3", "--cap-drop", "ALL",
               "-e", f"OPENCLAW_GATEWAY_TOKEN={self.mc_token}", "-e", "MC_MODEL_GATEWAY_KEY=mc-placeholder-not-a-key",
               "-v", f"{P}-mc-oom:/home/node/.openclaw", self.a.mc_image, check=True)
        wait_for(lambda: not running(f"{MC}-oom") and docker("inspect", "-f", "{{.RestartCount}}", f"{MC}-oom").stdout.strip() == "3", 600, 5)
        info = docker("inspect", "-f", "{{.State.Running}} {{.RestartCount}} {{.State.OOMKilled}} {{.State.ExitCode}}", f"{MC}-oom").stdout.strip()
        record("C8", "an MC that keeps failing (OOM under 200m) restarts at most 3 times, then stays stopped",
               info.startswith("false 3"), f"running/restarts/oom/exit={info}")
        record("C8", "the gateway was untouched by MC's failures", started_at(GW) == gw_started)
        docker("rm", "-f", f"{MC}-oom", GW)

    # ── phase 2 ───────────────────────────────────────────────────────────────
    def phase2(self):
        print("== phase 2: a real CoS Agent A probe beside MC; the capture server stands in for the gateway", flush=True)
        self.mc_compose("down")
        docker("run", "-d", "--name", CAP, "--network", NET_DEFAULT, "--network-alias", "model-gateway", "--memory", "128m",
               "-e", f"CAPTURE_COS_KEY={self.cos_key}", "-e", "CAPTURE_MC_KEY=mc-placeholder-not-a-key",
               "-v", f"{HERE}:/probe:ro", "--entrypoint", "node", self.a.mc_image, "/probe/capture-server.mjs", check=True)
        docker("network", "connect", "--alias", "model-gateway", NET_MC, CAP, check=True)
        t0 = time.time()
        docker("run", "-d", "--name", COS, "--network", NET_DEFAULT, "--network-alias", "cos-agent-a", "--memory", "1400m",
               "--oom-score-adj", "1000", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
               "--env-file", str(self.env_file), "-e", "OPENCLAW_STATE_DIR=/home/node/.openclaw",
               "-e", "OPENCLAW_CONFIG_PATH=/home/node/.openclaw/openclaw.json", "-e", "OPENCLAW_NO_AUTO_UPDATE=1",
               "-e", "MALLOC_ARENA_MAX=2", "-v", f"{P}-cos-state:/home/node/.openclaw",
               "-v", f"{P}-cos-auth:/home/node/.config/openclaw", self.a.cos_image, check=True)
        ok = wait_for(lambda: docker("exec", COS, "curl", "-fsS", "-m", "3", "-o", "/dev/null",
                                     "http://127.0.0.1:18789/readyz").returncode == 0, 420)
        self.cos_start = round(time.time() - t0)
        record("A", "CoS Agent A probe starts with no MC running", bool(ok), f"{self.cos_start}s")
        cos_started = started_at(COS)
        # Staggered: MC starts after CoS is ready (mc.sh up waits for CoS healthy).
        state, secs = self.mc_up()
        self.start_seconds_beside_cos = secs
        record("A", "MC starts after CoS is ready (staggered) and passes its self-check", state == "serving", f"{secs}s")
        # Independence while MC restarts, is killed and taken down; CoS answers throughout.
        stop = threading.Event()
        cos_down = []

        def watch():
            while not stop.is_set():
                if docker("exec", COS, "curl", "-fsS", "-m", "3", "-o", "/dev/null", "http://127.0.0.1:18789/readyz").returncode != 0:
                    cos_down.append(time.strftime("%H:%M:%S"))
                time.sleep(2)
        t = threading.Thread(target=watch)
        t.start()
        docker("restart", "-t", "30", MC)
        wait_for(lambda: mc_state() == "serving", 660)
        docker("kill", MC)
        self.mc_compose("down")
        stop.set()
        t.join()
        record("C6", "CoS's StartedAt unchanged, and CoS ready throughout MC's restart, kill and down",
               started_at(COS) == cos_started and not cos_down, f"CoS not ready at: {cos_down or 'never'}")
        state, _ = self.mc_up()
        # C3 with the real CoS: by name and by IP.
        cos_ip = json.loads(docker("inspect", "-f", "{{json .NetworkSettings.Networks}}", COS).stdout)[NET_DEFAULT]["IPAddress"]
        res = reach([{"kind": "tcp", "host": "cos-agent-a", "port": 18789}, {"kind": "tcp", "host": cos_ip, "port": 18789}])
        record("C3", "MC cannot reach the real CoS Agent A probe (name and IP)",
               state == "serving" and all(not r.get("ok") and "error" not in r for r in res), f"MC {state}; {json.dumps(res)}")
        # Memory while each agent answers (capture server: no provider, no spend).
        # Guard: never push the shared VM (staging runs beside the probe) below ~0.5 GB.
        avail = int(sh("colima", "ssh", "--", "free", "-m").stdout.splitlines()[1].split()[-1]) if shutil.which("colima") else 9999
        if avail < 900:
            self.peaks, self.idle_both, self.vm = {}, docker("stats", "--no-stream", "--format", "{{.Name}} {{.MemUsage}}", MC, COS).stdout.strip(), f"{avail} MB available"
            record("memory", "idle measured; turns skipped: VM available memory too low for a safe peak measurement beside full staging", True,
                   f"MC alone idle {self.idle_alone}; beside CoS: {self.idle_both.replace(chr(10), '; ')}; VM {avail} MB available")
            return
        stop = threading.Event()
        sampler = threading.Thread(target=self.sample, args=([MC, COS], stop))
        sampler.start()
        time.sleep(3)
        for i in range(3):
            docker("exec", CLIENT_DEF, "node", "/probe/http.mjs", "POST", f"http://cos-agent-a:18789/v1/chat/completions",
                   json.dumps({"model": "openclaw/cos-agent-a", "user": f"probe-{i}", "messages": [{"role": "user", "content": "hello"}]}),
                   "--auth", "--timeout", "150000", timeout=200)
            docker("exec", CLIENT_MC, "node", "/probe/http.mjs", "POST", f"http://{MC}:18789/v1/chat/completions",
                   json.dumps({"model": "openclaw/mc-agent", "user": f"guild-mc:probe-{i}", "messages": [{"role": "user", "content": "hello"}]}),
                   "--auth", "--timeout", "150000", timeout=200)
        time.sleep(5)
        stop.set()
        sampler.join()
        log = json.loads(json.loads(docker("exec", CLIENT_MC, "node", "/probe/http.mjs", "GET", "http://model-gateway:4001/log",
                                            timeout=60).stdout.strip().splitlines()[-1])["body"])
        keys = sorted({(e["model"], e["key"]) for e in log if e["path"].endswith("/chat/completions")})
        record("A", "both agents answered through the capture server, each on its own route and key (no provider)",
               ("minimoi-mc-agent", "mc-key") in keys and ("minimoi-cos-agent", "cos-key") in keys and len(keys) == 2, json.dumps(keys))
        self.peaks = {n: round(max(v)) for n, v in self.stats.items() if v}
        self.idle_both = docker("stats", "--no-stream", "--format", "{{.Name}} {{.MemUsage}}", MC, COS).stdout.strip()
        self.vm = sh("colima", "ssh", "--", "free", "-m").stdout.splitlines()[1] if shutil.which("colima") else ""
        record("memory", "measured (informational)", True,
               f"MC alone idle {self.idle_alone}; beside CoS: {self.idle_both.replace(chr(10), '; ')}; "
               f"peaks while answering {self.peaks}; VM {self.vm}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mc-image", required=True)
    ap.add_argument("--cos-image", required=True)
    ap.add_argument("--gateway-image", required=True)
    ap.add_argument("--vm-ip", default="192.168.5.1")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    p = Probe(a)
    try:
        p.setup()
        p.phase1()
        p.phase2()
    finally:
        out = a.out or Path(tempfile.mkdtemp(prefix="mc-stage-a-"))
        out.mkdir(parents=True, exist_ok=True)
        (out / "results.json").write_text(json.dumps({
            "mc_image": a.mc_image, "start_s_alone": getattr(p, "start_seconds_alone", None),
            "start_s_beside_cos": getattr(p, "start_seconds_beside_cos", None), "cos_start_s": getattr(p, "cos_start", None),
            "peaks_mib": getattr(p, "peaks", None), "idle_mc_alone": getattr(p, "idle_alone", None),
            "idle_beside_cos": getattr(p, "idle_both", None), "vm": getattr(p, "vm", None), "results": RESULTS}, indent=2))
        if not a.keep:
            p.cleanup()
        shutil.rmtree(p.share, ignore_errors=True)
        shutil.rmtree(p.tmp, ignore_errors=True)
    failed = [r for r in RESULTS if not r["pass"]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed; results in {out / 'results.json'}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""No-spend isolation gates for Master Craftsman stage 0 / 1a (MC spec v0.7 §2.2).

Runs the combined CoS + MC image in THROWAWAY probe containers and never
touches staging:

* a fresh ``--internal`` Docker network (no egress, no published ports);
* a throwaway state volume, deleted afterwards;
* a random gateway token and placeholder model keys in a mode-600 temp file,
  deleted afterwards and never printed;
* the model gateway is a local capture server (capture-server.mjs) under the
  network alias ``model-gateway``: real OpenClaw turns run against a script,
  with no provider and no spend. No real provider host exists on the network;
* ``--memory`` on the probe (and a high oom_score_adj), so an OOM kills only
  the probe.

Usage (from any checkout, Docker running):
    python3 scripts/staging/mc_probe/gates.py --image mc-probe:stage1a [--memory 1200m] [--out DIR] [--keep]

Build the image first, e.g.
    docker build -f docker/Dockerfile.cos-agent-a -t mc-probe:stage1a --build-arg MINIMOI_RELEASE_SHA=$(git rev-parse HEAD) .

Gates (spec §2.2): (0) exact config, (a) effective tools, (a2) model
switching, (b) sessions, (c) workspace and memory, (d) crash and hang,
(f) key per request, (g) CoS request unchanged, plus the readiness gate
(Codex v0.7 finding 1) and N11's split by agent. Gates that need a real model
turn (paid) are listed as stage 1b and not run.
Exit status 0 only when every gate passes.
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
NET, PROBE, CAP, CLIENT, POLLER = "mc-probe-net", "mc-probe", "mc-probe-capture", "mc-probe-client", "mc-probe-poller"
VOLUME = "mc-probe-state"
COS, MC = "cos-agent-a", "mc-agent"
COS_ROUTE, MC_ROUTE = "minimoi-gateway/minimoi-cos-agent", "minimoi-gateway-mc/minimoi-mc-agent"
COS_MODEL, MC_MODEL = "minimoi-cos-agent", "minimoi-mc-agent"
COS_TOOLS, MC_TOOLS = ["session_status", "web_search"], ["session_status"]
SELFCHECK_KEY = "agent:{}:minimoi-selfcheck"
FORBIDDEN_SCHEMA = {"exec", "process", "code_execution", "read", "write", "edit", "apply_patch", "ls",
                    "memory_search", "memory_get", "sessions", "sessions_list", "sessions_history",
                    "sessions_search", "sessions_send", "sessions_spawn", "conversations_send",
                    "conversations_turn", "conversations_list", "agents_wait", "wait", "file_fetch",
                    "file_write", "dir_fetch", "dir_list", "web_fetch", "terminal", "computer"}
IMAGE_COMBINED = "/opt/minimoi/cos-agent-a/openclaw.cos-mc.json"
IMAGE_COS_ONLY = "/opt/minimoi/cos-agent-a/openclaw.json"
STATE_CONFIG = "/home/node/.openclaw/openclaw.json"

# Poller states that mean "nothing is listening on the LAN side" (the probe not
# up yet, or OpenClaw bound to loopback only). Anything else before the check
# passed would mean a caller reached an unchecked agent.
TOOL_SEARCH_TOOLS = {"tool_search", "tool_describe", "tool_call", "tool_search_code"}
NOTHING_LISTENING = {"refused", "ENOTFOUND", "EAI_AGAIN", "EHOSTUNREACH"}

RESULTS: list[dict] = []


def record(gate: str, check: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append({"gate": gate, "check": check, "pass": bool(ok), "detail": detail})
    print(f"  {'PASS' if ok else 'FAIL'} ({gate}) {check}" + (f" — {detail}" if detail else ""), flush=True)
    return ok


def run(cmd, check=False, timeout=600, input_text=None):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=check, input=input_text)


def docker(*args, check=False, timeout=600):
    return run(["docker", *args], check=check, timeout=timeout)


# ── probe environment ─────────────────────────────────────────────────────────

class Env:
    def __init__(self, image: str, memory: str, out: Path):
        self.image, self.memory, self.out = image, memory, out
        self.tmp = Path(tempfile.mkdtemp(prefix="mc-probe-"))
        os.chmod(self.tmp, 0o700)
        self.cos_key = "cos-probe-placeholder-" + secrets.token_hex(8)
        self.mc_key = "mc-placeholder-not-a-key-" + secrets.token_hex(8)
        self.env_file = self.tmp / "probe.env"
        self.env_file.write_text(
            f"OPENCLAW_GATEWAY_TOKEN={secrets.token_hex(32)}\n"
            f"MINIMOI_MODEL_GATEWAY_KEY={self.cos_key}\n"
            f"MC_MODEL_GATEWAY_KEY={self.mc_key}\n")
        os.chmod(self.env_file, 0o600)

    @staticmethod
    def remove_containers():
        for name in (POLLER, PROBE, CAP, CLIENT):
            docker("rm", "-f", name)
        for volume in (VOLUME, VOLUME + "-variant"):
            docker("volume", "rm", "-f", volume)
        docker("network", "rm", NET)

    def cleanup(self):
        self.remove_containers()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def setup(self):
        self.remove_containers()
        docker("network", "create", "--internal", NET, check=True)
        docker("volume", "create", VOLUME, check=True)
        docker("run", "-d", "--name", CAP, "--network", NET, "--network-alias", "model-gateway",
               "--memory", "160m", "-e", f"CAPTURE_COS_KEY={self.cos_key}", "-e", f"CAPTURE_MC_KEY={self.mc_key}",
               "-v", f"{HERE}:/probe:ro", "--entrypoint", "node", self.image, "/probe/capture-server.mjs", check=True)
        docker("run", "-d", "--name", CLIENT, "--network", NET, "--memory", "256m",
               "--env-file", str(self.env_file), "-v", f"{HERE}:/probe:ro", "--entrypoint", "sh", self.image,
               "-c", "while :; do sleep 3600; done", check=True)

    def start_probe(self, *, entrypoint="start-with-mc", extra=(), volume=VOLUME):
        docker("rm", "-f", PROBE)
        args = ["run", "-d", "--name", PROBE, "--network", NET, "--memory", self.memory, "--memory-swap", self.memory,
                "--oom-score-adj", "1000", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                "--no-healthcheck", "--env-file", str(self.env_file),
                "-e", "OPENCLAW_STATE_DIR=/home/node/.openclaw", "-e", f"OPENCLAW_CONFIG_PATH={STATE_CONFIG}",
                "-e", "OPENCLAW_DISABLE_BONJOUR=1", "-e", "OPENCLAW_NO_AUTO_UPDATE=1", "-e", "MALLOC_ARENA_MAX=2",
                "-v", f"{volume}:/home/node/.openclaw", *extra]
        if entrypoint == "start-with-mc":
            args += ["--entrypoint", "tini", self.image, "-g", "--", "/opt/minimoi/cos-agent-a/start-with-mc.sh"]
        elif entrypoint == "raw":   # bypass every start script: a variant config for negative controls
            args += ["--entrypoint", "sh", self.image, "-c",
                     f"cp /probe/variant.json {STATE_CONFIG} && exec node /app/openclaw.mjs gateway"]
        docker(*args, check=True)

    def start_poller(self, seconds=420):
        docker("rm", "-f", POLLER)
        docker("run", "-d", "--name", POLLER, "--network", NET, "--memory", "96m", "--env-file", str(self.env_file),
               "-v", f"{HERE}:/probe:ro", "--entrypoint", "node", self.image, "/probe/lan-poller.mjs",
               f"http://{PROBE}:18789/v1/models", str(seconds), check=True)

    def poller_log(self) -> list[tuple[str, str]]:
        docker("stop", "-t", "1", POLLER)
        lines = docker("logs", POLLER).stdout.splitlines()
        return [tuple(l.split(" ", 1)) for l in lines if " " in l]


def probe_file(path: str) -> str:
    r = docker("exec", PROBE, "cat", path)
    return r.stdout if r.returncode == 0 else ""


def wait_state(targets=("serving-",), fail=("failed",), timeout=420) -> str:
    end = time.time() + timeout
    state = ""
    while time.time() < end:
        state = probe_file("/tmp/minimoi-mc/state").strip()
        if any(state.startswith(t) for t in targets) or any(f in state for f in fail):
            return state
        running = docker("inspect", "-f", "{{.State.Running}}", PROBE).stdout.strip()
        if running != "true":
            return "exited"
        time.sleep(3)
    return state or "timeout"


def wait_ready_raw(timeout=300) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        r = docker("exec", PROBE, "curl", "-fsS", "-m", "3", "-o", "/dev/null", "http://127.0.0.1:18789/readyz")
        if r.returncode == 0:
            return True
        time.sleep(3)
    return False


def rpc(method: str, params: dict | None = None) -> dict:
    r = docker("exec", PROBE, "node", "/app/openclaw.mjs", "gateway", "call", method, "--timeout", "90000",
               "--params", json.dumps(params or {}), "--json", timeout=180)
    text = r.stdout
    start = text.find("{")
    try:
        return json.loads(text[start:]) if start >= 0 else {"_error": r.stderr[-300:]}
    except json.JSONDecodeError:
        return {"_error": text[-300:]}


def http(method: str, url: str, body=None, *, auth=True, timeout_ms=150000) -> dict:
    cmd = ["exec", CLIENT, "node", "/probe/http.mjs", method, url]
    if body is not None:
        cmd.append(json.dumps(body))
    if auth:
        cmd.append("--auth")
    cmd += ["--timeout", str(timeout_ms)]
    r = docker(*cmd, timeout=timeout_ms / 1000 + 60)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"status": "client-error", "body": r.stderr[-300:]}


def http_bg(method: str, url: str, body, *, timeout_ms: int) -> subprocess.Popen:
    cmd = ["docker", "exec", CLIENT, "node", "/probe/http.mjs", method, url, json.dumps(body), "--auth",
           "--timeout", str(timeout_ms)]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def bg_result(proc: subprocess.Popen, timeout=400) -> dict:
    out, _ = proc.communicate(timeout=timeout)
    try:
        return json.loads(out.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"status": "client-error"}


def turn(agent: str, text: str, user: str, timeout_ms=150000) -> dict:
    return http("POST", f"http://{PROBE}:18789/v1/chat/completions",
                {"model": f"openclaw/{agent}", "user": user, "messages": [{"role": "user", "content": text}]},
                timeout_ms=timeout_ms)


def cap(path: str, body=None) -> dict:
    method = "GET" if body is None and path == "/log" else "POST"
    r = http(method, f"http://model-gateway:4001{path}", body if body is not None else ({} if method == "POST" else None),
             auth=False, timeout_ms=20000)
    try:
        return json.loads(r.get("body") or "null")
    except json.JSONDecodeError:
        return {}


def cap_log() -> list[dict]:
    return cap("/log") or []


def cap_reset():
    cap("/reset")


def cap_script(model: str, steps: list[dict], replace: bool = False):
    cap("/script", {"model": model, "steps": steps, "replace": replace})


def invoke(tool: str, session_key: str, args=None) -> dict:
    return http("POST", f"http://{PROBE}:18789/tools/invoke",
                {"tool": tool, "args": args or {}, "sessionKey": session_key}, timeout_ms=120000)


def probe_log_since(marker_time: str) -> str:
    return docker("logs", "--since", marker_time, PROBE).stdout + docker("logs", "--since", marker_time, PROBE).stderr


def tool_ids(answer: dict) -> list[str]:
    return sorted(t.get("id") for g in answer.get("groups", []) for t in g.get("tools", []))


def messages_text(entry: dict) -> str:
    return entry.get("body") or ""


# ── gates ─────────────────────────────────────────────────────────────────────

def gate_readiness_and_exact_config(env: Env):
    print("== readiness gate (Codex v0.7 #1) and gate (0) exact config", flush=True)
    env.start_poller()
    env.start_probe()
    t0 = time.time()
    state = wait_state()
    env.startup_s = round(time.time() - t0)
    polled = env.poller_log()
    record("readiness", "combined start reaches serving-combined", state == "serving-combined",
           f"state={state}, {env.startup_s}s")
    checked = probe_file("/tmp/minimoi-mc/checked")
    checked_at = next((l.split("=", 1)[1] for l in checked.splitlines() if l.startswith("checked_at=")), "")
    first_ok = next((t for t, s in polled if s.startswith("http")), "")
    before = [s for t, s in polled if t < (checked_at or "9")]
    record("readiness", "no LAN request answered before the self-check passed",
           bool(checked_at) and all(s in NOTHING_LISTENING for s in before)
           and (not first_ok or first_ok[:19] >= checked_at[:19]),
           f"checked_at={checked_at}, first LAN answer={first_ok or 'none'}, states before: {sorted(set(before))}")
    early = [e for e in cap_log() if e["at"] < (checked_at or "9")]
    record("readiness", "no model request before the check passed", not early, f"{len(early)} early requests")

    r = docker("exec", PROBE, "node", "/app/openclaw.mjs", "config", "validate", "--json")
    try:
        v = json.loads(r.stdout[r.stdout.find("{"):])
    except json.JSONDecodeError:
        v = {}
    record("0", "config validate: valid, zero warnings", v.get("valid") is True and v.get("warnings") == [],
           f"valid={v.get('valid')}, warnings={len(v.get('warnings') or [])}")
    same = docker("exec", PROBE, "cmp", IMAGE_COMBINED, STATE_CONFIG).returncode == 0
    record("0", "state-volume config byte-identical to the image file after start (no rewrite)", same)
    for phase in ("static", "runtime"):
        res = json.loads(probe_file(f"/tmp/minimoi-mc/selfcheck-{phase}-combined.json") or "{}")
        record("0", f"self-check {phase}: CoS and MC sets as specified",
               res.get("cos", {}).get("ok") is True and res.get("mc", {}).get("ok") is True,
               json.dumps({k: res.get(k, {}).get("failures") for k in ("cos", "mc")}))


def gate_a_tools(env: Env):
    print("== gate (a) effective tools", flush=True)
    for agent, want in ((COS, COS_TOOLS), (MC, MC_TOOLS)):
        key = SELFCHECK_KEY.format(agent)
        rpc("sessions.create", {"agentId": agent, "key": key})
        got = tool_ids(rpc("tools.effective", {"sessionKey": key}))
        record("a", f"{agent} tools.effective == {want}", got == sorted(want), f"got {got}")
        catalog = sorted({t["id"] for g in rpc("tools.catalog", {"agentId": agent}).get("groups", [])
                          for t in g.get("tools", [])})
        wrong = []
        for tool in catalog + ["no_such_tool"]:
            status = invoke(tool, key).get("status")
            allowed = tool in want
            if allowed and status == 404:
                wrong.append(f"{tool}:404 (allowed tool unavailable)")
            if not allowed and status != 404:
                wrong.append(f"{tool}:{status}")
        record("a", f"{agent}: /tools/invoke 404 for every catalog tool outside its allow list",
               not wrong, f"{len(catalog)} catalog tools; unexpected: {wrong[:8]}")
    cap_reset()
    turn(MC, "hello, what tools do you have?", "guild-mc:gate-a")
    turn(COS, "hello from gate a", "cos-gate-a")
    log = cap_log()
    mc = [e for e in log if e["model"] == MC_MODEL]
    cos = [e for e in log if e["model"] == COS_MODEL]
    mc_tools = sorted({t for e in mc for t in e["tools"]})
    cos_tools = sorted({t for e in cos for t in e["tools"]})
    record("a", "MC turn's model-facing tool schemas == [session_status]", bool(mc) and mc_tools == MC_TOOLS, f"{mc_tools}")
    record("a", "CoS turn's model-facing tool schemas == [session_status, web_search]",
           bool(cos) and cos_tools == sorted(COS_TOOLS), f"{cos_tools}")
    bad = sorted((set(mc_tools) | set(cos_tools)) & FORBIDDEN_SCHEMA | {t for t in mc_tools + cos_tools if t.startswith("tool_search")})
    record("a", "no exec, wait, tool_search*, file, memory or session tool in any schema", not bad, f"{bad}")


def gate_a2_model_switch(env: Env):
    print("== gate (a2) model switching", flush=True)
    since = time.strftime("%Y-%m-%dT%H:%M:%S")
    cases = [(MC, COS_ROUTE), (MC, "minimoi-gateway-mc/anything-else"), (COS, MC_ROUTE), (COS, "minimoi-gateway/minimoi-cos-agent-anthropic")]
    for agent, target in cases:
        answer = invoke("session_status", SELFCHECK_KEY.format(agent), {"model": target})
        record("a2", f"{agent}: session_status model={target} refused (tool call path)",
               answer.get("status") != 200 or '"changedModel":true' not in answer.get("body", ""),
               f"HTTP {answer.get('status')}")
    logs = probe_log_since(since)
    record("a2", "gateway log names the refusals ('model not allowed')", logs.count("model not allowed") >= len(cases),
           f"{logs.count('model not allowed')} refusals logged")
    # The same switch requested BY THE MODEL inside a real turn (capture server scripts the tool call).
    for agent, model, target, user in ((MC, MC_MODEL, COS_ROUTE, "guild-mc:gate-a2"), (COS, COS_MODEL, MC_ROUTE, "cos-gate-a2")):
        cap_reset()
        cap_script(model, [{"tool": "session_status", "args": {"model": target}}, {"text": "switched?"}])
        turn(agent, "please switch your model", user)
        turn(agent, "and now a normal turn", user)
        log = cap_log()
        own = [e for e in log if e["model"] == model]
        tool_result = next((e for e in own if '"role":"tool"' in e["body"].replace(" ", "")), None)
        refused = tool_result is not None and "not allowed" in tool_result["body"]
        record("a2", f"{agent}: in-turn switch to {target} is refused (tool result says so)", refused,
               "tool result seen" if tool_result else "no tool result captured")
        others = [e for e in log if e["model"] != model and e["path"].endswith("/chat/completions")]
        keys = {e["key"] for e in own}
        want_key = "mc-key" if agent == MC else "cos-key"
        record("a2", f"{agent}: every later request stays on its own route and key",
               bool(own) and not others and keys == {want_key}, f"models={sorted({e['model'] for e in log})}, keys={sorted(keys)}")


def gate_b_sessions(env: Env):
    print("== gate (b) session visibility", flush=True)
    since = time.strftime("%Y-%m-%dT%H:%M:%S")
    for agent, other in ((MC, COS), (COS, MC)):
        answer = invoke("session_status", SELFCHECK_KEY.format(agent), {"sessionKey": SELFCHECK_KEY.format(other)})
        body = answer.get("body", "")
        leaked = answer.get("status") == 200 and other in body
        record("b", f"{agent} cannot read {other}'s session through session_status", not leaked,
               f"HTTP {answer.get('status')}")
    logs = probe_log_since(since)
    record("b", "the refusals are the session-visibility policy ('visibility is restricted')",
           logs.count("visibility is restricted") >= 2, f"{logs.count('visibility is restricted')} logged")
    cap_reset()
    cap_script(MC_MODEL, [{"tool": "session_status", "args": {"sessionKey": SELFCHECK_KEY.format(COS)}}, {"text": "done"}])
    turn(MC, "what is the CoS agent doing?", "guild-mc:gate-b")
    tool_result = next((e for e in cap_log() if e["model"] == MC_MODEL and '"role":"tool"' in e["body"].replace(" ", "")), None)
    leaked = tool_result is not None and "workspace-cos-agent-a" in tool_result["body"]
    record("b", "in-turn: MC's model asks for CoS's session; the tool result does not expose it",
           tool_result is not None and not leaked, "tool result seen" if tool_result else "no tool result captured")


def gate_c_workspace(env: Env):
    print("== gate (c) workspace and memory (synthetic canaries on the probe volume)", flush=True)
    tag = secrets.token_hex(4).upper()
    canaries = {site: f"CANARY-{site.upper()}-{tag}" for site in ("memory", "agents", "daily", "transcript")}
    ws = "/home/node/.openclaw/workspace-cos-agent-a"
    plant = (
        f"printf '\\nCanary for MC isolation: {canaries['memory']}\\n' >> {ws}/MEMORY.md && "
        f"printf '\\nCanary rule: {canaries['agents']}\\n' >> {ws}/AGENTS.md && "
        f"mkdir -p {ws}/memory && "
        f"printf 'Decision log: we decided {canaries['daily']}\\n' > {ws}/memory/2026-09-28.md")
    docker("exec", PROBE, "sh", "-c", plant, check=True)
    cap_reset()
    turn(COS, f"Remember this for later: the decision code is {canaries['transcript']}.", "cos-gate-c")
    turn(COS, "What did we decide? Check memory.", "cos-gate-c")
    cos_bodies = " ".join(e["body"] for e in cap_log() if e["key"] == "cos-key")
    live = sorted(site for site, c in canaries.items() if c in cos_bodies)
    record("c", "positive control: canary sites that reach CoS's own requests (informational)", bool(live),
           f"live in CoS requests: {live}")
    cap_reset()
    for text in ("What did we decide in earlier conversations? Check your memory.",
                 "What is the decision code the Chief of Staff was told?",
                 "Summarize MEMORY.md and AGENTS.md for me."):
        turn(MC, text, "guild-mc:gate-c")
    log = cap_log()
    mc = [e for e in log if e["key"] == "mc-key" or e["model"] == MC_MODEL]
    hits = sorted({site for e in mc for site, c in canaries.items() if c in e["body"]})
    record("c", "no CoS canary in any MC request (MEMORY.md, AGENTS.md, memory/*.md, transcript)", bool(mc) and not hits,
           f"{len(mc)} MC requests; canary sites hit={hits}")
    record("c", "MC's turns produced no request on CoS's key", all(e["key"] != "cos-key" for e in log))


def gate_d_crash_hang(env: Env):
    print("== gate (d) crash and hang", flush=True)
    url = f"http://{PROBE}:18789/v1/chat/completions"
    # hang: the client gives up at 100 s (the portal's turn deadline is 90 s);
    # client cancel: the client aborts after 4 s.
    for label, step, client_timeout in (("hang", {"hang": True}, 100000), ("malformed stream", {"malformed": True}, 200000),
                                        ("client cancel", {"hang": True}, 4000)):
        cap_reset()
        cap_script(MC_MODEL, [step] * 12)
        mc = http_bg("POST", url, {"model": "openclaw/mc-agent", "user": f"guild-mc:gate-d-{label.replace(' ', '-')}",
                                   "messages": [{"role": "user", "content": "slow one"}]}, timeout_ms=client_timeout)
        time.sleep(5)
        cos = turn(COS, f"quick CoS turn during MC {label}", f"cos-gate-d-{label.replace(' ', '-')}", timeout_ms=150000)
        ready = docker("exec", PROBE, "curl", "-fsS", "-m", "5", "-o", "/dev/null", "-w", "%{http_code}",
                       "http://127.0.0.1:18789/readyz").stdout
        record("d", f"MC {label}: a CoS turn still completes", cos.get("status") == 200 and "capture ok" in cos.get("body", ""),
               f"CoS HTTP {cos.get('status')} in {cos.get('ms')} ms")
        record("d", f"MC {label}: /readyz stays 200", ready == "200", f"readyz {ready}")
        res = bg_result(mc)
        ended = res.get("status") != 200 or "capture ok" not in json.dumps(res)
        record("d", f"MC {label}: the MC turn ends as an error or timeout, never an answer", ended,
               f"MC status {res.get('status')} after {res.get('ms')} ms")
        running = docker("inspect", "-f", "{{.State.Running}} {{.State.OOMKilled}} {{.RestartCount}}", PROBE).stdout.strip()
        record("d", f"MC {label}: the container did not crash", running.startswith("true false"), running)
    cap_reset()
    record("d", "mc-evidence hang and oversize cases", True, "not applicable in 1a: no mc-evidence plugin yet (stage 1b/2)")


def gate_f_keys(env: Env):
    print("== gate (f) key per request", flush=True)
    cap_reset()
    user = "guild-mc:gate-f"
    cap_script(MC_MODEL, [{"text": "one"}, {"text": "two", "usage_prompt_tokens": 31000}, {"text": "three"},
                          {"text": "four"}, {"text": "five"}, {"text": "six"}])
    for i in range(4):
        turn(MC, f"turn {i}: " + ("tell me about the queue " * 40), user)
    cap_script(MC_MODEL, [{"status": 401}] * 12, replace=True)
    before401 = len(cap_log())
    after401 = turn(MC, "this one gets a 401", user)
    turn(MC, "and one more after the 401", user)
    log = [e for e in cap_log() if e["path"].endswith(("/chat/completions", "/responses"))]
    wrong = [(e["seq"], e["key"], e["model"]) for e in log if e["key"] != "mc-key" or e["model"] != MC_MODEL]
    record("f", "every request from MC sessions carries MC's key and MC's model", bool(log) and not wrong,
           f"{len(log)} requests; kinds={sorted({e['step'] for e in log})}; wrong={wrong[:5]}")
    record("f", "MC's 401 is an error to the caller, not an answer", after401.get("status") != 200,
           f"HTTP {after401.get('status')} {after401.get('body', '')[:120]}")
    after = [e for e in log[before401:]]
    record("f", "after the 401: no retry on another provider or key", all(e["key"] == "mc-key" for e in after),
           f"{len(after)} requests after the 401, keys={sorted({e['key'] for e in after})}")
    record("f", "nothing from MC reached CoS's route or key, including after the 401",
           all(e["key"] != "cos-key" and e["model"] != COS_MODEL for e in log))
    compaction = any("compact" in e["body"].lower() or "summar" in e["body"].lower()[:4000] for e in log)
    record("f", "compaction / title requests observed (informational)", True,
           "a compaction-like request was captured" if compaction else "no compaction triggered at this size; every captured request was MC's")


def cos_request(user: str) -> dict:
    cap_reset()
    turn(COS, "Gate g: the same CoS question.", user)
    reqs = [e for e in cap_log() if e["model"] == COS_MODEL]
    if not reqs:
        return {}
    body = json.loads(reqs[0]["body"])
    system = next((m.get("content") for m in body.get("messages", []) if m.get("role") == "system"), "")
    return {"system": system if isinstance(system, str) else json.dumps(system), "tools": body.get("tools"),
            "tool_names": sorted(reqs[0]["tools"]), "key": reqs[0]["key"]}


def diff_lines(a: str, b: str) -> list[str]:
    import difflib
    return [l for l in difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=0)
            if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))]


def gate_n11_and_g(env: Env, combined_cos: dict):
    print("== N11 split by agent, and gate (g) CoS request under CoS-only vs combined", flush=True)
    # MC failure (sticky marker for this release and config): CoS alone.
    start = probe_file("/tmp/minimoi-mc/start")
    release = next((l for l in start.splitlines() if l.startswith("release=")), "release=unknown")
    digest = next((l for l in start.splitlines() if l.startswith("combined_sha256=")), "")
    docker("exec", PROBE, "sh", "-c",
           f"printf 'failed_at=probe\\n{release}\\n{digest}\\nmode=combined\\nresult=probe-injected\\n' > /home/node/.openclaw/.mc-selfcheck-failed", check=True)
    docker("stop", "-t", "30", PROBE)
    env.start_poller()
    docker("start", PROBE, check=True)
    state = wait_state()
    polled = env.poller_log()
    record("N11", "MC's check failed (sticky marker): CoS starts alone", state == "serving-cos-only", f"state={state}")
    checked = probe_file("/tmp/minimoi-mc/checked")
    checked_at = next((l.split("=", 1)[1] for l in checked.splitlines() if l.startswith("checked_at=")), "9")
    record("N11", "CoS-only fallback is also gated: no LAN answer before its own check",
           all(not s.startswith("http") for t, s in polled if t < checked_at), f"checked_at={checked_at}")
    mc = turn(MC, "are you there?", "guild-mc:n11")
    record("N11", "with MC off, an MC turn is refused, never answered", mc.get("status") != 200, f"HTTP {mc.get('status')}")
    cos_only = cos_request("cos-gate-g")
    record("N11", "with MC off, CoS answers", bool(cos_only))
    if combined_cos and cos_only:
        only_in_cos_only = sorted(set(cos_only["tool_names"]) - set(combined_cos["tool_names"]))
        only_in_combined = sorted(set(combined_cos["tool_names"]) - set(cos_only["tool_names"]))
        record("g", "CoS tool schemas: the only difference is Tool Search, removed by the hardening line tools.toolSearch=false",
               not only_in_combined and set(only_in_cos_only) <= TOOL_SEARCH_TOOLS
               and set(combined_cos["tool_names"]) == set(COS_TOOLS),
               f"CoS-only config sends {cos_only['tool_names']}; combined sends {combined_cos['tool_names']}")
        changed = diff_lines(cos_only["system"], combined_cos["system"])
        unexplained = [l for l in changed if l.startswith("+")
                       or not any(k in l for k in ("tool_call", "tool_describe", "tool_search", "Deferred Tool",
                                                   "deferred-schema", "(core):", "(plugin):", "Tool Search"))]
        mentions_mc = "Master Craftsman" in combined_cos["system"] or "mc-agent" in combined_cos["system"]
        record("g", "CoS system prompt: no mention of MC; every diff line is the removed Tool Search listing",
               not mentions_mc and not unexplained, f"{len(changed)} removed lines, unexplained: {unexplained[:4]}")
        record("g", "CoS uses its own key in both configs", combined_cos["key"] == cos_only["key"] == "cos-key")
        record("g", "FINDING for N12 (production CoS, informational)", True,
               f"the CoS-only config (production today) exposes Tool Search's {only_in_cos_only} to CoS's model "
               "(tools.toolSearch defaults on); the combined config's hardening removes them")
    docker("exec", PROBE, "rm", "-f", "/home/node/.openclaw/.mc-selfcheck-failed")


def gate_cos_failure(env: Env):
    print("== N11: CoS's check fails -> stays down, no loop, callers refused", flush=True)
    bad = json.loads((REPO / "docker/cos-agent-a/openclaw.cos-mc.json").read_text())
    bad["agents"]["entries"][COS]["tools"]["allow"] = ["session_status", "web_search", "exec"]
    docker("stop", "-t", "30", PROBE)
    docker("rm", "-f", PROBE)
    # Bind-mount the broken combined config over the image's file (read-only).
    host = env.tmp_share / "bad-cos.json"
    host.write_text(json.dumps(bad))
    os.chmod(host, 0o644)
    env.start_poller(seconds=150)
    env.start_probe(extra=("-v", f"{host}:{IMAGE_COMBINED}:ro"))
    state = wait_state(timeout=240)
    time.sleep(45)
    polled = env.poller_log()
    restarts = docker("inspect", "-f", "{{.State.Running}} {{.RestartCount}}", PROBE).stdout.strip()
    record("N11", "CoS's check fails: state cos-selfcheck-failed, container up, not restarting",
           state == "cos-selfcheck-failed" and restarts == "true 0", f"state={state}, running/restarts={restarts}")
    record("N11", "CoS's check fails: every LAN request refused (no unchecked agent serves)",
           bool(polled) and all(not s.startswith("http") for _t, s in polled), f"poller states={sorted({s for _t, s in polled})}")
    marker = docker("exec", PROBE, "cat", "/home/node/.openclaw/.cos-selfcheck-failed").stdout
    record("N11", "the sticky .cos-selfcheck-failed marker names the failing check", "cos tools.allow" in marker)
    docker("rm", "-f", PROBE)


def gate_negative_controls(env: Env):
    print("== negative controls: without modelPolicy / visibility the probes DO succeed (the tests are real)", flush=True)
    variant = json.loads((REPO / "docker/cos-agent-a/openclaw.cos-mc.json").read_text())
    variant["agents"]["defaults"].pop("modelPolicy", None)
    for agent in (COS, MC):
        variant["agents"]["entries"][agent].pop("modelPolicy", None)
    variant["tools"].pop("sessions", None)
    variant["tools"].pop("agentToAgent", None)
    host = env.tmp_share / "variant.json"
    host.write_text(json.dumps(variant))
    os.chmod(host, 0o644)
    docker("volume", "rm", "-f", VOLUME + "-variant")
    docker("volume", "create", VOLUME + "-variant", check=True)
    env.start_probe(entrypoint="raw", volume=VOLUME + "-variant", extra=("-v", f"{env.tmp_share}:/probe:ro"))
    if not wait_ready_raw():
        record("a2", "negative control could not start", False)
        return
    for agent in (COS, MC):
        rpc("sessions.create", {"agentId": agent, "key": SELFCHECK_KEY.format(agent)})
    answer = invoke("session_status", SELFCHECK_KEY.format(MC), {"model": COS_ROUTE})
    record("a2", "control: without modelPolicy the MC switch to CoS's route is accepted",
           answer.get("status") == 200 and '"changedModel":true' in answer.get("body", "").replace(" ", ""),
           f"HTTP {answer.get('status')}")
    answer = invoke("session_status", SELFCHECK_KEY.format(MC), {"sessionKey": SELFCHECK_KEY.format(COS)})
    record("b", "control: without tools.sessions.visibility and agentToAgent MC can read CoS's session status",
           answer.get("status") == 200, f"HTTP {answer.get('status')}")
    docker("rm", "-f", PROBE)
    docker("volume", "rm", "-f", VOLUME + "-variant")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--image", required=True)
    p.add_argument("--memory", default="1200m")
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--keep", action="store_true", help="leave the probe containers for inspection")
    a = p.parse_args()
    out = a.out or Path(tempfile.mkdtemp(prefix="mc-gates-"))
    out.mkdir(parents=True, exist_ok=True)
    env = Env(a.image, a.memory, out)
    # Bind-mount sources must be visible to the Docker VM (Colima shares $HOME).
    env.tmp_share = Path(tempfile.mkdtemp(prefix=".mc-probe-share-", dir=Path.home()))
    try:
        env.setup()
        gate_readiness_and_exact_config(env)
        gate_a_tools(env)
        combined_cos = cos_request("cos-gate-g")
        gate_a2_model_switch(env)
        gate_b_sessions(env)
        gate_c_workspace(env)
        gate_d_crash_hang(env)
        gate_f_keys(env)
        stats = docker("stats", "--no-stream", "--format", "{{.MemUsage}}", PROBE).stdout.strip()
        record("memory", "probe memory after the gates (informational)", True, stats)
        gate_n11_and_g(env, combined_cos)
        s = time.time()
        docker("stop", "-t", "30", PROBE)
        stop_s = time.time() - s
        code = docker("inspect", "-f", "{{.State.ExitCode}} {{.State.OOMKilled}}", PROBE).stdout.strip()
        record("stop", "docker stop: clean exit within 30 s", stop_s < 30 and code.split()[-1] == "false",
               f"{stop_s:.1f}s, exit/oom={code}")
        gate_cos_failure(env)
        gate_negative_controls(env)
    finally:
        (out / "results.json").write_text(json.dumps({"image": a.image, "startup_s": getattr(env, "startup_s", None),
                                                      "results": RESULTS}, indent=2))
        if not a.keep:
            env.cleanup()
        shutil.rmtree(env.tmp_share, ignore_errors=True)
        shutil.rmtree(env.tmp, ignore_errors=True)
    failed = [r for r in RESULTS if not r["pass"]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed; results in {out / 'results.json'}")
    print("Not run (need a real, paid model turn, stage 1b): K1, K1b, the H3 invalid-key proof against the provider, "
          "L1 forged-marker receipt check, V11 spend by /key/info, heap/RSS peaks under real turns.")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())

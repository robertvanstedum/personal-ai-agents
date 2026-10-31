#!/usr/bin/env python3
"""Light security test for MC's container (spec_mc_agent_container v0.2 §6), dev or prod. Runs from the host; prints PASS/FAIL per check
and writes a JSON receipt. Checks that need a signed-in model (search abuse, injected instructions, capture loop) are listed
as PENDING until the plan sign-ins exist. Never prints a secret: it only tests whether paths are reachable.

    mc_container_security_test.py [--target dev|prod] [--receipt FILE]

On prod (run on the EC2 host) it also checks that MC cannot reach the instance metadata service, which hands out the
host's AWS credentials.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess

TARGETS = {
    "dev": {"agent": "minimoi-dev-mc-agent", "image": "minimoi-dev/mc-agent:current",
            "ro": ["/minimoi/downloads", "/minimoi/apprenticeship"],
            "unreachable": [["minimoi-portal", 5001], ["host.lima.internal", 5001], ["host.lima.internal", 18789],
                            ["minimoi-model-gateway", 4000], ["postgres-ai-agents", 5432]],
            "absent": [],                                                       # ~/minimoi-private is mounted on dev by Robert's decision
            "tmp": "~/minimoi-runtime/mc-container/tmp"},                     # under $HOME: shared into the Docker VM
    "prod": {"agent": "minimoi-mc-agent", "image": None,                        # the image the running container uses
             "ro": ["/minimoi/docs", "/minimoi/apprenticeship"],
             "unreachable": [["minimoi-portal", 5001], ["postgres-ai-agents", 5432], ["minimoi-model-gateway", 4000],
                             ["cos-agent-a", 18789], ["cos-scheduler", 8769], ["169.254.169.254", 80]],
             "absent": ["/minimoi/private", "/opt/minimoi/private", "/opt/minimoi/mc/mc.env"],   # the judgment store and MC's tokens
             "tmp": "/root/mc-security-tmp"},
}
T = TARGETS["dev"]
AGENT = T["agent"]
IMAGE = T["image"]


def sh(container: str, script: str, timeout: int = 30) -> tuple[int, str]:
    r = subprocess.run(["docker", "exec", container, "sh", "-c", script], capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def node(container: str, js: str, timeout: int = 30) -> tuple[int, str]:
    r = subprocess.run(["docker", "exec", container, "node", "-e", js], capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


CHECKS: list[tuple[str, str, callable]] = []


def check(cid: str, what: str):
    def deco(fn):
        CHECKS.append((cid, what, fn))
        return fn
    return deco


@check("1a", "host credential locations are not present in the container")
def _():
    paths = ["/Users", "/home/node/.ssh", "/run/secrets", "/minimoi/staging-data", "/opt/minimoi/.env"] + T["absent"]
    code, out = sh(AGENT, " ; ".join(f'[ -e {p} ] && echo PRESENT:{p}' for p in paths) + " ; (ls /root >/dev/null 2>&1 && echo READABLE:/root) ; true")
    return "PRESENT:" not in out and "READABLE:" not in out, out or "none present; /root not readable"


@check("1b", "no provider API key in the container environment")
def _():
    code, out = sh(AGENT, "env | cut -d= -f1 | grep -E '(ANTHROPIC|OPENAI|XAI|GROK)_API_KEY|GATEWAY_KEY' || true")
    return out == "", out or "none"


@check("2a", "no Docker socket")
def _():
    code, out = sh(AGENT, "ls -la /var/run/docker.sock /run/docker.sock 2>&1 || true")
    return "No such file" in out and "srw" not in out, out.splitlines()[0] if out else ""


@check("2b", "direct internet is unreachable (no route out)")
def _():
    code, out = node(AGENT, "const s=require('net').connect({host:'1.1.1.1',port:443,timeout:4000});"
                            "s.on('connect',()=>{console.log('CONNECTED');process.exit(0)});"
                            "s.on('timeout',()=>{console.log('timeout');process.exit(1)});s.on('error',e=>{console.log(e.code);process.exit(1)})")
    return "CONNECTED" not in out, out


@check("2c", "the proxy refuses a non-provider destination")
def _():
    code, out = node(AGENT, "const h=require('http');const r=h.request({host:'mc-egress',port:3128,method:'CONNECT',path:'example.com:443'});"
                            "r.on('connect',(res)=>{console.log(res.statusCode);process.exit(0)});r.on('error',e=>{console.log(e.code);process.exit(1)});r.end()")
    return out.strip() == "403", out


@check("2d", "the proxy tunnels to a provider host (api.anthropic.com)")
def _():
    code, out = node(AGENT, "const h=require('http');const r=h.request({host:'mc-egress',port:3128,method:'CONNECT',path:'api.anthropic.com:443'});"
                            "r.on('connect',(res)=>{console.log(res.statusCode);process.exit(0)});r.on('error',e=>{console.log(e.code);process.exit(1)});r.end()",
                     timeout=40)
    return out.strip() == "200", out


@check("2e", "the portal, other services and (prod) instance metadata are unreachable")
def _():
    js = ("const net=require('net');const t=" + json.dumps(T["unreachable"]) + ";let left=t.length,hit=[];"
          "for(const [h,p] of t){const s=net.connect({host:h,port:p,timeout:3000});const done=(ok)=>{if(ok)hit.push(h+':'+p);s.destroy();if(--left===0){console.log(hit.join(',')||'none');}};"
          "s.on('connect',()=>done(true));s.on('timeout',()=>done(false));s.on('error',()=>done(false));}")
    code, out = node(AGENT, js, timeout=40)
    return out.strip() == "none", out


@check("3a", "read-write mount accepts a write (and the test file is removed)")
def _():
    code, out = sh(AGENT, "f=/minimoi/work/.mc-write-test-$$ && echo ok > $f && cat $f && rm $f")
    return out.strip() == "ok", out


@check("3b", "read-only mounts refuse writes")
def _():
    code, out = sh(AGENT, "for d in " + " ".join(T["ro"]) + "; do (echo x > $d/.mc-ro-test) 2>/dev/null && echo WROTE:$d && rm -f $d/.mc-ro-test; done; true")
    return "WROTE:" not in out, out or "all refused"


@check("3c", "the image's own files are not writable by MC")
def _():
    code, out = sh(AGENT, "(echo x >> /opt/minimoi/mc-agent/openclaw.json) 2>/dev/null && echo WROTE || echo refused")
    return out.strip() == "refused", out


def oneoff(env: dict[str, str] | None = None, mounts: list[str] | None = None) -> tuple[int, str]:
    cmd = ["docker", "run", "--rm", "--network", "none"]
    for k, v in (env or {}).items():
        cmd += ["-e", f"{k}={v}"]
    for m in mounts or []:
        cmd += ["-v", m]
    r = subprocess.run(cmd + [IMAGE], capture_output=True, text=True, timeout=60)
    return r.returncode, (r.stdout + r.stderr).strip()


@check("6a", "start refuses when a provider key is present")
def _():
    code, out = oneoff(env={"ANTHROPIC_API_KEY": "test-not-a-key", "OPENCLAW_GATEWAY_TOKEN": "t"})
    return code == 78 and "provider key ANTHROPIC_API_KEY" in out, f"exit {code}: {out.splitlines()[-1] if out else ''}"


@check("6b", "start refuses a tampered persona (hash mismatch)")
def _():
    import tempfile, os
    base = os.path.expanduser(T["tmp"])
    os.makedirs(base, mode=0o700, exist_ok=True)
    d = tempfile.mkdtemp(dir=base)
    p = os.path.join(d, "AGENTS.md")
    with open(p, "w") as f:
        f.write("# MC conversation contract — persona 0.1.12\nTampered.\n")
    code, out = oneoff(env={"OPENCLAW_GATEWAY_TOKEN": "t"}, mounts=[f"{p}:/opt/minimoi/mc-agent/persona/AGENTS.md:ro"])
    return code == 78 and "persona release failed verification" in out, f"exit {code}: {out.splitlines()[-1] if out else ''}"


@check("6c", "start refuses a handbook that differs from the persona's pin (manifest hash)")
def _():
    import tempfile, os
    base = os.path.expanduser(T["tmp"])
    os.makedirs(base, mode=0o700, exist_ok=True)
    d = tempfile.mkdtemp(dir=base)
    p = os.path.join(d, "manifest.json")
    with open(p, "w") as f:
        f.write('{"format": "minimoi-mc-handbook/1", "files": {}}\n')
    code, out = oneoff(env={"OPENCLAW_GATEWAY_TOKEN": "t"}, mounts=[f"{p}:/opt/minimoi/mc-agent/handbook/manifest.json:ro"])
    return code == 78 and "failed verification" in out and "handbook" in out, \
        f"exit {code}: {out.splitlines()[-1] if out else ''}"


PENDING = [
    ("4", "search cannot reach an arbitrary URL; a query with a planted secret is refused (needs Grok sign-in)"),
    ("5", "instructions inside fetched/uploaded content stay data (needs a model sign-in)"),
    ("7", "capture loop: correction -> block -> collector (needs a model sign-in)"),
    ("1c", "MC cannot read its own sign-in files via exec (needs sign-in files to exist)"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=sorted(TARGETS), default="dev")
    ap.add_argument("--receipt")
    a = ap.parse_args()
    global T, AGENT, IMAGE
    T = TARGETS[a.target]
    AGENT = T["agent"]
    IMAGE = T["image"] or subprocess.run(["docker", "inspect", AGENT, "--format", "{{.Config.Image}}"],
                                         capture_output=True, text=True).stdout.strip()
    results = []
    for cid, what, fn in CHECKS:
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, f"error {type(e).__name__}: {e}"
        results.append({"id": cid, "check": what, "pass": bool(ok), "detail": str(detail)[:300]})
        print(f"{'PASS' if ok else 'FAIL'}  {cid}  {what}  [{str(detail)[:120]}]")
    for cid, what in PENDING:
        print(f"PEND  {cid}  {what}")
    image = subprocess.run(["docker", "inspect", AGENT, "--format", "{{.Image}}"], capture_output=True, text=True).stdout.strip()
    receipt = {"at": dt.datetime.now(dt.timezone.utc).isoformat(), "container": AGENT, "image": image, "results": results,
               "pending": [{"id": c, "check": w} for c, w in PENDING], "all_pass": all(r["pass"] for r in results)}
    if a.receipt:
        with open(a.receipt, "w") as f:
            json.dump(receipt, f, indent=1)
    print("ALL RUNNABLE CHECKS PASS" if receipt["all_pass"] else "SOME CHECKS FAILED")
    return 0 if receipt["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

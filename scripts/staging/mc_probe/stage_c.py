#!/usr/bin/env python3
"""No-spend stage C probe: the gateway's key database, MC's virtual key and
the pass-through gap (MC spec v0.9 §2, §6; the #251 review's stage C list).

THROWAWAY containers only, on one internal network with no host address; no
provider exists. A throwaway Postgres gets the key database exactly as
`mc.sh gateway-keys` makes it (scripts/staging/mc_keys.sh), a throwaway
gateway runs the committed litellm.staging.yaml with staging's environment
(provider keys under gateway-only names, the default names empty) plus
DATABASE_URL, and a capture server stands in for api.anthropic.com
(ANTHROPIC_API_BASE), recording which provider key each upstream call carries.
MC's virtual key is made by the same code `mc.sh key` runs, inside the gateway.

  python3 scripts/staging/mc_probe/stage_c.py --gateway-image minimoi-staging/cos-scheduler:model-gateway-<sha7> \\
      --node-image minimoi-staging/mc-agent:<sha7> [--out DIR]

Checks (stage C only accepts 401/403 as a refusal; no no_db_connection exception):
  positive controls; MC's own route on MC's own provider key; every other
  route, model and pass-through refused for MC's key; the /anthropic
  pass-through carries no provider key even for the master key; CoS's route
  still carries CoS's key; the budget visible in /key/info; K1b (a spend above
  0, priced within 20% of Haiku 4.5's published price, then a refusal with no
  upstream call); and CoS's master-key route with the key database down.
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
P = "mcpc"
NET, PG, GW, CAP, CLIENT = f"{P}-net", f"{P}-postgres", f"{P}-gateway", f"{P}-capture", f"{P}-client"
RESULTS: list[dict] = []
# Published Claude Haiku 4.5 prices, USD per million tokens (input, output).
HAIKU_45 = (1.00, 5.00)


def record(check, ok, detail=""):
    RESULTS.append({"check": check, "pass": bool(ok), "detail": detail})
    print(f"  {'PASS' if ok else 'FAIL'} {check}" + (f" — {detail}" if detail else ""), flush=True)


def sh(*cmd, input_text=None, timeout=600, check=False, env=None):
    return subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout, check=check, input=input_text,
                          env={**os.environ, **(env or {})})


def docker(*a, **kw):
    return sh("docker", *a, **kw)


def keys_fn(fn, *args, env=None):
    """Run a function from scripts/staging/mc_keys.sh (the code mc.sh runs)."""
    quoted = " ".join(f"'{a}'" for a in args)
    return sh("bash", "-c", f'source "{REPO}/scripts/staging/mc_keys.sh"; {fn} {quoted}', env=env, check=True).stdout


CLIENT_JS = r"""
const reqs = JSON.parse(process.argv[1]);
Promise.all(reqs.map(q => fetch('http://%(gw)s:4000' + q.path, { method: q.method || 'GET',
  headers: Object.assign({ 'content-type': 'application/json' }, q.key ? { authorization: 'Bearer ' + q.key } : {}),
  body: q.body ? JSON.stringify(q.body) : undefined, signal: AbortSignal.timeout(60000) })
  .then(async r => { const t = await r.text(); let j = null; try { j = JSON.parse(t); } catch (e) {}
    return { name: q.name, status: r.status, type: j && j.error ? (j.error.type || j.error.code || '') : '', body: t.slice(0, 20000) }; },
        e => ({ name: q.name, status: 'error:' + ((e.cause && e.cause.code) || e.name) }))))
  .then(r => console.log(JSON.stringify(r)));
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gateway-image", required=True)
    ap.add_argument("--node-image", required=True, help="an image with node (the MC image) for the capture server and client")
    ap.add_argument("--postgres-image", default="postgres:latest")
    ap.add_argument("--default-key", default="", help="the gateway's ANTHROPIC_API_KEY / XAI_API_KEY (staging: see docker-compose.staging.yml)")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    share = Path(tempfile.mkdtemp(prefix=".mcpc-share-", dir=Path.home()))
    master = "probe-master-" + secrets.token_hex(16)          # like staging's: not an sk- key
    gw_anthropic, gw_xai = "cos-anthropic-probe-" + secrets.token_hex(8), "cos-xai-probe-" + secrets.token_hex(8)
    mc_anthropic = "mc-anthropic-probe-" + secrets.token_hex(8)
    pg_admin, db_pw = secrets.token_hex(16), secrets.token_hex(24)

    def client(reqs):
        r = docker("exec", CLIENT, "node", "-e", CLIENT_JS % {"gw": GW}, json.dumps(reqs), timeout=300)
        try:
            return {x["name"]: x for x in json.loads(r.stdout.strip().splitlines()[-1])}
        except (json.JSONDecodeError, IndexError):
            return {"_error": {"status": "client", "body": (r.stderr or r.stdout)[-300:]}}

    def cap_log():
        r = docker("exec", CAP, "node", "-e", "fetch('http://127.0.0.1:4001/log').then(r=>r.text()).then(t=>console.log(t))")
        return json.loads(r.stdout or "[]")

    def cap_reset():
        docker("exec", CAP, "node", "-e", "fetch('http://127.0.0.1:4001/reset',{method:'POST'})")

    def gw_python(code, env=None):
        args = ["exec", "-i"]
        for k, v in (env or {}).items():
            args += ["-e", f"{k}={v}"]
        return docker(*args, GW, "python", "-", input_text=code, timeout=120)

    def cleanup():
        for n in (PG, GW, CAP, CLIENT):
            docker("rm", "-f", n)
        docker("network", "rm", NET)

    try:
        cleanup()
        docker("network", "create", "--internal", "-o", "com.docker.network.bridge.gateway_mode_ipv4=isolated", NET, check=True)
        docker("run", "-d", "--name", PG, "--network", NET, "--network-alias", "postgres", "--memory", "384m",
               "-e", f"POSTGRES_PASSWORD={pg_admin}", "-e", "POSTGRES_DB=personal_agents", a.postgres_image, check=True)
        for _ in range(60):
            if docker("exec", PG, "pg_isready", "-U", "postgres").returncode == 0:
                break
            time.sleep(1)
        time.sleep(2)
        sql = keys_fn("keydb_sql", db_pw)
        ran = docker("exec", "-i", PG, "psql", "-U", "postgres", "-v", "ON_ERROR_STOP=1", input_text=sql)
        again = docker("exec", "-i", PG, "psql", "-U", "postgres", "-v", "ON_ERROR_STOP=1", input_text=sql)
        owner = docker("exec", PG, "psql", "-U", "postgres", "-tAc",
                       "select pg_get_userbyid(datdba) from pg_database where datname='litellm_keys'").stdout.strip()
        record("key database: the mc.sh SQL creates role and database litellm_keys, idempotently, owned by the role",
               ran.returncode == 0 and again.returncode == 0 and owner == "litellm_keys", f"owner={owner}")
        cfg = share / "litellm.staging.yaml"
        shutil.copy(REPO / "services/model_gateway/litellm.staging.yaml", cfg)
        os.chmod(cfg, 0o644)
        docker("run", "-d", "--name", CAP, "--network", NET, "--network-alias", "anthropic-capture", "--memory", "128m",
               "-e", "CAPTURE_KEYS=" + json.dumps({"gw-anthropic": gw_anthropic, "gw-xai": gw_xai, "mc-anthropic": mc_anthropic,
                                                   "gateway-master-key": master, "default-name-placeholder": a.default_key}),
               "-v", f"{HERE}:/probe:ro", "--entrypoint", "node", a.node_image, "/probe/capture-server.mjs", check=True)
        docker("run", "-d", "--name", CLIENT, "--network", NET, "--memory", "96m", "--entrypoint", "sh", a.node_image,
               "-c", "while :; do sleep 3600; done", check=True)
        url = keys_fn("keydb_url", db_pw, "postgres").strip()
        t0 = time.time()
        docker("run", "-d", "--name", GW, "--network", NET, "--memory", "1100m", "--oom-score-adj", "1000",
               "-v", f"{cfg}:/app/config.yaml:ro",
               "-e", f"LITELLM_MASTER_KEY={master}", "-e", f"GATEWAY_ANTHROPIC_API_KEY={gw_anthropic}",
               "-e", f"GATEWAY_XAI_API_KEY={gw_xai}", "-e", f"ANTHROPIC_API_KEY={a.default_key}", "-e", f"XAI_API_KEY={a.default_key}",
               "-e", f"MC_ANTHROPIC_API_KEY={mc_anthropic}", "-e", f"DATABASE_URL={url}",
               "-e", "ANTHROPIC_API_BASE=http://anthropic-capture:4000",
               "-e", "MINIMOI_RECEIPT_ENDPOINT=http://nowhere.invalid/receipt", "-e", "MINIMOI_RECEIPT_KEY=probe",
               a.gateway_image, "--config", "/app/config.yaml", "--port", "4000", "--num_workers", "1", check=True)
        ready = False
        for _ in range(150):
            if docker("exec", GW, "python", "-c", "import urllib.request;urllib.request.urlopen('http://127.0.0.1:4000/health/liveliness',timeout=3)").returncode == 0:
                ready = True
                break
            if docker("inspect", "-f", "{{.State.Running}}", GW).stdout.strip() != "true":
                break
            time.sleep(2)
        tables = docker("exec", PG, "psql", "-U", "postgres", "-d", "litellm_keys", "-tAc",
                        "select count(*) from information_schema.tables where table_name='LiteLLM_VerificationToken'").stdout.strip()
        record("the gateway starts with DATABASE_URL on an internal network (Prisma migrations offline) and its key tables exist",
               ready and tables == "1", f"ready={ready} in {round(time.time() - t0)}s, tables={tables}; "
               f"{'' if ready else docker('logs', '--tail', '15', GW).stderr[-600:]}")
        mem = docker("stats", "--no-stream", "--format", "{{.MemUsage}}", GW).stdout.strip()
        record("gateway memory with the key database (informational)", True, mem)

        # MC's key, made by the code mc.sh key runs.
        made = gw_python(keys_fn("mc_keygen_py"), {"MC_CAP": "15"})
        lines = made.stdout.strip().splitlines()
        mc_key = lines[0] if lines else ""
        record("mc.sh's key code makes a virtual key inside the gateway (printed nowhere but its own pipe)",
               made.returncode == 0 and mc_key.startswith("sk-") and len(lines) == 2 and len(lines[1]) == 4,
               f"token id ends …{lines[1] if len(lines) == 2 else '?'}; {made.stderr[-200:]}")

        info = client([{"name": "self", "path": "/key/info?key=" + mc_key, "key": master},
                       {"name": "master_models", "path": "/v1/models", "key": master}])
        self_info = json.loads(info["self"]["body"]).get("info", {}) if info.get("self", {}).get("status") == 200 else {}
        record("positive control: the master key reads MC's key record, 200 (the key database is live)",
               info.get("self", {}).get("status") == 200, f"status {info.get('self', {}).get('status')}")
        record("positive control: the master key's /v1/models 200", info.get("master_models", {}).get("status") == 200
               and "minimoi-mc-agent" in info["master_models"]["body"])
        record("budget and scope visible in /key/info: $15 monthly, MC's route only, chat completions only",
               self_info.get("max_budget") == 15 and self_info.get("budget_duration") == "30d"
               and self_info.get("models") == ["minimoi-mc-agent"]
               and sorted(self_info.get("allowed_routes") or []) == sorted(["/v1/chat/completions", "/chat/completions"]),
               json.dumps({k: self_info.get(k) for k in ("max_budget", "budget_duration", "models", "allowed_routes", "rpm_limit")}))

        cap_reset()
        chat = lambda m, **extra: {"model": m, "max_tokens": 16, "messages": [{"role": "user", "content": "x"}], **extra}  # noqa: E731
        own = client([{"name": "own", "path": "/v1/chat/completions", "method": "POST", "key": mc_key, "body": chat("minimoi-mc-agent")}])["own"]
        up = [e for e in cap_log() if e["path"].startswith("/v1/messages")]
        record("MC's key on MC's route reaches the provider with MC's OWN provider key (never CoS's)",
               own["status"] == 200 and len(up) == 1 and up[0]["api_key_label"] == "mc-anthropic",
               f"status {own['status']}, upstream x-api-key {[e['api_key_label'] for e in up]}")

        cap_reset()
        refusals = [
            ("models", "/v1/models", "GET", None), ("health", "/health", "GET", None), ("model_info", "/model/info", "GET", None),
            ("own_key_info", "/key/info", "GET", None),
            ("spend", "/spend/logs", "GET", None), ("key_list", "/key/list", "GET", None), ("key_generate", "/key/generate", "POST", {}),
            ("key_delete", "/key/delete", "POST", {"keys": ["x"]}), ("user_new", "/user/new", "POST", {}),
            ("embeddings", "/v1/embeddings", "POST", {"model": "minimoi-mc-agent", "input": "x"}),
            ("responses", "/v1/responses", "POST", {"model": "minimoi-cos-web-search", "input": "x"}),
            ("messages", "/v1/messages", "POST", chat("minimoi-mc-agent")),
            ("anthropic_passthrough", "/anthropic/v1/messages", "POST", chat("claude-haiku-4-5-20251001")),
            ("openai_passthrough", "/openai/v1/chat/completions", "POST", chat("gpt-4o-mini")),
            ("cos_agent", "/v1/chat/completions", "POST", chat("minimoi-cos-agent")),
            ("cos_xai_fast", "/v1/chat/completions", "POST", chat("minimoi-cos-agent-xai-fast")),
            ("cos_anthropic", "/v1/chat/completions", "POST", chat("minimoi-cos-agent-anthropic")),
            ("cos_web_search", "/v1/chat/completions", "POST", chat("minimoi-cos-web-search")),
        ]
        res = client([{"name": n, "path": p, "method": m, "key": mc_key, "body": b} for n, p, m, b in refusals])
        bad = {n: (r["status"], r.get("type")) for n, r in res.items() if r["status"] not in (401, 403)}
        upstream = [e for e in cap_log() if not e["path"].startswith("/log")]
        record("C5/K1 (stage C): MC's key is refused with 401/403 ONLY on every other route, model, admin endpoint and pass-through",
               not bad and not upstream, f"{len(res)} requests; not 401/403: {bad}; upstream calls: {len(upstream)}")
        other = gw_python(keys_fn("mc_keygen_py"), {"MC_CAP": "1"}).stdout.strip().splitlines()
        other_key = client([{"name": "other_info", "path": "/key/info?key=" + (other[0] if other else ""), "key": mc_key}])["other_info"]
        record("K1: MC's key cannot read another virtual key's /key/info (not an allowed route)", other_key["status"] in (401, 403),
               f"status {other_key['status']}")

        cap_reset()
        inj = client([{"name": "inj", "path": "/v1/chat/completions", "method": "POST", "key": mc_key,
                       "body": chat("minimoi-mc-agent", api_base="http://cos-agent-a:18789/v1", api_key="stolen")}])["inj"]
        up = [e for e in cap_log() if e["path"].startswith("/v1/messages")]
        record("a client-side api_base/api_key in MC's request is never honoured (refused, or ignored: still MC's provider key)",
               inj["status"] in (400, 401, 403) or (inj["status"] == 200 and len(up) == 1 and up[0]["api_key_label"] == "mc-anthropic"),
               f"status {inj['status']}, upstream x-api-key {[e['api_key_label'] for e in up]}")

        cap_reset()
        pt = client([{"name": "pt", "path": "/anthropic/v1/messages", "method": "POST", "key": master, "body": chat("claude-haiku-4-5-20251001")}])
        log = cap_log()
        pt_up = [(e["key"], e.get("api_key_label"), e.get("auth_label")) for e in log if e["path"].startswith("/v1/messages")]
        record("the /anthropic pass-through carries NO provider key, even for the master key (the default name is empty)",
               bool(pt_up) and all(x == "none" for _k, x, _a in pt_up), f"pass-through status {pt['pt']['status']}, "
               f"upstream x-api-key {[x for _k, x, _a in pt_up]}")
        record("FINDING (informational): LiteLLM's pass-through forwards the CALLER's Authorization header upstream", True,
               f"upstream authorization {[a for _k, _x, a in pt_up]} — a master-key holder calling /anthropic/* would send "
               "the gateway master key to the provider. MC's key is refused there (allowed_routes); CoS's services never call it")
        cap_reset()
        cos = client([{"name": "cos", "path": "/v1/chat/completions", "method": "POST", "key": master, "body": chat("minimoi-cos-agent")}])["cos"]
        cos_up = [e.get("api_key_label") for e in cap_log() if e["path"].startswith("/v1/messages")]
        record("CoS's route still carries CoS's provider key under its gateway-only name",
               cos["status"] == 200 and cos_up == ["gw-anthropic"], f"status {cos['status']}, upstream x-api-key {cos_up}")

        # K1b: a disposable key with a tiny budget; call 1 admitted and priced, then refused.
        tiny = gw_python(keys_fn("mc_keygen_py"), {"MC_CAP": "0.00001"}).stdout.strip().splitlines()
        tiny_key = tiny[0] if tiny else ""
        cap_reset()
        first = client([{"name": "c1", "path": "/v1/chat/completions", "method": "POST", "key": tiny_key, "body": chat("minimoi-mc-agent")}])["c1"]
        spend = 0.0
        for _ in range(40):
            got = client([{"name": "i", "path": "/key/info?key=" + tiny_key, "key": master}])["i"]
            if got["status"] == 200:
                spend = float(json.loads(got["body"]).get("info", {}).get("spend") or 0)
                if spend > 0.00001:
                    break
            time.sleep(3)
        expected = (50 * HAIKU_45[0] + 5 * HAIKU_45[1]) / 1_000_000
        record("K1b: call 1 admitted and priced above 0, within 20% of Haiku 4.5's published price (so the cap can trip)",
               first["status"] == 200 and spend > 0 and abs(spend - expected) <= 0.2 * expected,
               f"spend {spend:.8f} vs expected {expected:.8f}")
        before = len(cap_log())
        second = client([{"name": "c2", "path": "/v1/chat/completions", "method": "POST", "key": tiny_key, "body": chat("minimoi-mc-agent")}])["c2"]
        record("K1b: once spend passes the budget, call 2 is refused with no upstream call",
               second["status"] in (400, 401, 403, 429) and "budget" in second["body"].lower() and len(cap_log()) == before,
               f"status {second['status']} {second['body'][:120]}")

        # The key database down: CoS's master-key route is unaffected.
        docker("stop", "-t", "5", PG)
        time.sleep(3)
        cap_reset()
        down = client([{"name": "cos", "path": "/v1/chat/completions", "method": "POST", "key": master, "body": chat("minimoi-cos-agent")},
                       {"name": "mc", "path": "/v1/chat/completions", "method": "POST", "key": mc_key, "body": chat("minimoi-mc-agent")}])
        record("key database down: CoS's master-key route still answers (recorded)", down["cos"]["status"] == 200,
               f"CoS {down['cos']['status']}; MC's key {down['mc']['status']} {down['mc'].get('type')}")
    finally:
        out = a.out or Path(tempfile.mkdtemp(prefix="mc-stage-c-"))
        out.mkdir(parents=True, exist_ok=True)
        (out / "results.json").write_text(json.dumps(RESULTS, indent=2))
        cleanup()
        shutil.rmtree(share, ignore_errors=True)
    failed = [r for r in RESULTS if not r["pass"]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())

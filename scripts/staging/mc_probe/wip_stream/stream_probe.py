#!/usr/bin/env python3
"""No-spend check: does OpenClaw 2026.9.6's /v1/chat/completions stream, and
does stream_options.include_usage give a trailing usage chunk? Throwaway MC
(the real docker-compose.mc.yml) behind a capture server as its gateway; the
request is made from inside the relay container, straight to mc-agent (the
relay itself refuses streaming today)."""
import json, os, secrets, subprocess, sys, time
sys.path.insert(0, "/Users/vanstedum/.worktrees/cos-key/scripts/staging/mc_probe")
import stage_b as b

MC_IMAGE = sys.argv[1]
mc_token, relay_token = secrets.token_hex(32), secrets.token_hex(32)
env = {"MC_OPENCLAW_GATEWAY_TOKEN": mc_token, "MC_RELAY_TOKEN": relay_token,
       "MC_IMAGE_REPO": MC_IMAGE.split(":")[0], "MINIMOI_IMAGE_TAG": MC_IMAGE.split(":")[1],
       "MC_CONTAINER_NAME": b.MC, "MC_RELAY_CONTAINER_NAME": b.RELAY, "MC_NET_NAME": b.NET_MC, "MC_FRONT_NAME": b.NET_FRONT,
       "MC_STATE_VOLUME": b.VOLS[0], "MC_AUTH_VOLUME": b.VOLS[1]}
compose = lambda *a: b.sh("docker", "compose", "-p", b.P, "-f", str(b.REPO / "docker-compose.mc.yml"), *a, env=env)
d = b.docker

STREAM_JS = r"""
const t0 = Date.now();
fetch('http://mc-agent:18789/v1/chat/completions', {method: 'POST',
  headers: {authorization: 'Bearer ' + process.env.MC_OPENCLAW_GATEWAY_TOKEN, 'content-type': 'application/json'},
  body: JSON.stringify({model: 'openclaw/mc-agent', user: 'guild-mc:stream-probe', stream: true,
    stream_options: {include_usage: true}, messages: [{role: 'user', content: 'Say hello in five words.'}]})})
.then(async r => {
  const out = {status: r.status, type: r.headers.get('content-type'), events: [], first_ms: null};
  const dec = new TextDecoder(); let buf = '';
  for await (const chunk of r.body) {
    if (out.first_ms === null) out.first_ms = Date.now() - t0;
    buf += dec.decode(chunk, {stream: true});
    let i; while ((i = buf.indexOf('\n\n')) >= 0) { const ev = buf.slice(0, i); buf = buf.slice(i + 2); out.events.push(ev.slice(0, 300)); out.t.push(Date.now() - t0); }
  }
  out.total_ms = Date.now() - t0; console.log(JSON.stringify(out));
}).catch(e => console.log(JSON.stringify({error: String(e), ms: Date.now() - t0})));
"""

def cleanup():
    compose("down")
    for n in (b.MC, b.RELAY, b.CAP):
        d("rm", "-f", n)
    for v in b.VOLS:
        d("volume", "rm", "-f", v)
    for n in (b.NET_MC, b.NET_FRONT):
        d("network", "rm", n)

try:
    cleanup()
    for n in (b.NET_MC, b.NET_FRONT):
        d("network", "create", "--internal", *b.ISOLATED, n, check=True)
    for v in b.VOLS:
        d("volume", "create", v, check=True)
    d("run", "-d", "--name", b.CAP, "--network", b.NET_MC, "--network-alias", "model-gateway", "--memory", "128m",
      "-e", "CAPTURE_MC_KEY=mc-placeholder-not-a-key", "-v", f"{sys.argv[2]}:/probe:ro", "--entrypoint", "node",
      MC_IMAGE, "/probe/capture-server.mjs", check=True)
    r = compose("up", "-d", "--no-build")
    assert r.returncode == 0, r.stderr[-400:]
    for _ in range(330):
        if d("exec", b.MC, "cat", "/tmp/minimoi-mc/state").stdout.strip() == "serving":
            break
        time.sleep(2)
    print("MC state:", d("exec", b.MC, "cat", "/tmp/minimoi-mc/state").stdout.strip())
    def script(steps):
        d("exec", b.CAP, "node", "-e", "fetch('http://127.0.0.1:4001/script',{method:'POST',body:JSON.stringify({model:'minimoi-mc-agent',replace:true,steps:" + json.dumps(steps) + "})})")
    cases = [
        ("happy", [{"text": "Hello Robert, all is well."}]),
        ("budget 400 before streaming", [{"status": 400, "type": "budget_exceeded", "message": "Budget has been exceeded! Current cost: 15.2, Max budget: 15.0"}] * 12),
        ("upstream 500", [{"status": 500, "type": "internal_error", "message": "upstream exploded"}] * 12),
        ("upstream 429", [{"status": 429, "type": "rate_limit", "message": "Rate limit reached for rpm 10"}] * 12),
        ("drop after partial text", [{"drop_after": "The first half of an answer"}] * 12),
        ("error event inside the stream", [{"error_event": "Budget has been exceeded! mid-stream"}] * 12),
    ]
    results = {}
    for label, steps in cases:
        script(steps)
        d("exec", b.CAP, "node", "-e", "fetch('http://127.0.0.1:4001/reset',{method:'POST'})") if False else None
        before = len(json.loads(d("exec", b.CAP, "node", "-e", "fetch('http://127.0.0.1:4001/log').then(r=>r.text()).then(t=>console.log(t))").stdout or "[]"))
        out = d("exec", b.RELAY, "node", "-e", STREAM_JS.replace("stream-probe", "stream-" + label.split()[0]), timeout=400)
        after = json.loads(d("exec", b.CAP, "node", "-e", "fetch('http://127.0.0.1:4001/log').then(r=>r.text()).then(t=>console.log(t))").stdout or "[]")
        try:
            got = json.loads(out.stdout.strip().splitlines()[-1])
        except Exception:
            got = {"raw": (out.stdout + out.stderr)[-500:]}
        got["upstream_calls"] = len(after) - before
        results[label] = got
        print("==", label, json.dumps(got)[:1800], flush=True)
    json.dump(results, open(sys.argv[3], "w"), indent=1)
finally:
    cleanup()

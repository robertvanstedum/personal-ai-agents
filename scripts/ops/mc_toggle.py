#!/usr/bin/env python3
"""Point the dev portal's Master Craftsman at the native relay or at MC's container relay, and verify (spec §5).

    mc_toggle.py status
    mc_toggle.py to native|container [--dry-run]

Only the portal's MC_RUNTIME_URL changes (both relays accept the same caller token and model name). The portal is recreated
with its exact live compose chain plus one override file, Board is reattached to the new portal's network, and the result is
checked from inside the portal: the target relay's health, and one real relay turn with the portal's own token.
Each switch writes a receipt under ~/minimoi-runtime/operators/mc-toggle/. Rollback = `to native`.
Never prints a token or other environment value.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import time

OPS = os.path.expanduser("~/minimoi-runtime/operators/portal-paste-1db1b640")      # the reviewed compose wrappers
OUT = os.path.expanduser("~/minimoi-runtime/operators/mc-toggle")
TARGETS = {"native": "http://host.lima.internal:18796/v1", "container": "http://host.lima.internal:18797/v1"}


def run(*a, timeout=300):
    r = subprocess.run(list(a), capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def current() -> str:
    url = run("docker", "exec", "minimoi-portal", "printenv", "MC_RUNTIME_URL")[1]
    return next((k for k, v in TARGETS.items() if v == url), f"other:{url}")


def check_from_portal(target: str) -> dict:
    base = TARGETS[target].rsplit("/v1", 1)[0]
    js = f"""
import json, os, urllib.request, urllib.error
base = {base!r}
out = {{}}
try:
    out['healthz'] = urllib.request.urlopen(base + '/healthz', timeout=10).status
except Exception as e:
    out['healthz'] = type(e).__name__
req = urllib.request.Request(base + '/v1/chat/completions', method='POST',
    data=json.dumps({{'model': 'openclaw/mc-chat', 'user': 'guild-mc:toggle-check', 'stream': False,
                     'messages': [{{'role': 'user', 'content': 'Toggle check by Claude Code (owner-authorized). Reply with one short sentence; no tools.'}}]}}).encode(),
    headers={{'Content-Type': 'application/json', 'Authorization': 'Bearer ' + os.environ['MC_RUNTIME_TOKEN']}})
try:
    with urllib.request.urlopen(req, timeout=240) as r:
        out['turn_status'] = r.status
        out['turn_text'] = json.loads(r.read())['choices'][0]['message']['content'][:160]
except urllib.error.HTTPError as e:
    out['turn_status'] = e.code
except Exception as e:
    out['turn_status'] = type(e).__name__
print(json.dumps(out))
"""
    code, out = run("docker", "exec", "minimoi-portal", "python3", "-c", js, timeout=300)
    try:
        return json.loads(out.splitlines()[-1])
    except Exception:
        return {"error": out[-300:]}


def switch(target: str, dry: bool) -> int:
    os.makedirs(OUT, mode=0o700, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    override = os.path.join(OUT, f"toggle-{target}.json")
    with open(override, "w") as f:
        json.dump({"services": {"portal": {"environment": {"MC_RUNTIME_URL": TARGETS[target]}}}}, f, indent=2)
    before = current()
    receipt = {"at": stamp, "from": before, "to": target}
    # The live chain may already end in a toggle override; compose.sh '-' reuses the live chain exactly, plus ours.
    code, out = run(os.path.join(OPS, "compose.sh"), override, "config", "--quiet")
    if code != 0:
        print("refused: compose config failed:", out[-400:]); return 2
    if dry:
        print(json.dumps({**receipt, "dry_run": True})); return 0
    if run("docker", "stop", "minimoi-board-api-dev")[0] != 0:
        print("refused: could not stop Board"); return 2
    code, out = run(os.path.join(OPS, "compose.sh"), override, "up", "-d", "--no-deps", "--no-build", "--pull", "never", "portal")
    receipt["portal_up"] = code == 0
    code2, out2 = run(os.path.join(OPS, "board_compose.sh"), "-", "up", "-d", "--no-deps", "--no-build", "--pull", "never", "--force-recreate", "board-api")
    receipt["board_up"] = code2 == 0
    time.sleep(6)
    pid = run("docker", "inspect", "minimoi-portal", "--format", "{{.Id}}")[1]
    receipt["board_in_portal_namespace"] = pid in run("docker", "inspect", "minimoi-board-api-dev", "--format", "{{.HostConfig.NetworkMode}}")[1]
    try:
        import urllib.request
        receipt["portal_health"] = urllib.request.urlopen("http://127.0.0.1:5001/health", timeout=15).status
    except Exception as e:
        receipt["portal_health"] = type(e).__name__
    receipt["now_pointing_to"] = current()
    receipt["relay_check"] = check_from_portal(target)
    ok = (receipt["portal_up"] and receipt["board_up"] and receipt["board_in_portal_namespace"] and receipt["portal_health"] == 200
          and receipt["now_pointing_to"] == target and receipt["relay_check"].get("turn_status") == 200)
    receipt["ok"] = ok
    with open(os.path.join(OUT, f"RECEIPT-{stamp}-{target}.json"), "w") as f:
        json.dump(receipt, f, indent=1)
    print(json.dumps(receipt, indent=1))
    return 0 if ok else 1


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "status":
        print(json.dumps({"portal_mc_runtime": current()})); return 0
    if len(sys.argv) >= 3 and sys.argv[1] == "to" and sys.argv[2] in TARGETS:
        return switch(sys.argv[2], "--dry-run" in sys.argv)
    print(__doc__); return 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Check a MiniMoi MC relay the way the portal calls it: health, a refused wrong token, and one real chat turn with the
portal's caller token and model name. Reads the token from MC's container env file; never prints it.

    mc_relay_check.py [--url http://127.0.0.1:18797] [--model openclaw/mc-chat] [--no-turn]
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request


def token() -> str:
    for line in open(os.path.expanduser("~/minimoi-runtime/mc-container/mc.env")):
        if line.startswith("MC_RELAY_CALLER_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("no caller token in mc.env")


def call(url, path, body=None, tok=None, timeout=240):
    req = urllib.request.Request(url + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {tok}"} if tok else {})},
                                 method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:18797")
    ap.add_argument("--model", default="openclaw/mc-chat")
    ap.add_argument("--no-turn", action="store_true")
    a = ap.parse_args()
    ok = True
    s, _ = call(a.url, "/healthz")
    print("healthz", s); ok &= s == 200
    s, _ = call(a.url, "/v1/chat/completions", {"model": a.model, "user": "guild-mc:check", "messages": [{"role": "user", "content": "hi"}]}, tok="wrong-token-" + "x" * 40)
    print("wrong token ->", s); ok &= s == 401
    if not a.no_turn:
        s, body = call(a.url, "/v1/chat/completions", {"model": a.model, "user": "guild-mc:relay-check", "stream": False,
                        "messages": [{"role": "user", "content": "Relay check by Claude Code (owner-authorized). Reply with one short sentence; no tools."}]}, tok=token())
        text = ""
        try:
            text = json.loads(body)["choices"][0]["message"]["content"]
        except Exception:
            text = body[:300]
        print("turn ->", s, repr(text[:200])); ok &= s == 200 and bool(text.strip())
    print("RELAY OK" if ok else "RELAY CHECK FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

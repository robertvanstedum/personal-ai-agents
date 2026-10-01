#!/usr/bin/env python3
"""A stand-in for the Claude Code CLI in connector tests: no network, no model.

Behaviour is chosen by FAKE_CLAUDE_MODE:
  ok            init (no tools) then one result event with text and usage
  no_output     starts, prints nothing, exits 1
  bad_init      init lists a tool (the boundary must refuse)
  error_result  init, then a result event with is_error true
  slow          init, then sleeps until killed
  huge          init, then more than 1 MB of assistant text
  signed_out    `auth status` reports loggedIn false
The event shapes follow the CLI's stream-json output as documented; the real
shapes are checked inside the owner-approved Prove (ROOMS_R2.md §3.7).
FAKE_CLAUDE_LOG, if set, receives the argument list and environment keys.
"""
import json
import os
import sys
import time

mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
log = os.environ.get("FAKE_CLAUDE_LOG")
if log:
    with open(log, "a") as f:
        f.write(json.dumps({"argv": sys.argv[1:], "env": sorted(os.environ), "cwd": os.getcwd()}) + "\n")

if sys.argv[1:3] == ["auth", "status"]:
    out = {"loggedIn": mode != "signed_out", "authMethod": "claude.ai" if mode != "signed_out" else "none",
           "apiProvider": "firstParty"}
    print(json.dumps(out))
    sys.exit(0)
if sys.argv[1:2] == ["--version"]:
    print("2.1.76 (Claude Code)")
    sys.exit(0)

prompt = sys.stdin.read()
if mode == "no_output":
    sys.exit(1)


def emit(event):
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


emit({"type": "system", "subtype": "init", "session_id": "fake", "tools": ["Read"] if mode == "bad_init" else [],
      "mcp_servers": [], "model": "fake-model", "permissionMode": "default", "plugins": []})
if mode == "slow":
    while True:
        time.sleep(0.2)
if mode == "huge":
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": "x" * 1_200_000}]}})
    sys.exit(0)
if mode == "error_result":
    emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": ""})
    sys.exit(1)
text = "A short useful point from Claude Code." if "boundary" not in prompt else "I have no tools here."
emit({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}})
emit({"type": "result", "subtype": "success", "is_error": False, "result": text,
      "usage": {"input_tokens": 120, "output_tokens": 14}})

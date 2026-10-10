#!/usr/bin/env python3
"""A stand-in for the Codex CLI in connector tests: no network, no model.

`--version` and `login status` answer directly. `app-server` speaks the
JSON-RPC shapes of codex app-server 0.145.0 (from its generated schema) over
stdio. Behaviour is chosen by FAKE_CODEX_MODE:
  ok             one agentMessage, usage, turn completed
  empty          turn completed with no agentMessage
  item_<type>    an item of that type starts (e.g. item_commandExecution)
  server_request a server request (item/tool/requestUserInput) arrives
  unauthorized   turn failed, codexErrorInfo unauthorized, no output, no usage
  late_unauth    an agentMessage and usage, then turn failed unauthorized
  usage_limit    turn failed, usageLimitExceeded, no output
  slow           turn started, then nothing (still reads: answers turn/interrupt)
  silent         never answers initialize (a stalled handshake)
  delta_unauth   an agentMessage delta, then turn failed unauthorized (no item, no usage)
  malformed      a non-JSON line after turn/started
  api_key        `login status` reports an API key sign-in
  signed_out     `login status` reports not logged in
FAKE_CODEX_LOG, if set, receives argv and the environment keys; FAKE_CODEX_METHODS, if set,
receives every JSON-RPC method the fake reads (to see turn/interrupt).
"""
import json
import os
import sys
import time

mode = os.environ.get("FAKE_CODEX_MODE", "ok")
log = os.environ.get("FAKE_CODEX_LOG")
if log:
    with open(log, "a") as f:
        f.write(json.dumps({"argv": sys.argv[1:], "env": sorted(os.environ), "cwd": os.getcwd()}) + "\n")

if sys.argv[1:2] == ["--version"]:
    print("codex-cli 0.145.0")
    sys.exit(0)
if sys.argv[1:3] == ["login", "status"]:
    print({"api_key": "Logged in using an API key - sk-***", "signed_out": "Not logged in"}.get(mode, "Logged in using ChatGPT"))
    sys.exit(0)


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def note(method, params):
    send({"method": method, "params": params})


def item(kind, text=None, done=True):
    body = {"type": kind, "id": f"item_{kind}"}
    if text is not None:
        body["text"] = text
    note("item/started", {"threadId": "th", "turnId": "tu", "item": body})
    if done:
        note("item/completed", {"threadId": "th", "turnId": "tu", "item": body})


def usage(inp, out):
    b = {"inputTokens": inp, "outputTokens": out, "cachedInputTokens": 0, "reasoningOutputTokens": 0,
         "totalTokens": inp + out}
    note("thread/tokenUsage/updated", {"threadId": "th", "turnId": "tu", "tokenUsage": {"last": b, "total": b}})


def completed(status, error=None):
    note("turn/completed", {"threadId": "th", "turn": {"id": "tu", "items": [], "status": status, "error": error}})


methods_log = os.environ.get("FAKE_CODEX_METHODS")
for line in sys.stdin:
    msg = json.loads(line)
    method, rid = msg.get("method"), msg.get("id")
    if methods_log:
        with open(methods_log, "a") as f:
            f.write(f"{method}\n")
    if mode == "silent":
        continue
    if method == "initialize":
        send({"id": rid, "result": {"userAgent": "fake"}})
    elif method == "thread/start":
        send({"id": rid, "result": {"thread": {"id": "th"}}})
        note("thread/started", {"thread": {"id": "th"}})
    elif method == "turn/interrupt":
        send({"id": rid, "result": {}})
        completed("interrupted")
    elif method == "turn/start":
        send({"id": rid, "result": {"turn": {"id": "tu", "items": [], "status": "inProgress"}}})
        note("turn/started", {"threadId": "th", "turn": {"id": "tu", "items": [], "status": "inProgress"}})
        item("userMessage")
        if mode == "ok":
            item("reasoning")
            item("agentMessage", "A short useful point from Codex.")
            usage(120, 14)
            completed("completed")
        elif mode == "empty":
            completed("completed")
        elif mode.startswith("item_"):
            item(mode[5:], done=False)
        elif mode == "server_request":
            send({"id": 99, "method": "item/tool/requestUserInput", "params": {"threadId": "th", "turnId": "tu"}})
        elif mode == "delta_unauth":
            note("item/agentMessage/delta", {"threadId": "th", "turnId": "tu", "itemId": "m", "delta": "Already thinking aloud"})
            completed("failed", {"message": "401", "codexErrorInfo": "unauthorized"})
        elif mode == "unauthorized":
            completed("failed", {"message": "401", "codexErrorInfo": "unauthorized"})
        elif mode == "late_unauth":
            item("agentMessage", "Partly")
            usage(100, 12)
            completed("failed", {"message": "401", "codexErrorInfo": "unauthorized"})
        elif mode == "usage_limit":
            completed("failed", {"message": "limit", "codexErrorInfo": "usageLimitExceeded"})
        elif mode == "slow":
            pass                                           # keep reading; only turn/interrupt ends it
        elif mode == "malformed":
            sys.stdout.write("not json\n")
            sys.stdout.flush()

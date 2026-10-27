#!/usr/bin/env python3
"""Say why you stopped, ask Robert a decision, and wait for an answer or a nudge (#365; AGENTS.md "Stop line").

  stop.py line --state STATE [--needs "..."] [--next "..."] [--pr N] [--issue N]
      print the one machine-readable stop line and file it for MC's check-ins.
      STATE: done | blocked | awaiting_owner | awaiting_review | waiting_ci | error
  stop.py ask "exact decision, naming what it covers (PR, commit, service)" [--pr N] [--issue N]
      file a decision for Robert (sent to him on Telegram word for word; he answers there or in the Guild chat),
      and print the matching awaiting_owner stop line. Prints the decision key.
  stop.py wait [--key KEY] [--timeout SECONDS]
      block until Robert answers KEY, or MC nudges this session; print what arrived (JSON) and exit 0.
      Claude Code: run it with run_in_background before ending the turn; its exit wakes the session.
      Exit 3 on timeout (default 8 h): re-arm it if still waiting.

Stdlib only; files under ~/.minimoi/supervision (MINIMOI_SUPERVISION_DIR). scripts/mc_host/supervision_sync.py
carries them to and from production. Nothing here can approve anything: answers come only from Robert's own reply.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

STATES = ("done", "blocked", "awaiting_owner", "awaiting_review", "waiting_ci", "error")
PREFIX = "MINIMOI-STOP "
LINE = re.compile(r"^MINIMOI-STOP (\{.*\})\s*$", re.M)


def root():
    return Path(os.environ.get("MINIMOI_SUPERVISION_DIR") or Path.home() / ".minimoi" / "supervision")


def _dir(*parts):
    p = root().joinpath(*parts)
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    return p


def _publish(path, value):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, sort_keys=True))
    os.replace(tmp, path)


def identity(args=None):
    """(agent, session): Claude Code's own session id, else what the caller names (Codex: --session)."""
    session = getattr(args, "session", None) or os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CODEX_SESSION_ID")
    agent = getattr(args, "agent", None) or ("claude-code" if os.environ.get("CLAUDECODE") or
                                             os.environ.get("CLAUDE_CODE_SESSION_ID") else "codex")
    if not session or not re.fullmatch(r"[A-Za-z0-9._:-]{4,96}", session):
        sys.exit("stop.py: no session id (Claude Code sets one; Codex passes --session <thread id>)")
    return agent, session


def branch(cwd=None):
    try:
        return subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd, capture_output=True, text=True,
                              timeout=5).stdout.strip()[:200]
    except Exception:
        return ""


def report_key(session, line, at=None):
    """The same line from the same session within the same hour is one report (stop.py files it, then the Claude Code
    hook sees it again at the end of the turn)."""
    hour = int((at or time.time()) // 3600)
    return "stop-" + hashlib.sha256(f"{session}\n{line}\n{hour}".encode()).hexdigest()[:40]


def waiting(session):
    """True while a `stop.py wait` for this session is alive."""
    marker = root() / "waiting" / f"{session}.pid"
    try:
        os.kill(int(marker.read_text()), 0)
        return True
    except Exception:
        return False


def file_stop(agent, session, fields, line, *, stopped_at=None):
    at = stopped_at or time.time()
    record = {"report_key": report_key(session, line, at), "agent": agent, "session": session,
              "stopped_at": at, "waiting": waiting(session), **fields}
    _publish(_dir("outbox", "stops") / f"{record['report_key']}.json", record)
    return record


def make_line(state, needs="", next_="", decision_key=None, pr=None, issue=None):
    if state not in STATES:
        sys.exit(f"stop.py: state must be one of {', '.join(STATES)}")
    fields = {"state": state, "needs": needs.strip()[:500], "next": next_.strip()[:500]}
    if decision_key:
        fields["decision_key"] = decision_key
    if pr:
        fields["pr"] = pr
    if issue:
        fields["issue"] = issue
    return fields, PREFIX + json.dumps(fields, sort_keys=True, ensure_ascii=False)


def parse_line(text):
    """The LAST stop line in ``text`` as a dict of known fields, or None."""
    found = LINE.findall(text or "")
    if not found:
        return None
    try:
        value = json.loads(found[-1])
    except ValueError:
        return None
    if type(value) is not dict or value.get("state") not in STATES:
        return None
    keep = {k: value[k] for k in ("state", "needs", "next", "decision_key", "pr", "issue") if k in value}
    for k in ("needs", "next"):
        keep[k] = str(keep.get(k, ""))[:500]
    for k in ("pr", "issue"):
        if k in keep and not (type(keep[k]) is int and 1 <= keep[k] <= 999999):
            keep.pop(k)
    if "decision_key" in keep and not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", str(keep["decision_key"])):
        keep.pop("decision_key")
    return keep


def cmd_line(args):
    agent, session = identity(args)
    fields, line = make_line(args.state, args.needs, args.next, None, args.pr, args.issue)
    fields["branch"] = branch()
    file_stop(agent, session, fields, line)
    print(line)


def cmd_ask(args):
    agent, session = identity(args)
    text = " ".join(args.text.split())
    if not 10 <= len(text) <= 1500:
        sys.exit("stop.py: the decision must be 10 to 1500 characters, in the exact words Robert approves")
    key = "ask-" + uuid.uuid4().hex
    _publish(_dir("outbox", "asks") / f"{key}.json",
             {"request_key": key, "agent": agent, "session": session, "branch": branch(), "text": text})
    fields, line = make_line("awaiting_owner", "Robert's decision: " + text[:470], args.next or "", key, args.pr, args.issue)
    fields["branch"] = branch()
    file_stop(agent, session, fields, line)
    print(f"decision key: {key}")
    print(line)


def inbox(session):
    return _dir("inbox", session)


def cmd_wait(args):
    agent, session = identity(args)
    marker = _dir("waiting") / f"{session}.pid"
    marker.write_text(str(os.getpid()))
    deadline = time.time() + args.timeout
    try:
        while time.time() < deadline:
            arrived = []
            for f in sorted(inbox(session).glob("*.json")):
                item = json.loads(f.read_text())
                if item.get("kind") == "decision" and args.key and item.get("request_key") != args.key:
                    continue          # another decision of this session: left for its own waiter
                arrived.append(item)
                f.rename(_dir("seen", session) / f.name)
            if arrived:
                print(json.dumps(arrived, indent=2, ensure_ascii=False))
                return 0
            time.sleep(10)
        print(json.dumps({"kind": "timeout", "session": session, "key": args.key}))
        return 3
    finally:
        try:
            if marker.read_text() == str(os.getpid()):
                marker.unlink()
        except FileNotFoundError:
            pass


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("line", "ask", "wait"):
        s = sub.add_parser(name)
        s.add_argument("--session")
        s.add_argument("--agent", choices=("claude-code", "codex", "grok"))
        if name in ("line", "ask"):
            s.add_argument("--pr", type=int)
            s.add_argument("--issue", type=int)
            s.add_argument("--next", default="")
    sub.choices["line"].add_argument("--state", required=True, choices=STATES)
    sub.choices["line"].add_argument("--needs", default="")
    sub.choices["ask"].add_argument("text")
    sub.choices["wait"].add_argument("--key")
    sub.choices["wait"].add_argument("--timeout", type=int, default=8 * 3600)
    args = p.parse_args(argv)
    return {"line": cmd_line, "ask": cmd_ask, "wait": cmd_wait}[args.cmd](args) or 0


if __name__ == "__main__":
    sys.exit(main())

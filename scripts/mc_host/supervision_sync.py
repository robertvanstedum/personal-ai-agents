#!/usr/bin/env python3
"""One sync of build supervision between this Mac and production (#365). launchd runs it every 30 s
(scripts/mc_host/install_supervision_sync.sh); outbound HTTPS only, so the Mac opens no port.

  up:   stop lines and decision asks that agents filed with scripts/agents/stop.py (~/.minimoi/supervision/outbox),
        and the agents' exchanges in the request ledger (asks and replies between MC, Claude Code, Codex and Grok),
        so MC on production can read them; each ledger entry is sent once
  down: Robert's answers to those decisions (with their receipts) and MC's fixed nudges, into each session's inbox,
        where a waiting `stop.py wait` picks them up and wakes its agent; then acknowledged.

Its token is in the login Keychain (service minimoi_supervision, account sync_token); production keeps only its
SHA-256. The token can report and ask, never answer. Stdlib only; never prints the token.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

ENDPOINT = os.environ.get("MINIMOI_SUPERVISION_URL", "https://minimoi.ai/internal/supervision/v1/worker")
ALLOWED = ("https://minimoi.ai/internal/supervision/v1/worker", "https://dev.minimoi.ai/internal/supervision/v1/worker")
ROOT = Path(os.environ.get("MINIMOI_SUPERVISION_DIR") or Path.home() / ".minimoi" / "supervision")
LEDGER = Path(os.environ.get("MINIMOI_AGENT_REQUESTS_DIR") or Path.home() / "minimoi-staging/data/guild/agent_requests")
REPLY_KINDS = ("receipt", "progress", "result", "needs_you", "cancel")
WINDOW_S = 7 * 86400          # only requests touched in the last week are read
MAX_EXCHANGES = 50


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError("supervision_redirect_refused")


def token():
    value = subprocess.run(["security", "find-generic-password", "-s", "minimoi_supervision", "-a", "sync_token", "-w"],
                           capture_output=True, text=True, timeout=10).stdout.strip()
    if not 32 <= len(value) <= 256:
        sys.exit("supervision sync: no token in the Keychain (run install_supervision_sync.sh)")
    return value


def call(body, secret):
    if ENDPOINT not in ALLOWED:
        raise RuntimeError("supervision_endpoint_invalid")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    req = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + secret,
                                          "User-Agent": "MiniMoi-supervision-sync/1"})
    with opener.open(req, timeout=20) as r:
        return json.loads(r.read(262144))


def _pending(kind, limit):
    folder = ROOT / "outbox" / kind
    return sorted(folder.glob("*.json"))[:limit] if folder.is_dir() else []


def _publish(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, sort_keys=True, ensure_ascii=False))
    os.replace(tmp, path)


def _epoch(stamp):
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def exchanges(sent_keys, now):
    """New ledger entries, oldest first: each request's ask, then the recipient's replies. Never more than
    MAX_EXCHANGES per sync; a damaged file is skipped, never guessed at."""
    found = []
    folder = LEDGER / "requests"
    if not folder.is_dir():
        return found
    for req_dir in folder.iterdir():
        try:
            if req_dir.is_symlink() or not req_dir.is_dir() or now - req_dir.stat().st_mtime > WINDOW_S:
                continue
            req = json.loads((req_dir / "request.json").read_text())
            rid, frm, to = req["request_id"], req["from"], req["to"]
            if f"{rid}:ask" not in sent_keys:
                found.append({"request_id": rid, "event_id": "ask", "kind": "ask", "actor": frm, "to": to,
                              "at": _epoch(req["created_at"]), "text": (req.get("action") or "")[:600]})
            for line in (req_dir / "events.jsonl").read_text().splitlines():
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("kind") in REPLY_KINDS and f"{rid}:{ev.get('event_id')}" not in sent_keys:
                    found.append({"request_id": rid, "event_id": ev["event_id"], "kind": ev["kind"],
                                  "actor": ev.get("actor"), "to": frm, "at": _epoch(ev["at"]),
                                  "text": (ev.get("text") or "")[:600]})
        except (OSError, KeyError, ValueError, TypeError):
            continue
    return sorted(found, key=lambda e: e["at"])[:MAX_EXCHANGES]


def _sent_keys():
    path = ROOT / "exchanges-sent.json"
    try:
        return set(json.loads(path.read_text()))
    except (OSError, ValueError):
        return set()


def up(secret, now=None):
    now = now or time.time()
    stops, asks = _pending("stops", 20), _pending("asks", 5)
    seen = _sent_keys()
    news = exchanges(seen, now)
    if not stops and not asks and not news:
        return {}
    body = {"phase": "report", "stops": [json.loads(f.read_text()) for f in stops],
            "asks": [json.loads(f.read_text()) for f in asks], "exchanges": news}
    answer = call(body, secret)
    if answer.get("exchanges"):
        keep = sorted(seen | set(answer["exchanges"]))[-5000:]
        _publish(ROOT / "exchanges-sent.json", keep)
    sent = ROOT / "sent"
    sent.mkdir(parents=True, exist_ok=True, mode=0o700)
    for f in stops + asks:
        f.rename(sent / f.name)
    for a in answer.get("asks", []):
        _publish(ROOT / "asked" / f"{a['request_key']}.json", a)    # the D-code Robert sees, for the agent to quote
    return answer


def down(secret):
    got = call({"phase": "pull"}, secret)
    acked = {"decisions": [], "nudges": []}
    for d in got.get("decisions", []):
        if d.get("session"):
            _publish(ROOT / "inbox" / d["session"] / f"decision-{d['request_key']}.json", dict(d, kind="decision"))
        acked["decisions"].append(d["request_key"])
    for n in got.get("nudges", []):
        _publish(ROOT / "inbox" / n["session"] / f"nudge-{n['id']}.json", dict(n, kind="nudge", nudge=n["kind"]))
        acked["nudges"].append(n["id"])
    if acked["decisions"] or acked["nudges"]:
        call({"phase": "ack", **acked}, secret)
    return acked


def main():
    secret = token()
    sent = up(secret)
    got = down(secret)
    if sent or got.get("decisions") or got.get("nudges"):
        print(json.dumps({"up": {k: len(v) for k, v in sent.items()},
                          "down": {k: len(v) for k, v in got.items()}}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"supervision sync failed: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)

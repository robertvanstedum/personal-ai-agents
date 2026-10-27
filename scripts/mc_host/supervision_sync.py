#!/usr/bin/env python3
"""One sync of build supervision between this Mac and production (#365). launchd runs it every 30 s
(scripts/mc_host/install_supervision_sync.sh); outbound HTTPS only, so the Mac opens no port.

  up:   stop lines and decision asks that agents filed with scripts/agents/stop.py (~/.minimoi/supervision/outbox),
        and the agents' exchanges in the request ledgers (asks and replies between MC, Claude Code, Codex and Grok),
        so MC on production can read them; each ledger entry is sent once. Then the relay (OPS-2026-10-27): the agents'
        replies to the requests Robert put to them through MC on production, each once, and which agents are listening.
  down: Robert's answers to those decisions (with their receipts) and MC's fixed nudges, into each session's inbox,
        where a waiting `stop.py wait` picks them up and wakes its agent; and Robert's requests to the agents from
        production, each created in the shared ledger with the same id and content; then acknowledged.

Its token is in the login Keychain (service minimoi_supervision, account sync_token); production keeps only its
SHA-256. The token can report, ask and relay, never answer. Stdlib only (it runs outside any checkout, so the ledger's
own create is mirrored here, byte for byte); never prints the token.
"""
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
import urllib.request

ENDPOINT = os.environ.get("MINIMOI_SUPERVISION_URL", "https://minimoi.ai/internal/supervision/v1/worker")
ALLOWED = ("https://minimoi.ai/internal/supervision/v1/worker", "https://dev.minimoi.ai/internal/supervision/v1/worker")
ROOT = Path(os.environ.get("MINIMOI_SUPERVISION_DIR") or Path.home() / ".minimoi" / "supervision")
LEDGER = Path(os.environ.get("MINIMOI_AGENT_REQUESTS_DIR") or Path.home() / "minimoi-staging/data/guild/agent_requests")
# The MC worker's own ledger (core/agent_requests/direct_contact.Outbox): read for the mirror too.
WORKER_LEDGER = Path(os.environ.get("MINIMOI_MC_WORKER_LEDGER_DIR")
                     or Path.home() / "minimoi-runtime/mc-prod-worker/outbox/direct-targets-v1")
REPLY_KINDS = ("receipt", "progress", "result", "needs_you", "cancel")
WINDOW_S = 7 * 86400          # only requests touched in the last week are read
MAX_EXCHANGES = 50
# The relay, as production checks it (core/supervision/relay.py).
RECIPIENTS = ("claude-code", "codex", "grok")
RELAY_ACTORS = ("mc", "claude-code", "codex", "grok")
TEXT_KINDS = ("progress", "result", "needs_you")
TEXT_MAX = 16 * 1024
ACTION_MAX = 4000
MAX_RELAY_EVENTS, MAX_PULL = 20, 20
LISTENING_S = 120
RELAY_BYTES = 196608          # one relay report stays well under production's 256 KiB body bound
_RID = re.compile(r"^r-[0-9a-f]{20,40}$")
_CID = re.compile(r"^(shop-floor-thread|c-[0-9a-f]{8,32})$")
_EID = re.compile(r"^[0-9a-f]{8,40}$")
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError("supervision_redirect_refused")


def token():
    value = subprocess.run(["security", "find-generic-password", "-s", "minimoi_supervision", "-a", "sync_token", "-w"],
                           capture_output=True, text=True, timeout=10).stdout.strip()
    if not 32 <= len(value) <= 256:
        sys.exit("supervision sync: no token in the Keychain (run install_supervision_sync.sh)")
    return value


def _body(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def call(body, secret):
    if ENDPOINT not in ALLOWED:
        raise RuntimeError("supervision_endpoint_invalid")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    req = urllib.request.Request(ENDPOINT, data=_body(body), method="POST",
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
    """New ledger entries from both ledgers (the shared one, then the MC worker's), oldest first: each request's ask,
    then the recipient's replies; a request in both is read once. Never more than MAX_EXCHANGES per sync; a damaged
    file is skipped, never guessed at."""
    found, seen = [], set()
    for ledger in (LEDGER, WORKER_LEDGER):
        folder = ledger / "requests"
        if not folder.is_dir():
            continue
        for req_dir in folder.iterdir():
            try:
                if req_dir.is_symlink() or not req_dir.is_dir() or now - req_dir.stat().st_mtime > WINDOW_S:
                    continue
                req = json.loads((req_dir / "request.json").read_text())
                rid, frm, to = req["request_id"], req["from"], req["to"]
                if rid in seen:
                    continue
                seen.add(rid)
                if f"{rid}:ask" not in sent_keys:
                    found.append({"request_id": rid, "event_id": "ask", "kind": "ask", "actor": frm, "to": to,
                                  "at": _epoch(req["created_at"]), "text": (req.get("action") or "")[:600]})
                for ev in _events(req_dir):
                    if ev.get("kind") in REPLY_KINDS and f"{rid}:{ev.get('event_id')}" not in sent_keys:
                        found.append({"request_id": rid, "event_id": ev["event_id"], "kind": ev["kind"],
                                      "actor": ev.get("actor"), "to": frm, "at": _epoch(ev["at"]),
                                      "text": (ev.get("text") or "")[:600]})
            except (OSError, KeyError, ValueError, TypeError):
                continue
    return sorted(found, key=lambda e: e["at"])[:MAX_EXCHANGES]


def _events(req_dir):
    out = []
    path = req_dir / "events.jsonl"
    if path.is_symlink() or not path.exists():
        return out
    for line in path.read_text().splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict):
            out.append(ev)
    return out


def _keys(name):
    try:
        return set(json.loads((ROOT / name).read_text()))
    except (OSError, ValueError, TypeError):
        return set()


def _remember(name, have, new, keep=5000):
    if new - have:
        _publish(ROOT / name, sorted(have | new)[-keep:])


def _sent_keys():
    return _keys("exchanges-sent.json")


# ── the relay: the ledger's own create, mirrored (core/agent_requests/ledger.create_request) ─────────────────────
def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dir(path):
    if path.is_symlink():
        raise OSError("ledger folder is a link")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise OSError("ledger folder is not a folder")


def _append(path, line):
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, (line + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_new(path, data):
    tmp = path.with_name(f"{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            return False
        return True
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _delivery():
    return json.dumps({"event_id": secrets.token_hex(8), "kind": "delivery", "actor": "ledger", "at": _now(),
                       "state": "queued", "adapter": "pending_file"})


def create_relayed(root, r):
    """Create one request pulled from production in the ledger at ``root`` with the same id and content, exactly as
    ``create_request(frm="mc", origin="owner_message_via_mc")`` writes it: the request file once, its first delivery
    event, then the recipient's wake hint. True when it is there with that content (new or already); False when the
    pull does not check out (bad fields, or content that does not match production's hash) or the id holds another
    request. Raises OSError when the disk refuses, so it is neither acked nor remembered."""
    try:
        rid, to, action, cid = r["request_id"], r["to"], r["action"], r["conversation_id"]
        digest, created_at = r["content_sha256"], r["created_at"]
    except (KeyError, TypeError):
        return False
    if (type(rid) is not str or not _RID.match(rid) or to not in RECIPIENTS or type(action) is not str
            or not action.strip() or len(action) > ACTION_MAX or action != action.strip() or "\r" in action
            or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", action)
            or not (cid is None or (type(cid) is str and _CID.match(cid)))):
        return False
    body = {"v": 1, "request_id": rid, "from": "mc", "to": to, "action": action, "conversation_id": cid,
            "topic_id": None, "pointer": None, "origin": "owner_message_via_mc"}
    if hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest() != digest:
        return False
    try:
        _epoch(created_at)
    except (TypeError, ValueError):
        created_at = _now()
    request = {**body, "created_at": created_at, "content_sha256": digest}
    folder = root / "requests" / rid
    _dir(root / "requests")
    _dir(folder)
    path = folder / "request.json"
    if not _write_new(path, json.dumps(request, ensure_ascii=False, sort_keys=True).encode("utf-8")):
        try:
            have = json.loads(path.read_text())
        except (OSError, ValueError):
            return False
        if have.get("content_sha256") != digest:
            return False
        if not any(e.get("kind") == "delivery" for e in _events(folder)):
            _append(folder / "events.jsonl", _delivery())
        return True
    _append(folder / "events.jsonl", _delivery())
    _dir(root / "pending")
    _append(root / "pending" / f"{to}.log",
            f"REQUEST {rid} from mc to {to} · agent-authored, not a message or approval from Robert"
            f" · read it with the helper: scripts/agent_requests.py show {rid}")
    return True


def listeners(now):
    """Seconds since each agent's watcher last said it was listening in the shared ledger, for those seen lately."""
    out = {}
    for agent in RECIPIENTS:
        try:
            age = max(0.0, now - (LEDGER / "pending" / f"{agent}.armed").stat().st_mtime)
        except OSError:
            continue
        if age <= LISTENING_S:
            out[agent] = round(age, 1)
    return out


def relay_events(relayed, sent):
    """The agents' replies to relayed requests, not yet sent, oldest first, as production checks them. A reply that
    production would refuse whatever happens (too long, a damaged line) is returned in ``skip`` and never sent."""
    found, skip = [], set()
    for rid in sorted(relayed):
        folder = LEDGER / "requests" / rid
        if not _RID.match(rid) or folder.is_symlink() or not folder.is_dir():
            continue
        for ev in _events(folder):
            eid, kind = ev.get("event_id"), ev.get("kind")
            if kind not in REPLY_KINDS or type(eid) is not str or not _EID.match(eid) or f"{rid}:{eid}" in sent:
                continue
            text = ev.get("text") or "" if kind in TEXT_KINDS else ""
            if (ev.get("actor") not in RELAY_ACTORS or type(text) is not str or len(text) > TEXT_MAX
                    or _CTRL.search(text) or (kind in TEXT_KINDS and not text.strip())):
                skip.add(f"{rid}:{eid}")
                continue
            found.append((ev.get("at") or "", {"request_id": rid, "event_id": eid, "kind": kind,
                                               "actor": ev["actor"], **({"text": text} if text else {})}))
    found.sort(key=lambda x: x[0])
    return [e for _, e in found], skip


def relay_up(secret, now):
    """The agents' replies to Robert's relayed requests, packed under RELAY_BYTES (always at least one), and which
    agents are listening. A reply production refused for want of its relay marker is tried again next time."""
    relayed, sent = _keys("relayed.json"), _keys("relay-sent.json")
    events, skip = relay_events(relayed, sent)
    ages = listeners(now)
    _remember("relay-sent.json", sent, skip)
    if not events and not ages:
        return {}
    body = {"phase": "report", "relay_events": [], "listeners": ages}
    for e in events[:MAX_RELAY_EVENTS]:
        if body["relay_events"] and len(_body(body)) + len(_body(e)) + 2 > RELAY_BYTES:
            break
        body["relay_events"].append(e)
    answer = call(body, secret)
    done = {o["key"] for o in answer.get("relay_events", []) if isinstance(o, dict) and isinstance(o.get("key"), str)
            and (o.get("state") in ("applied", "duplicate")
                 or (o.get("state") == "refused" and o.get("code") not in ("not_relayed", "relay_off")))}
    _remember("relay-sent.json", sent | skip, done)
    return {"relay_events": answer.get("relay_events", []), "listeners": answer.get("listeners", [])}


def up(secret, now=None):
    now = now or time.time()
    stops, asks = _pending("stops", 20), _pending("asks", 5)
    seen = _sent_keys()
    news = exchanges(seen, now)
    answer = {}
    if stops or asks or news:
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
    try:
        answer.update(relay_up(secret, now))
    except Exception as exc:                           # e.g. a production without the relay yet: decisions still go down
        print(f"supervision sync: relay up failed: {type(exc).__name__}", file=sys.stderr)
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
    made = []
    for r in (got.get("requests") or [])[:MAX_PULL]:
        try:
            if isinstance(r, dict) and create_relayed(LEDGER, r):
                made.append(r["request_id"])
        except OSError:
            continue                                   # not created, so not acked: production offers it again
    if acked["decisions"] or acked["nudges"] or made:
        answer = call({"phase": "ack", **acked, **({"requests": made} if made else {})}, secret)
        # Replies go up only for requests production marked relayed; one not marked is pulled and acked again.
        ok = {rid for rid in answer.get("requests", []) if isinstance(rid, str) and rid in made}
        _remember("relayed.json", _keys("relayed.json"), ok, keep=2000)
    return dict(acked, requests=made)


def main():
    secret = token()
    sent = up(secret)
    got = down(secret)
    if any(v for k, v in sent.items() if k != "listeners") or any(got.values()):
        print(json.dumps({"up": {k: len(v) for k, v in sent.items()},
                          "down": {k: len(v) for k, v in got.items()}}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"supervision sync failed: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)

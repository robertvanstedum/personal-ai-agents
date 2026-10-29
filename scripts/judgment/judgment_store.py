#!/usr/bin/env python3
"""Thin capture-and-adopt machinery for the judgment apprenticeship (spec v0.2 §3). Not an agent runtime; standard library only.

    judgment_store.py collect [--apply]                     move MC capture blocks from session records into the private store
    judgment_store.py freeze  --conversation C | --session S [--label L] [--apply]
                                                            freeze one conversation's records as a replay scenario
    judgment_store.py adopt   --card FILE --name N --approved-by robert --approval "<Robert's words>" [--apply]
                                                            record an owner-adopted card (versioned, hashed); prints the persona block to project
    judgment_store.py status                                what is collected, adopted and confirmed loaded

Sources are read-only: the mc-chat native session database is opened with ``mode=ro`` and never written. Everything this tool
writes lives under the private store (default ``~/minimoi-private/judgment``), which no agent runtime mounts. Without ``--apply``
every command only reports what it would do.

Store layout (file-first, append-only where it matters):
    correction-pairs.md        pairs, verbatim, each followed by a provenance comment (session, seq, event id, collected-at)
    capture-failures.md        a receipt that does not match its blocks, a malformed block (kept verbatim), or a read error
    defects.md                 "+1 defect noted" receipts, for the parity track
    adoption-receipts.md       "Cards loaded: name@hash" lines seen in MC replies, checked against cards/registry.json
    scenarios/<id>/            frozen records (events.jsonl) + manifest.json with the snapshot hash
    cards/<name>/<n>.md        adopted card versions; cards/registry.json is the index (current version per card, hashes)
    .collector-state.json      watermark (last transcript rowid) and recently collected event ids

Adoption is complete only when a later MC reply carries ``Cards loaded: <name>@<hash>`` with the adopted hash (``status`` says
which cards are confirmed). A hash in the registry alone is not an adoption."""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

DEFAULT_STORE = Path.home() / "minimoi-private" / "judgment"
DEFAULT_DB = Path.home() / ".openclaw" / "agents" / "mc-chat" / "agent" / "openclaw-agent.sqlite"
FIELDS = ("when", "task", "did", "owner-said", "should-have", "tag", "source")
TAGS = {"scope", "role", "authorization", "priority", "evidence", "effort"}
BLOCK = re.compile(r"```capture[ \t]*\n(.*?)\n```", re.S)
RECEIPT = re.compile(r"Captured (\d+) correction pairs?", re.I)
NO_CORRECTIONS = re.compile(r"No corrections to capture", re.I)
DEFECT = re.compile(r"\+1 defect noted[^\n]*", re.I)
CARDS = re.compile(r"Cards loaded:\s*([^\n]+)")
CARD_REF = re.compile(r"([A-Za-z0-9][\w.\-]{0,63})@([0-9a-f]{12,64})")
NAME = re.compile(r"^[a-z0-9][a-z0-9\-]{1,63}$")
KEEP_IDS = 2000


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def append(path: Path, text: str, header: str = "") -> None:
    """Append-only write with fsync; creates the file (0600) with ``header`` the first time."""
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        if new and header:
            f.write(header)
        f.write(text)
        f.flush()
        os.fsync(f.fileno())


@contextlib.contextmanager
def locked(store: Path):
    store.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(store / ".lock", os.O_WRONLY | os.O_CREAT, 0o600)
    with os.fdopen(fd, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("refused: another judgment_store run holds the lock") from None
        yield


def open_ro(db: Path) -> sqlite3.Connection:
    if not db.exists():
        raise SystemExit(f"refused: session database not found: {db}")
    return sqlite3.connect(f"{db.absolute().as_uri()}?mode=ro", uri=True)


def text_of(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    parts = []
    for part in content or []:
        if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
            parts.append(part["text"])
    return "\n".join(parts)


def parse_block(body: str) -> tuple[dict | None, str]:
    fields, current = {}, None
    for line in body.splitlines():
        m = re.match(r"^([a-z\-]+):\s?(.*)$", line)
        if m and m.group(1) in FIELDS:
            current = m.group(1)
            if current in fields:
                return None, f"field '{current}' repeated"
            fields[current] = m.group(2).strip()
        elif current and line.strip():
            fields[current] += " " + line.strip()          # a wrapped line continues the previous field
    missing = [f for f in FIELDS if not fields.get(f)]
    if missing:
        return None, "missing " + ", ".join(missing)
    tags = [t.strip() for t in fields["tag"].split("|")]
    if not all(t in TAGS or t.startswith("propose-new:") for t in tags):
        return None, f"unknown tag '{fields['tag']}'"
    return fields, ""


def load_state(store: Path) -> dict:
    p = store / ".collector-state.json"
    return json.loads(p.read_text()) if p.exists() else {"last_rowid": 0, "event_ids": []}


# ── collect ──────────────────────────────────────────────────────────────────

def collect(db: Path, store: Path, apply: bool, exclude: list[str] | None = None) -> dict:
    state = load_state(store)
    seen = set(state.get("event_ids", []))
    excluded = set(state.get("excluded_sessions", [])) | set(exclude or [])      # test sessions: never collected, recorded in state
    con = open_ro(db)
    conv = dict(con.execute("select session_id, conversation_id from session_conversations").fetchall())
    rows = con.execute("select rowid, session_id, seq, event_json from transcript_events where rowid > ? order by rowid",
                       (state["last_rowid"],)).fetchall()
    con.close()
    registry = load_registry(store)
    out = {"scanned": len(rows), "pairs": [], "failures": [], "defects": [], "cards_seen": [], "watermark": state["last_rowid"]}
    for rowid, session, seq, raw in rows:
        out["watermark"] = rowid
        try:
            ev = json.loads(raw)
        except ValueError:
            out["failures"].append((f"{session}#{seq}", "unreadable event json", ""))
            continue
        msg = ev.get("message") or {}
        if session in excluded:
            continue
        if ev.get("type") != "message" or msg.get("role") != "assistant" or ev.get("id") in seen:
            continue
        text = text_of(msg)
        where = f"conversation={conv.get(session, '?')} session={session} seq={seq} event={ev.get('id')}"
        blocks = BLOCK.findall(text)
        valid = 0
        for body in blocks:
            fields, why = parse_block(body)
            if fields:
                valid += 1
                out["pairs"].append((where, fields))
            else:
                out["failures"].append((where, f"malformed block: {why}", body))
        receipts = [int(n) for n in RECEIPT.findall(text)]
        if receipts and sum(receipts) != valid:
            out["failures"].append((where, f"receipt says {sum(receipts)} but {valid} valid block(s) found", ""))
        if blocks and not receipts:
            out["failures"].append((where, f"{len(blocks)} block(s) with no receipt line", ""))
        for d in DEFECT.findall(text):
            out["defects"].append((where, d.strip()))
        for line in CARDS.findall(text):
            for name, h in CARD_REF.findall(line):
                cur = registry.get(name, {}).get("current")
                ok = bool(cur) and cur["sha256"].startswith(h)
                out["cards_seen"].append((where, name, h, ok))
        seen.add(ev.get("id"))
    if apply:
        at = now()
        for where, f in out["pairs"]:
            block = "\n".join(f"{k}: {f[k]}" for k in FIELDS)          # verbatim; the collector never rewrites MC's or Robert's words
            append(store / "correction-pairs.md", f"\n---\n{block}\n---\n<!-- collected {at} by judgment_store collect · {where} -->\n")
        for where, why, body in out["failures"]:
            append(store / "capture-failures.md", f"\n- {at} | {where} | {why}\n" + (f"\n  ```\n{body}\n  ```\n" if body else ""),
                   header="# Capture failures — collector log (append-only)\n")
        for where, d in out["defects"]:
            append(store / "defects.md", f"- {at} | {where} | {d}\n", header="# Harness defects from MC receipts — for the parity track (#361)\n\n")
        for where, name, h, ok in out["cards_seen"]:
            append(store / "adoption-receipts.md", f"- {at} | {where} | {name}@{h} | {'matches current adopted version' if ok else 'NOT the current adopted version'}\n",
                   header="# Loading receipts seen in MC replies\n\n")
        state = {"last_rowid": out["watermark"], "event_ids": sorted(seen)[-KEEP_IDS:], "excluded_sessions": sorted(excluded), "db": str(db), "updated_at": at}
        atomic_write(store / ".collector-state.json", (json.dumps(state, indent=1) + "\n").encode())
    return {"applied": apply, "scanned": out["scanned"], "pairs_collected": len(out["pairs"]), "failures": len(out["failures"]),
            "defects": len(out["defects"]), "loading_receipts": len(out["cards_seen"]), "watermark": out["watermark"],
            "detail": {"failures": [w + " | " + y for w, y, _ in out["failures"]]}}


# ── freeze ───────────────────────────────────────────────────────────────────

def freeze(db: Path, store: Path, conversation: str | None, session: str | None, label: str, apply: bool) -> dict:
    con = open_ro(db)
    if conversation:
        sessions = [r[0] for r in con.execute("select session_id from session_conversations where conversation_id = ?", (conversation,))]
    else:
        sessions = [session]
    if not sessions or sessions == [None]:
        raise SystemExit("refused: no session found for that conversation")
    rows = []
    for s in sessions:
        rows += con.execute("select session_id, seq, event_json, created_at from transcript_events where session_id = ? order by seq", (s,)).fetchall()
    con.close()
    if not rows:
        raise SystemExit("refused: no records for that conversation/session")
    lines = "".join(json.dumps({"session_id": s, "seq": q, "created_at": c, "event": json.loads(e)}, ensure_ascii=False, sort_keys=True) + "\n"
                    for s, q, e, c in rows).encode()
    digest = sha(lines)
    sid = f"{dt.date.today().isoformat()}-{(conversation or session)[:24]}-{digest[:8]}"
    manifest = {"id": sid, "label": label, "conversation": conversation, "sessions": sessions, "events": len(rows),
                "events_sha256": digest, "frozen_at": now(), "source": "mc-chat native session records (read-only)"}
    if apply:
        d = store / "scenarios" / sid
        if d.exists():
            raise SystemExit(f"refused: scenario {sid} already frozen")
        atomic_write(d / "events.jsonl", lines)
        atomic_write(d / "manifest.json", (json.dumps(manifest, indent=1) + "\n").encode())
    return {"applied": apply, **manifest}


# ── adopt ────────────────────────────────────────────────────────────────────

def load_registry(store: Path) -> dict:
    p = store / "cards" / "registry.json"
    return json.loads(p.read_text()) if p.exists() else {}


def adopt(store: Path, card: Path, name: str, approved_by: str, approval: str, apply: bool) -> dict:
    if not NAME.match(name):
        raise SystemExit("refused: card name must be lowercase letters, digits and hyphens")
    if approved_by != "robert" or not approval.strip():
        raise SystemExit("refused: adoption is Robert's explicit act — give --approved-by robert and his words in --approval")
    body = card.read_bytes()
    if not body.strip():
        raise SystemExit("refused: empty card")
    registry = load_registry(store)
    entry = registry.get(name, {"versions": []})
    if entry["versions"] and entry["versions"][-1]["sha256"] == sha(body):
        raise SystemExit("refused: identical to the current adopted version")
    n = len(entry["versions"]) + 1
    version = {"n": n, "sha256": sha(body), "file": f"cards/{name}/{n}.md", "adopted_at": now(), "approved_by": approved_by,
               "approval": approval.strip(), "loading_confirmed": False}
    entry["versions"].append(version)
    entry["current"] = version
    registry[name] = entry
    block = f"## Judgment card: {name}@{version['sha256'][:12]}\n\n{body.decode('utf-8').strip()}\n"
    if apply:
        atomic_write(store / version["file"], body)
        atomic_write(store / "cards" / "registry.json", (json.dumps(registry, indent=1, ensure_ascii=False) + "\n").encode())
    return {"applied": apply, "card": name, "version": n, "sha256": version["sha256"], "receipt_expected": f"Cards loaded: {name}@{version['sha256'][:12]}",
            "next": "add the block below to the next mc-chat persona release and project it (scripts/mc_persona/project_agents.py); "
                    "adoption completes when an MC reply shows the receipt",
            "persona_block": block}


def status(store: Path) -> dict:
    state = load_state(store)
    text = (store / "correction-pairs.md").read_text() if (store / "correction-pairs.md").exists() else ""
    pairs = text.split("\n## Pairs", 1)[-1].count("\nowner-said:") if "\n## Pairs" in text else text.count("\nowner-said:")
    fails = (store / "capture-failures.md").read_text().count("\n- ") if (store / "capture-failures.md").exists() else 0
    receipts = (store / "adoption-receipts.md").read_text() if (store / "adoption-receipts.md").exists() else ""
    cards = {}
    for name, e in load_registry(store).items():
        h = e["current"]["sha256"]
        cards[name] = {"version": e["current"]["n"], "sha256": h[:12],
                       "loading_confirmed": f"{name}@{h[:12]} | matches" in receipts}
    scen = sorted(p.name for p in (store / "scenarios").iterdir()) if (store / "scenarios").exists() else []
    return {"pairs": pairs, "capture_failures": fails, "watermark": state.get("last_rowid"), "last_collected": state.get("updated_at"),
            "cards": cards, "scenarios": scen}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", type=Path, default=DEFAULT_STORE)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--apply", action="store_true")
    c.add_argument("--exclude-session", action="append", default=[], help="a test session id never to collect (recorded in state)")
    f = sub.add_parser("freeze")
    g = f.add_mutually_exclusive_group(required=True)
    g.add_argument("--conversation")
    g.add_argument("--session")
    f.add_argument("--label", default="")
    f.add_argument("--apply", action="store_true")
    a = sub.add_parser("adopt")
    a.add_argument("--card", type=Path, required=True)
    a.add_argument("--name", required=True)
    a.add_argument("--approved-by", required=True)
    a.add_argument("--approval", required=True)
    a.add_argument("--apply", action="store_true")
    sub.add_parser("status")
    x = ap.parse_args(argv)
    with locked(x.store):
        if x.cmd == "collect":
            r = collect(x.db, x.store, x.apply, x.exclude_session)
        elif x.cmd == "freeze":
            r = freeze(x.db, x.store, x.conversation, x.session, x.label, x.apply)
        elif x.cmd == "adopt":
            r = adopt(x.store, x.card, x.name, x.approved_by, x.approval, x.apply)
        else:
            r = status(x.store)
    print(json.dumps(r, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

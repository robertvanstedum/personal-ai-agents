"""One-off, staging only: mark the automatic UAT's EARLIER chats, so they leave Vault search too.

Since the marker exists, a chat created under the staging UAT session is marked ``"uat": true`` when it is created
(MC: ``minimoi_portal/guild_ui/conversations.py``; CoS: ``domains/cos/history.py``), and a marked chat is never taken
into the Vault (``scripts/vault/export_mc_conversations.py``, ``core/memory_shelf/cos.py``). Chats from UAT runs before
that carry no marker. This command finds them by what those runs left on the record and writes the same marker:

  MC    ``created_key`` is ``uat`` + 24 hex (the UAT script's idempotency key, ``tests/uat/staging_uat.py`` ``_key``)
  CoS   the title starts ``uat-<run id> `` (``cos_ask(..., f"uat-{run_id} C1")``)

plus any ``--id`` given by hand. Both identifiers are the client's own words, which is why they are used ONLY here, by
an operator who reads the dry run first, and never as the standing rule. Nothing is deleted: the chats stay in their
stores and on the staging shelf; the staging feed rebuilds its index from the admitted set on every cycle
(``scripts/memory/dev_feed.py``), so a marked chat is out of retrieval after the next cycle (five minutes).

Run it inside the container that owns each store, as that container's user (so the file keeps its owner), dry run first:

    docker exec minimoi-portal python -m core.uat_chats mc --guild-dir /app/runtime/guild --principal robert
    docker exec minimoi-portal python -m core.uat_chats mc --guild-dir /app/runtime/guild --principal robert --apply
    docker exec <cos-scheduler container> python -m core.uat_chats cos
    docker exec <cos-scheduler container> python -m core.uat_chats cos --apply

It refuses to run unless ``MINIMOI_ENV=staging`` and ``MINIMOI_ROLE=standby`` (the UAT door's own rule), takes each
store's own lock for the write, and prints ids and counts only: never a title or a turn. Exit 0 ok, 2 refused or usage.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
from pathlib import Path

FIELD = "uat"
MC_KEY = re.compile(r"uat[0-9a-f]{24}\Z")
MC_ID = re.compile(r"c-[0-9a-f]{12}\Z")
COS_TITLE = re.compile(r"uat-\S+ \S")
COS_ID = re.compile(r"cosc-[0-9a-f]{12,32}\Z")


def staging(environ) -> bool:
    return (environ.get("MINIMOI_ENV") or "").strip() == "staging" and (environ.get("MINIMOI_ROLE") or "").strip() == "standby"


def mark_mc(guild_dir, principal: str, *, ids=(), apply: bool = False) -> dict:
    """Mark ``principal``'s MC chats the UAT created (by ``created_key``) and those named in ``ids``."""
    from minimoi_portal.guild_ui.conversations import ConversationStore
    folder = Path(guild_dir) / "conversations"
    if not folder.is_dir():
        raise ValueError("conversations_unavailable")
    store = ConversationStore(str(folder.parent), base_floor="shop")
    if Path(store.dir) != folder:
        raise ValueError("conversations_unavailable")
    wanted = set(ids)
    out = {"store": "mc", "applied": apply, "marked": [], "already": 0, "not_found": []}
    with store._lock():
        for conv in store._all():
            cid = conv.get("id")
            if conv.get("principal") != principal or conv.get("legacy") or not MC_ID.match(str(cid)):
                continue
            if not (cid in wanted or MC_KEY.match(str(conv.get("created_key") or ""))):
                continue
            wanted.discard(cid)
            if conv.get(FIELD) is True:
                out["already"] += 1
                continue
            if apply:
                store._write(dict(conv, **{FIELD: True}))
            out["marked"].append(cid)
    out["not_found"] = sorted(wanted)
    return out


def mark_cos(conversations_dir, *, ids=(), apply: bool = False) -> dict:
    """Mark the CoS conversations the UAT created (by title) and those named in ``ids``. Never the standing conversation,
    a read-only or a private one. Holds the history writer's own lock; runs none of its recovery."""
    from core import durable_files as fsio
    from domains.cos.store import contracts
    root = Path(conversations_dir) / "history"
    folder = root / "conversations"
    if not folder.is_dir() or folder.is_symlink():
        raise ValueError("history_unavailable")
    wanted = set(ids)
    out = {"store": "cos", "applied": apply, "marked": [], "already": 0, "not_found": []}
    fd = os.open(root / ".lock", os.O_RDWR | os.O_CREAT, fsio.FILE_MODE)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        for path in sorted(folder.glob("cosc-*.json")):
            cid = path.stem
            doc = fsio.read_doc(path)
            if not COS_ID.match(cid) or not isinstance(doc, dict) or doc.get("conversation_id") != cid:
                continue
            if not (cid in wanted or COS_TITLE.match(str(doc.get("title") or ""))):
                continue
            wanted.discard(cid)
            if doc.get("read_only") or doc.get("private") is not False:
                continue
            if doc.get(FIELD) is True:
                out["already"] += 1
                continue
            marked = dict(doc, **{FIELD: True})
            if contracts.errors("conversation", "conversation", marked):
                raise ValueError("payload_invalid")
            if apply:
                fsio.write_doc(path, marked)
            out["marked"].append(cid)
    finally:
        os.close(fd)
    out["not_found"] = sorted(wanted)
    return out


def main(argv=None, environ=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="store", required=True)
    mc = sub.add_parser("mc")
    mc.add_argument("--guild-dir", required=True)
    mc.add_argument("--principal", required=True)
    cos = sub.add_parser("cos")
    cos.add_argument("--conversations-dir", help="default: COS_CONVERSATIONS_DIR")
    for p in (mc, cos):
        p.add_argument("--id", action="append", default=[], help="also mark this conversation id (repeatable)")
        p.add_argument("--apply", action="store_true", help="write the marker (default: dry run)")
    args = parser.parse_args(argv)
    env = os.environ if environ is None else environ
    if not staging(env):
        print(json.dumps({"state": "refused", "code": "staging_only"}))
        return 2
    try:
        if args.store == "mc":
            out = mark_mc(args.guild_dir, args.principal, ids=args.id, apply=args.apply)
        else:
            folder = args.conversations_dir or env.get("COS_CONVERSATIONS_DIR") or ""
            if not os.path.isabs(folder):
                raise ValueError("history_unavailable")
            out = mark_cos(folder, ids=args.id, apply=args.apply)
    except ValueError as exc:
        print(json.dumps({"state": "refused", "code": str(exc)}))
        return 2
    print(json.dumps({"state": "ok", **out, "count": len(out["marked"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

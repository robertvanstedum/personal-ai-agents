"""The meeting a conversation opens, made useful and honest (#298, part 2.3).

* ``opening_text``: what the room is seeded with, so the agents have something to answer: the topic, what the owner asked, MC's last answer, and the GitHub items the
  owner pointed at (titles and links). Posted once per request, under a stable operation key.
* ``status_words``: one plain line per invited agent: invited, accepted, here, away (with the reason), not approved. Never "joined": only a reply in the room says that.
* ``sync``: called from the page's regular poll. Reads the room and files ONE platform note per milestone in the SOURCE conversation (each agent replied, an agent that
  could not or did not, and one digest of excerpts with the room linked), then stops. Excerpts are the agents' own words, cut and attributed: no model is called and nothing
  is summarised for them. Everything is idempotent (a stable note id per milestone), so a reload or a second tab adds nothing.
Private conversations never create or reach any of this.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

NAMES = {"mc": "Master Craftsman", "claude-code": "Claude Code", "codex": "Codex"}
EXCERPT = 280
OWNER_CHARS = 600
MC_CHARS = 1200
WATCH_S = 15 * 60                 # after this long the digest is filed with whatever has arrived
AWAY_AFTER_S = 180                # an agent with no reply this long after the invitation, and not reachable, is named as away
REPLY_KINDS = ("message",)


def _db_path(access_dir: str) -> Path:
    return Path(access_dir) / "meetings.db"


@contextmanager
def database(access_dir: str):
    path = _db_path(access_dir)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        with db:
            db.execute("CREATE TABLE IF NOT EXISTS meeting_rooms(account TEXT, conversation TEXT, note_id TEXT, room TEXT, title TEXT, invitees TEXT, created_at REAL, "
                       "seeded INTEGER DEFAULT 0, seed_seq INTEGER, done INTEGER DEFAULT 0, PRIMARY KEY(account, conversation, note_id))")
            yield db
    finally:
        db.close()
    path.chmod(0o600)


def _clip(text: str, n: int) -> str:
    one = re.sub(r"[ \t]+", " ", str(text or "")).strip()
    return one if len(one) <= n else one[: n - 1].rstrip() + "…"


def opening_text(*, title: str, owner_text: str, mc_last: str, items: list[tuple[int, str, str]], invitees: list[str]) -> str:
    """The seed message. ``items`` are (number, title, url) for the GitHub items the owner pointed at."""
    names = ", ".join(NAMES.get(n, n) for n in invitees)
    lines = [f"Opening context, posted by the platform at Robert's request from a Master Craftsman conversation (not written by Robert).",
             f"Topic: {_clip(title, 120)}",
             f"Robert asked: {_clip(owner_text, OWNER_CHARS)}"]
    if mc_last:
        lines.append(f"Master Craftsman's last answer in that conversation: {_clip(mc_last, MC_CHARS)}")
    if items:
        lines.append("Items: " + "; ".join(f"#{n} {_clip(t, 90)} ({u})" for n, t, u in items))
    lines.append(f"Invited: {names}. Please each reply in this room with your view on the topic. Replies in this room do not start work; if work is approved, Robert hands it to the agents himself.")
    return "\n".join(lines)


def status_words(invitees: list[str], participants: list[dict], not_approved: list[str]) -> str:
    """One line for the chat: who was invited and what each is doing now, from the room's own record. Never says an agent has joined."""
    by = {p.get("id"): p for p in participants or []}
    parts = []
    for name in invitees:
        p = by.get(name) or {}
        rsvp = str(p.get("rsvp") or "invited").replace("_", " ")
        reach = p.get("reach") or {}
        where = reach.get("state") or "unknown"
        why = f" ({_clip(reach.get('reason'), 60)})" if reach.get("reason") else ""
        parts.append(f"{NAMES.get(name, name)}: {rsvp}, {where}{why}")
    for name in not_approved:
        parts.append(f"{NAMES.get(name, name)}: not approved, so not invited")
    return "; ".join(parts)


# ── the follow-up ──
def _events(room_doc: dict) -> list[dict]:
    ev = room_doc.get("events") or room_doc.get("room", {}).get("events") or []
    return [e for e in ev if isinstance(e, dict)]


def sync(conv: dict, account: str, access_dir: str, call, add_note, *, clock=time.time) -> tuple[list[dict], int]:
    """(notes filed or already filed for this conversation's open meetings, how many meetings are still being followed). ``call(method, path)`` reads Records as the owner;
    ``add_note(note_id, text)`` files a platform note and returns the stored note or None."""
    notes, pending = [], 0
    now = clock()
    try:
        with database(access_dir) as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM meeting_rooms WHERE account=? AND conversation=? AND done=0", (account, conv.get("id")))]
    except Exception:
        return notes, 0
    for row in rows:
        invitees = json.loads(row["invitees"] or "[]")
        if not invitees or not row["seeded"]:
            if now - row["created_at"] > WATCH_S:
                _finish(access_dir, row)
            else:
                pending += 1
            continue
        try:
            doc = call("GET", f"/rooms/{row['room']}")
            status = call("GET", f"/rooms/{row['room']}/turns")
        except Exception:
            pending += 1
            continue
        events = _events(doc)
        link = f"/guild-next/guild/rooms?room={row['room']}"
        seed_seq = row.get("seed_seq") or 0
        replies, trouble = {}, {}
        for e in events:
            if e.get("kind") in REPLY_KINDS and e.get("actor") in invitees and int(e.get("seq") or 0) > seed_seq and e["actor"] not in replies:
                replies[e["actor"]] = e
        parts = {p.get("id"): p for p in (status.get("participants") or [])}
        turns = status.get("turns") or []
        for name in invitees:
            if name in replies:
                continue
            p = parts.get(name) or {}
            failed = [t for t in turns if t.get("addressee") == name and t.get("state") in ("failed", "abandoned", "expired", "cancelled") and t.get("disposition")]
            if p.get("rsvp") in ("declined", "no_response"):
                trouble[name] = f"has not accepted ({str(p.get('rsvp')).replace('_', ' ')})"
            elif failed:
                trouble[name] = "could not answer: " + str(failed[0].get("disposition")).replace("_", " ")
            elif now - row["created_at"] > AWAY_AFTER_S and (p.get("reach") or {}).get("state") == "away":
                trouble[name] = "is away: " + (_clip((p.get("reach") or {}).get("reason"), 80) or "not reachable")
        for name, e in replies.items():
            nid = f"mtg-{row['room'][:8]}-{name}-replied"
            note = add_note(nid, f"**{NAMES.get(name, name)} replied in the meeting:** {_clip(e.get('body'), EXCERPT)} [Open the meeting]({link}).")
            if note:
                notes.append(note)
        for name, why in trouble.items():
            note = add_note(f"mtg-{row['room'][:8]}-{name}-trouble", f"**{NAMES.get(name, name)} {why}.** The meeting is still open; nothing was retried.")
            if note:
                notes.append(note)
        settled = len(replies) + len(trouble) >= len(invitees)
        if settled or now - row["created_at"] > WATCH_S:
            lines = [f"- {NAMES.get(n, n)}: " + (_clip(replies[n].get("body"), EXCERPT) if n in replies else (trouble.get(n) or "no reply yet")) for n in invitees]
            head = "Meeting digest (excerpts of what each said, in their own words; nothing here is a summary or a decision)" if settled else "Meeting so far (time is up for now)"
            note = add_note(f"mtg-{row['room'][:8]}-digest", f"**{head}:**\n" + "\n".join(lines) + f"\n[Open the meeting]({link}).")
            if note:
                notes.append(note)
                _finish(access_dir, row)
                continue
        pending += 1
    return notes, pending


def _finish(access_dir: str, row: dict) -> None:
    try:
        with database(access_dir) as db:
            db.execute("UPDATE meeting_rooms SET done=1 WHERE account=? AND conversation=? AND note_id=?", (row["account"], row["conversation"], row["note_id"]))
    except Exception:
        pass

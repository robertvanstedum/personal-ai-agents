"""Rooms export bundles on the shared memory path (amendment §10b; Codex handoff 2026-10-04).

A **Rooms bundle** is what Records' own exporter publishes (``transcript_publish``): a private directory
``<session>-r<revision>-<sha256>`` holding exactly ``manifest.json``, ``transcript.json`` and ``transcript.md``. This
module reads those bundles, **read-only**, and hands them to the same ``Bundle -> scrub -> Shelf.ingest -> ledger`` path
every other source uses. It never opens Records' SQLite store, never runs the exporter, never follows a reference,
copies an attachment or executes anything the transcript says. Whatever fails here fails here: a Rooms message is
never held up by it, because nothing in this module is on the path that accepts one.

**What a bundle must be (each failure is one fixed code, nothing else):**
the directory name matches the format; the three files are regular, not links, and nothing else is there; the manifest
is the typed shape with a supported schema (1.0, or 1.1 which adds typed execution evidence to agent turns; a **newer
minor of major 1 is refused with its own code, ``unsupported_minor``**, and any other family or major with
``unsupported_schema``: a newer minor may add fields that matter to identity, timestamps or attribution, so it is taught
deliberately, with fixtures, and never half-read); every file's length and sha256 match the manifest; the directory name
equals the identity recomputed from the files; the manifest, the transcript and the name agree on session and
revision; the transcript has the typed shape; the bundle comes from the approved source instance.

**D2 (settled), before anything is retained.** A meeting with any human other than the owner is excluded **whole**
(``other_participant``). A participant whose identity cannot be established (kind unknown, not on the roster, not a valid
actor id, an owner id that is not a human) fails closed with its own code (``unknown_participant``). Identity comes only
from the roster the exporter took from the store's principals and from the owner ids in the approved configuration,
never from a display name or from text. Exclusion happens on the participant list, before the transcript is turned
into anything that could be kept. There is no Private mode in Rooms and none is added.

**Identity and revisions.** One record per ``source_instance_id`` + ``session_id``. Bundles are read oldest revision
first, so every revision becomes an edition and earlier editions stay. A snapshot older than the record's current
revision is skipped (``older_revision``) and never becomes current; the same revision with different bytes is refused
(``revision_conflict``). An unchanged bundle read again changes nothing.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.memory_shelf import codes, fsio, ledger, render, review
from core.memory_shelf import bundle as bundles
from core.memory_shelf.config import SourceCfg, never_copied
from core.memory_shelf.sessions import ASSISTANT, HUMAN, Parsed

SCHEMA_FAMILY = "minimoi.transcript"
SUPPORTED_SCHEMAS = ("minimoi.transcript/1.0", "minimoi.transcript/1.1")   # 1.1: Rooms R1 execution evidence on agent turns
FILES = ("manifest.json", "transcript.json", "transcript.md")
PAYLOAD = ("transcript.json", "transcript.md")
MAX_FILE = 64 * 1024 * 1024
MAX_MANIFEST = 1 << 20
NORMALIZER = 1

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_NAME = re.compile(rf"^({_UUID})-r(\d+)-([0-9a-f]{{64}})$")
_ID = re.compile(rf"^{_UUID}$")
_SCHEMA = re.compile(rf"^{re.escape(SCHEMA_FAMILY)}/(\d+)\.(\d+)$")
_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_ACTOR = re.compile(r"^[a-z][a-z0-9_-]{0,59}$(?![\s\S])")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
KINDS = frozenset({"message", "lifecycle", "correction", "proposal", "recorded_decision", "assignment", "checkpoint",
                   "task_update", "membership", "moderator", "document_filed", "note_filed", "artifact_linked",
                   "coordination", "executive_snapshot"})
PARTICIPANT_KINDS = frozenset({"human", "agent", "unknown"})
STATES = frozenset({"active", "paused", "closed"})


class Refusal(Exception):
    """The bundle is refused for one fixed reason. The message is the code; it never carries content."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Excluded(Refusal):
    """D2: the meeting is excluded (``other_participant`` or ``unknown_participant``). Nothing about it is kept."""


@dataclass(frozen=True)
class Candidate:
    rel: str                       # the bundle directory's name
    path: Path
    session: str
    revision: int
    size: int
    mtime_ns: int


@dataclass
class Read:
    parsed: Parsed
    sha256: str                    # the bundle's content identity (the hash in its name)
    size: int
    instance: str
    session: str
    revision: int


# ── discovery ────────────────────────────────────────────────────────────────

def session_key(source: str, session: str) -> str:
    """The ledger key: one per session (the identity is instance + session, so the instance is bound by the source)."""
    return bundles.ledger_key_for(source, f"session:{session}")


def discover(cfg: SourceCfg) -> tuple[list[Candidate], dict]:
    """Bundle directories under the source root, oldest revision of each session first, and counts of what was passed
    over. Links, scratch (``.pending-*``), quarantine and unknown names are counted and never opened."""
    found, passed = [], {}
    root = Path(cfg.root)
    if not root.is_dir():
        return found, passed
    for entry in sorted(os.scandir(root), key=lambda e: e.name):
        name = entry.name
        if name.startswith(".") or name == "scratch-quarantine":
            continue                                          # the exporter's own scratch and quarantine
        try:
            st = os.lstat(entry.path)
        except OSError:
            continue
        match = _NAME.match(name)
        if stat.S_ISLNK(st.st_mode):
            passed["symlink"] = passed.get("symlink", 0) + 1
        elif not stat.S_ISDIR(st.st_mode) or not match:
            if stat.S_ISDIR(st.st_mode):
                passed["unrecognized"] = passed.get("unrecognized", 0) + 1       # files at the top (worker-status.json) are not bundles
        elif never_copied(name, cfg.never_copy):
            passed[codes.NEVER_COPY] = passed.get(codes.NEVER_COPY, 0) + 1
        else:
            size = 0
            for fname in FILES:
                try:
                    size += os.lstat(Path(entry.path) / fname).st_size
                except OSError:
                    pass
            found.append(Candidate(name, Path(entry.path), match.group(1), int(match.group(2)), size, st.st_mtime_ns))
    return sorted(found, key=lambda c: (c.session, c.revision, c.rel)), passed


# ── reading one bundle ───────────────────────────────────────────────────────

def _regular(path: Path, limit: int) -> int:
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
        raise Refusal(codes.TOO_LARGE if stat.S_ISREG(st.st_mode) else codes.BAD_BUNDLE_FILES)
    return st.st_size


def _read_bytes(path: Path, limit: int) -> bytes:
    _regular(path, limit)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise Refusal(codes.TOO_LARGE)
    return data


def _load(raw: bytes, code: str) -> dict:
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise Refusal(code) from None
    if not isinstance(doc, dict):
        raise Refusal(code)
    return doc


def _is(value, kind) -> bool:
    return isinstance(value, kind) and not isinstance(value, bool)


def schema_gate(version) -> str:
    """The supported schema string, or ``Refusal``: another family or major is ``unsupported_schema``; a 1.x minor this
    reader has not been taught is ``unsupported_minor`` (fail closed, distinct, so it is visible and can be taught)."""
    match = _SCHEMA.match(version) if isinstance(version, str) else None
    if not match or int(match.group(1)) != 1:
        raise Refusal(codes.UNSUPPORTED_SCHEMA)
    if version not in SUPPORTED_SCHEMAS:
        raise Refusal(codes.UNSUPPORTED_MINOR)
    return version


def _manifest(doc: dict) -> dict:
    need = {"bundle_id": str, "schema_version": str, "renderer_version": str, "source_instance_id": str,
            "session_id": str, "source_revision": int, "through_seq": int, "generated_at": str, "snapshot_at": str,
            "session_state": str, "publication_status": str, "scope": str, "files": dict}
    for key, kind in need.items():
        if not _is(doc.get(key), kind):
            raise Refusal(codes.BAD_MANIFEST)
    schema_gate(doc["schema_version"])
    if not (_ID.match(doc["source_instance_id"]) and _ID.match(doc["session_id"]) and doc["session_state"] in STATES
            and doc["source_revision"] >= 0 and doc["through_seq"] >= 0 and set(doc["files"]) == set(PAYLOAD)):
        raise Refusal(codes.BAD_MANIFEST)
    for entry in doc["files"].values():
        if not (isinstance(entry, dict) and _is(entry.get("bytes"), int) and isinstance(entry.get("sha256"), str)
                and _SHA.match(entry["sha256"])):
            raise Refusal(codes.BAD_MANIFEST)
    return doc


def verify_bundle(path: Path) -> tuple[dict, dict[str, bytes]]:
    """The manifest and the payload bytes, after every structural and integrity check; raises ``Refusal``."""
    path = Path(path)
    try:
        if not stat.S_ISDIR(os.lstat(path).st_mode):
            raise Refusal(codes.BAD_BUNDLE_FILES)
        names = sorted(e.name for e in os.scandir(path))
    except OSError:
        raise Refusal(codes.BAD_BUNDLE_FILES) from None
    if names != sorted(FILES):
        raise Refusal(codes.BAD_BUNDLE_FILES)
    try:
        manifest = _manifest(_load(_read_bytes(path / "manifest.json", MAX_MANIFEST), codes.BAD_MANIFEST))
        payload = {name: _read_bytes(path / name, MAX_FILE) for name in PAYLOAD}
    except OSError:
        raise Refusal(codes.BAD_BUNDLE_FILES) from None
    for name, content in payload.items():
        want = manifest["files"][name]
        if len(content) != want["bytes"] or hashlib.sha256(content).hexdigest() != want["sha256"]:
            raise Refusal(codes.HASH_MISMATCH)
    material = b"".join(name.encode() + b"\0" + payload[name] for name in sorted(payload))
    identity = f"{manifest['session_id']}-r{manifest['source_revision']}-{hashlib.sha256(material).hexdigest()}"
    if manifest["bundle_id"] != identity or path.name != identity:
        raise Refusal(codes.BAD_IDENTITY)
    return manifest, payload


def _text(value) -> bool:
    return isinstance(value, str)


def _nullable_time(value) -> bool:
    return value is None or (isinstance(value, str) and bool(_TIME.match(value)))


LEGACY_EXECUTION = {"coordination_request_id": lambda v: isinstance(v, str) and bool(_ID.match(v)),
                    "openclaw_run_id": lambda v: isinstance(v, str)}
ROOMS_REQUIRED = {"turn_id": lambda v: isinstance(v, str) and bool(_ID.match(v)),
                  "claim_id": lambda v: isinstance(v, str) and bool(_ID.match(v)),
                  "attempt": lambda v: _is(v, int) and v >= 1,
                  "coordinating_installation": lambda v: isinstance(v, str),
                  "caller_correlation": lambda v: isinstance(v, str) and bool(_HEX32.match(v))}
ROOMS_OPTIONAL = {"upstream_execution_id": lambda v: v is None or isinstance(v, str),
                  "usage_evidence_status": lambda v: v in ("reported", "none"),
                  "prompt_tokens": lambda v: _is(v, int) and v >= 0, "completion_tokens": lambda v: _is(v, int) and v >= 0}


def execution_ok(execution, schema: str) -> bool:
    """A record's ``execution`` under the declared schema, exactly the exporter's two strict shapes: the legacy one
    (1.0 and 1.1) or, in 1.1 only, the typed Rooms evidence (every required field, no unknown field)."""
    if not isinstance(execution, dict):
        return False
    keys = set(execution)
    if keys <= set(LEGACY_EXECUTION) and all(LEGACY_EXECUTION[k](execution[k]) for k in keys):
        return True
    if schema != "minimoi.transcript/1.1":
        return False
    allowed = {**ROOMS_REQUIRED, **ROOMS_OPTIONAL, **LEGACY_EXECUTION}
    return set(ROOMS_REQUIRED) <= keys <= set(allowed) and all(allowed[k](execution[k]) for k in keys)


def _shape(doc: dict, schema: str) -> None:
    """The typed shape of ``transcript.json`` (a local check; no schema library, no remote resolution)."""
    ok = (doc.get("schema_version") == schema and isinstance(doc.get("session"), dict)
          and isinstance(doc.get("coverage"), dict) and isinstance(doc.get("raw_transcript"), list)
          and isinstance(doc.get("participants"), list) and isinstance(doc.get("notes"), list)
          and isinstance(doc.get("references"), list) and _is(doc.get("source_revision"), int)
          and _is(doc.get("through_seq"), int) and isinstance(doc.get("source_instance_id"), str))
    if not ok:
        raise Refusal(codes.BAD_TRANSCRIPT)
    session = doc["session"]
    if not (_ID.match(str(session.get("session_id"))) and session.get("state") in STATES and _text(session.get("title"))
            and _nullable_time(session.get("opened_at")) and _nullable_time(session.get("closed_at"))):
        raise Refusal(codes.BAD_TRANSCRIPT)
    for p in doc["participants"]:
        if not (isinstance(p, dict) and _text(p.get("participant_id")) and _text(p.get("display_name"))
                and p.get("kind") in PARTICIPANT_KINDS):
            raise Refusal(codes.BAD_TRANSCRIPT)
    seen = set()
    for r in doc["raw_transcript"]:
        if not (isinstance(r, dict) and _ID.match(str(r.get("record_id"))) and _is(r.get("seq"), int) and r["seq"] >= 0
                and r.get("kind") in KINDS and _text(r.get("speaker_id")) and _text(r.get("submitted_by"))
                and _text(r.get("speaker_label")) and _text(r.get("text")) and _nullable_time(r.get("source_created_at"))
                and isinstance(r.get("ingested_at"), str) and _TIME.match(r["ingested_at"])
                and r.get("origin_assurance") in ("unknown", "declared", "verified")):
            raise Refusal(codes.BAD_TRANSCRIPT)
        for link in ("reply_to_record_id", "corrects_record_id"):
            if r.get(link) is not None and not _ID.match(str(r[link])):
                raise Refusal(codes.BAD_TRANSCRIPT)
        for key in ("agent_id", "model", "source_application", "material_class"):
            if r.get(key) is not None and not isinstance(r[key], str):
                raise Refusal(codes.BAD_TRANSCRIPT)
        through = r.get("context_through_seq")
        if through is not None and not (_is(through, int) and 0 <= through < r["seq"]):
            raise Refusal(codes.BAD_TRANSCRIPT)
        if "execution" in r and not execution_ok(r["execution"], schema):
            raise Refusal(codes.BAD_TRANSCRIPT)
        if r["origin_assurance"] == "verified" and not all(isinstance(r.get(k), str) and r[k].strip()
                                                           for k in ("evidence_reference", "verification_method")):
            raise Refusal(codes.BAD_TRANSCRIPT)
        if r["record_id"] in seen:
            raise Refusal(codes.BAD_TRANSCRIPT)
        seen.add(r["record_id"])
    for n in doc["notes"]:
        if not (isinstance(n, dict) and _ID.match(str(n.get("note_id"))) and _is(n.get("version"), int) and _text(n.get("kind"))
                and _text(n.get("author_id")) and _text(n.get("text")) and _is(n.get("source_through_seq"), int)
                and isinstance(n.get("created_at"), str) and isinstance(n.get("source_record_ids"), list)):
            raise Refusal(codes.BAD_TRANSCRIPT)
    for ref in doc["references"]:
        if not (isinstance(ref, dict) and _ID.match(str(ref.get("reference_id"))) and _text(ref.get("kind"))
                and _text(ref.get("target")) and _text(ref.get("label"))):
            raise Refusal(codes.BAD_TRANSCRIPT)


def judge_participants(doc: dict, owner_ids: tuple[str, ...]) -> dict[str, str]:
    """D2. ``{participant id: "owner" | "agent"}`` when the meeting may be kept; raises ``Excluded`` otherwise.

    Every actor the transcript names (speaker, submitter, note author) must be on the roster; the roster's kind comes
    from the store's principals. Anyone not established as the owner or an agent excludes the meeting."""
    roster = {p["participant_id"]: p for p in doc["participants"]}
    named = {r[k] for r in doc["raw_transcript"] for k in ("speaker_id", "submitted_by")} | {n["author_id"] for n in doc["notes"]}
    owners = set(owner_ids)
    unknown = False
    for pid in sorted(named | set(roster)):
        person = roster.get(pid)
        if person is None or person["kind"] == "unknown" or not _ACTOR.match(pid):
            unknown = True
        elif person["kind"] == "human" and pid not in owners:
            raise Excluded(codes.OTHER_PARTICIPANT)
        elif pid in owners and person["kind"] != "human":
            unknown = True                                     # an owner id whose principal is not a human: not established
    if unknown:
        raise Excluded(codes.UNKNOWN_PARTICIPANT)
    return {pid: ("owner" if pid in owners else "agent") for pid in roster}


def _links_ok(doc: dict) -> None:
    ids = {r["record_id"]: r["seq"] for r in doc["raw_transcript"]}
    for r in doc["raw_transcript"]:
        for link in ("reply_to_record_id", "corrects_record_id"):
            target = r.get(link)
            if target is not None and (target not in ids or ids[target] >= r["seq"]):
                raise Refusal(codes.BAD_TRANSCRIPT)


def read_bundle(path: Path, cfg: SourceCfg) -> Read:
    """Verify, check identity, apply D2, and turn one bundle into a ``Parsed``. Raises ``Refusal`` / ``Excluded``."""
    manifest, payload = verify_bundle(path)
    doc = _load(payload["transcript.json"], codes.BAD_TRANSCRIPT)
    if doc.get("schema_version") != manifest["schema_version"]:
        raise Refusal(codes.BAD_IDENTITY)                     # the manifest and the transcript must declare the same schema
    _shape(doc, manifest["schema_version"])
    if not (doc["source_instance_id"] == manifest["source_instance_id"] and doc["session"]["session_id"] == manifest["session_id"]
            and doc["source_revision"] == manifest["source_revision"] and doc["through_seq"] == manifest["through_seq"]
            and doc["session"]["state"] == manifest["session_state"]):
        raise Refusal(codes.BAD_IDENTITY)
    if not cfg.source_instance_id or manifest["source_instance_id"] != cfg.source_instance_id:
        raise Refusal(codes.WRONG_SOURCE)                      # no configured instance means no bundle is accepted
    roles = judge_participants(doc, cfg.owner_ids)             # D2: before anything is built from the transcript
    _links_ok(doc)
    sha = _NAME.match(Path(path).name).group(3)
    return Read(_parse(doc, manifest, roles), sha, sum(len(b) for b in payload.values()), manifest["source_instance_id"],
                manifest["session_id"], manifest["source_revision"])


def _parse(doc: dict, manifest: dict, roles: dict[str, str]) -> Parsed:
    kinds = {p["participant_id"]: p["kind"] for p in doc["participants"]}
    session = doc["session"]
    records = sorted(doc["raw_transcript"], key=lambda r: (r["seq"], r["record_id"]))
    parsed = Parsed("rooms", "Rooms", source_id=f"{manifest['source_instance_id']}:{session['session_id']}",
                    started=session.get("opened_at") or (records[0]["ingested_at"] if records else None),
                    identity="source", designatable=False, normalizer=NORMALIZER)
    parsed.lines = len(records)
    for number, r in enumerate(records, 1):
        text = r["text"]
        if not text.strip():
            parsed.skip(f"empty:{r['kind']}", number)
            continue
        attrs = {"who": r["speaker_id"], "seq": r["seq"], "kind": r["kind"], "rid": r["record_id"],
                 "ing": r["ingested_at"], "label": r["speaker_label"]}
        if isinstance(r.get("execution"), dict) and r["execution"].get("turn_id"):
            attrs["turn"] = r["execution"]["turn_id"]           # links the agent's reply to its Rooms turn, in the readable view
        if r.get("source_created_at"):
            attrs["ts"] = r["source_created_at"]
        for key, src in (("reply", "reply_to_record_id"), ("corrects", "corrects_record_id")):
            if r.get(src):
                attrs[key] = r[src]
        if r["submitted_by"] != r["speaker_id"]:
            attrs["submitted_by"] = r["submitted_by"]
        for key in ("agent_id", "model", "source_application", "material_class", "context_through_seq", "imported_source",
                    "execution", "evidence_reference", "verification_method"):
            if r.get(key) not in (None, "", {}):
                attrs[key] = r[key]
        attrs["origin"] = r["origin_assurance"]
        speaker = HUMAN if kinds[r["speaker_id"]] == "human" else ASSISTANT
        parsed.add(speaker, text, number, f"room:{r['kind']}", attrs)
    parsed.participants = [{"id": p["participant_id"], "kind": p["kind"], "role": roles[p["participant_id"]],
                            "label": p["display_name"]} for p in sorted(doc["participants"], key=lambda p: p["participant_id"])]
    parsed.notes = [{"note_id": n["note_id"], "author": n["author_id"], "kind": n["kind"], "version": n["version"],
                     "created_at": n["created_at"], "text": n["text"], "source_through_seq": n["source_through_seq"],
                     "source_record_ids": sorted(n["source_record_ids"]), "supersedes": n.get("supersedes")}
                    for n in sorted(doc["notes"], key=lambda n: n["note_id"])]
    parsed.references = [{k: v for k, v in sorted(ref.items())} for ref in sorted(doc["references"], key=lambda r: r["reference_id"])]
    parsed.manifest = {"source_instance_id": manifest["source_instance_id"], "session_id": session["session_id"],
                       "source_revision": manifest["source_revision"], "through_seq": manifest["through_seq"],
                       "session_state": session["state"], "publication_status": manifest["publication_status"],
                       "participants": len(doc["participants"]), "notes": len(doc["notes"]),
                       "references": len(doc["references"]), "declared_coverage": doc["coverage"],
                       "transcript_schema": doc["schema_version"],
                       "execution_records": sum(1 for r in records if isinstance(r.get("execution"), dict)
                                                and set(ROOMS_REQUIRED) <= set(r["execution"]))}
    omissions = [o for o in (doc["coverage"].get("omissions") or []) if isinstance(o, str)]
    empties = sum(1 for r in records if not r["text"].strip())
    gap = max(0, len(records) - len(parsed.turns) - empties)
    flags = ([coverage_flag()] if (gap or omissions) else [])
    parsed.coverage = {"seen": {"records": len(records)}, "taken": {"records": len(parsed.turns) + empties},
                       "gap": {"records": gap}, "flags": flags, "declared_omissions": len(omissions)}
    return parsed


def coverage_flag() -> str:
    from core.memory_shelf import coverage
    return coverage.POSSIBLE_GAP


# ── the listing and the pass ─────────────────────────────────────────────────

def _date(mtime_ns: int) -> str:
    return datetime.fromtimestamp(mtime_ns / 1e9, tz=timezone.utc).strftime("%Y-%m-%d")


def decide(cand: Candidate, cfg: SourceCfg) -> tuple[str, str | None]:
    """``("read", None)``, ``("excluded", code)`` or ``("refused", code)`` for one bundle. Never raises."""
    try:
        read_bundle(cand.path, cfg)
        return "read", None
    except Excluded as exc:
        return "excluded", exc.code
    except Refusal as exc:
        return "refused", exc.code
    except Exception:                                          # noqa: BLE001 - a fixed code, never the message
        return "refused", codes.BAD_BUNDLE_FILES


def build_listing(cfg: SourceCfg, now: datetime | None = None) -> dict:
    """The dry-run listing: session ids, revisions, sizes, dates, states and decisions. **No transcript text, title,
    label or participant name.** The bundles are checked the way a real pass checks them (D2 included), so the owner
    sees what would be kept, excluded and refused before approving."""
    cands, passed = discover(cfg)
    rows, counts, total = [], {"would_copy": 0, "excluded": {}, "refused": {}, "passed_over": passed}, 0
    for cand in cands:
        what, why = decide(cand, cfg)
        decision = "would-copy" if what == "read" else f"would-{what}:{why}"
        if what == "read":
            counts["would_copy"] += 1
            total += cand.size
        else:
            counts[what][why] = counts[what].get(why, 0) + 1
        rows.append({"id": cand.session, "revision": cand.revision, "bytes": cand.size, "date": _date(cand.mtime_ns),
                     "decision": decision})
    digest = hashlib.sha256("\n".join(f"{r['id']}\t{r['revision']}\t{r['bytes']}\t{r['decision']}" for r in rows).encode()).hexdigest()
    counts["bytes"] = total
    counts["sessions"] = len({r["id"] for r in rows})
    return {"source": cfg.name, "kind": cfg.kind, "fingerprint": cfg.fingerprint(),
            "generated_at": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source_instance_configured": bool(cfg.source_instance_id), "counts": counts, "sessions": rows,
            "listing_sha256": digest}


def capture(shelf, cfg: SourceCfg, cand: Candidate, skey: str, prev: dict, now: datetime,
            settle_seconds: float, lkey: str) -> tuple[str, str | None, str | None, dict | None, str | None]:
    """One bundle. ``(outcome, record id, sha256, coverage, reason)``; never raises, never touches the source."""
    name = cfg.name
    if settle_seconds and (now.timestamp() - cand.mtime_ns / 1e9) < settle_seconds:
        ledger.record(shelf, name, lkey, codes.UNSTABLE, now=now)
        return codes.UNSTABLE, None, None, None, None
    try:
        read = read_bundle(cand.path, cfg)
    except Excluded as exc:
        _exclusion(shelf, name, lkey, exc.code, cand, now)
        return codes.EXCLUDED, None, None, None, exc.code
    except Refusal as exc:
        ledger.record(shelf, name, lkey, codes.REFUSED, reason=exc.code, now=now)
        return codes.REFUSED, None, None, None, exc.code
    except Exception:                                          # noqa: BLE001 - fixed code only
        ledger.record(shelf, name, lkey, codes.FAILED, reason="parse_failed", now=now)
        return codes.FAILED, None, None, None, "parse_failed"
    parsed = read.parsed
    created = bundles.utc(parsed.started, datetime.fromtimestamp(cand.mtime_ns / 1e9, tz=timezone.utc))
    try:
        bundle = bundles.from_parsed(
            parsed, read.sha256, read.size, key=f"rooms:{parsed.source_id}", title=f"rooms session {created[:10]}",
            origin=f"watcher:{name}", created=created, retained={"kind": "rooms-bundle", "source": name, "rel": cand.rel},
            ledger_key=lkey)
    except Exception:                                          # noqa: BLE001
        ledger.record(shelf, name, lkey, codes.FAILED, reason="parse_failed", now=now)
        return codes.FAILED, None, None, None, "parse_failed"
    path, code = shelf.stage(bundle)
    if code:
        ledger.record(shelf, name, lkey, codes.DISK_LOW, now=now)
        return codes.DISK_LOW, None, read.sha256, None, None
    result = next((r for r in shelf.drain() if r.key == bundle.key), None)
    if result is None and not path.exists():
        result = shelf.ingest(bundle)                  # another process's drain applied our staged bundle: ask again (idempotent)
    if result is None:
        return codes.FAILED, None, read.sha256, None, None
    return result.outcome, result.record_id, read.sha256, parsed.coverage or None, result.reason


def _exclusion(shelf, source: str, lkey: str, code: str, cand: Candidate, now: datetime) -> None:
    """D2 outcome: one ledger row (a code) and one review item (ids and counts), and nothing from the meeting."""
    had = False
    try:
        had = any(r.get("source") == source and r.get("key") == lkey and r["outcome"] in codes.OK_OUTCOMES
                  for r in fsio.read_jsonl(Path(shelf.ledger_dir) / "ledger.jsonl"))
    except Exception:                                          # noqa: BLE001
        pass
    ledger.record(shelf, source, lkey, codes.EXCLUDED, reason=code, now=now)
    review.sync_rooms_exclusion(shelf, lkey, {"source": source, "reason": code, "latest_revision": cand.revision,
                                              "earlier_editions_kept": had, "date": _date(cand.mtime_ns)})


__all__ = ["discover", "read_bundle", "verify_bundle", "judge_participants", "build_listing", "capture", "decide",
           "Refusal", "Excluded", "Candidate", "session_key", "NORMALIZER"]

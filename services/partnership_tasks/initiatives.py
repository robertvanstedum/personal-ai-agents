"""Private professional-initiative store under the task service (#680; Lane G spec v0.2 §2.6, changes 5-7).

A parked home for professional initiatives CoS files from the owner's own turn, each with its packet of files:

    <root>/<partition>/                      one partition per (principal, environment); no caller-chosen path
        counter.json                         {"year": 2026, "next": 2}, advanced under the partition lock
        PI-2026-0001/initiative.json         id, title, summary, state, revision, created, updated, filed_by, source, packet[]
        PI-2026-0001/packet/<sha12>-<name>   the bytes, written once
        PI-2026-0001/history.jsonl           append-only, written last
        staging/<sha256>                     uploaded files waiting to be named by ``file`` or ``attach`` (24 h)
        operations/<op digest>.json          the first result of each write, so a replay returns it

IDs are ``PI-YYYY-NNNN``, assigned by the store, never by a model, and never ``INIT-`` (Planning Studio's series).
``parked`` is the only state this store writes. Filing creates no task, request or proposal.

Every write takes the partition lock, builds the new initiative folder beside its final place and renames it in, so
a crash leaves nothing or a whole record. Authorization lives in ``execute`` and is checked again before each write.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ID = re.compile(r"^PI-(\d{4})-(\d{4})$")
SHA = re.compile(r"^[0-9a-f]{64}$")
MAX_FILE = 5 * 1024 * 1024
MAX_FILES = 20
MAX_TOTAL = 25 * 1024 * 1024
MAX_TITLE = 160
MAX_SUMMARY = 4000
STAGING_TTL_S = 24 * 3600
TEXT_TYPES = (".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".html", ".xml")
MAX_TEXT = 24000
WRITE_STORES = {"cos-history", "cos-meeting-owner"}     # platform-recorded owner turns only (change 5)


class Refused(ValueError):
    """A definite refusal made before anything was written. ``code`` is safe to show."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_name(name: object) -> str:
    if not isinstance(name, str):
        raise Refused("file_name_invalid")
    base = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base)[:120].strip(" .")
    if not base or base in (".", ".."):
        raise Refused("file_name_invalid")
    return base


def _text(value: object, limit: int, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise Refused(code)
    return value.strip()


class Store:
    def __init__(self, root, principal: str, environment: str, *, clock=time.time):
        for value in (principal, environment):
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:@-]{1,120}", value):
                raise ValueError("invalid store binding")
        key = hashlib.sha256(json.dumps([principal, environment]).encode()).hexdigest()[:28]
        self.root = Path(root) / ("pi-" + key)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.root / "staging").mkdir(exist_ok=True, mode=0o700)
        (self.root / "operations").mkdir(exist_ok=True, mode=0o700)
        self._clock = clock

    # ---- locking and small helpers -------------------------------------------------------------------------
    @contextmanager
    def _locked(self):
        with open(self.root / ".lock", "a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    @staticmethod
    def _write_json(path: Path, value) -> None:
        tmp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex[:8])
        tmp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1), "utf-8")
        os.replace(tmp, path)

    def _folder(self, initiative_id: object) -> Path:
        if not isinstance(initiative_id, str) or not ID.match(initiative_id):
            if isinstance(initiative_id, str) and initiative_id.startswith("INIT-"):
                raise Refused("planning_studio_id_not_accepted")
            raise Refused("initiative_id_invalid")
        folder = self.root / initiative_id
        if not (folder / "initiative.json").is_file():
            raise Refused("initiative_not_found")
        return folder

    def _load(self, folder: Path) -> dict:
        return json.loads((folder / "initiative.json").read_text("utf-8"))

    def _replayed(self, operation_id: str):
        path = self.root / "operations" / (hashlib.sha256(operation_id.encode()).hexdigest() + ".json")
        return path, (json.loads(path.read_text("utf-8")) if path.is_file() else None)

    def _next_id(self) -> str:
        year = datetime.fromtimestamp(self._clock(), timezone.utc).year
        path = self.root / "counter.json"
        counter = json.loads(path.read_text("utf-8")) if path.is_file() else {"year": year, "next": 1}
        if counter.get("year") != year:
            counter = {"year": year, "next": 1}
        number = int(counter["next"])
        while (self.root / f"PI-{year}-{number:04d}").exists():    # a crash after rename, before the counter moved
            number += 1
        if number > 9999:
            raise Refused("initiative_series_full")
        self._write_json(path, {"year": year, "next": number + 1})
        return f"PI-{year}-{number:04d}"

    # ---- staging ------------------------------------------------------------------------------------------
    def _purge_staging(self) -> None:
        cutoff = self._clock() - STAGING_TTL_S
        for path in (self.root / "staging").iterdir():
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass

    def stage(self, sha256: str, data: bytes) -> dict:
        """Keep one uploaded file until ``file`` or ``attach`` names it. The body must hash to ``sha256``."""
        if not isinstance(sha256, str) or not SHA.match(sha256):
            raise Refused("file_sha256_invalid")
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise Refused("file_empty")
        if len(data) > MAX_FILE:
            raise Refused("file_too_large")
        if hashlib.sha256(data).hexdigest() != sha256:
            raise Refused("file_sha256_mismatch")
        with self._locked():
            self._purge_staging()
            path = self.root / "staging" / sha256
            if not path.is_file():
                tmp = path.with_name(sha256 + ".tmp-" + uuid.uuid4().hex[:8])
                tmp.write_bytes(bytes(data))
                os.replace(tmp, path)
            else:
                os.utime(path)
        return {"file_ref": sha256, "bytes": len(data)}

    def _staged(self, files) -> list[tuple[str, str, Path]]:
        if not isinstance(files, list) or len(files) > MAX_FILES:
            raise Refused("packet_too_many_files")
        out, seen = [], set()
        for item in files:
            if not isinstance(item, dict) or set(item) != {"file_ref", "name"}:
                raise Refused("packet_entry_invalid")
            ref = item["file_ref"]
            if not isinstance(ref, str) or not SHA.match(ref):
                raise Refused("file_ref_invalid")
            path = self.root / "staging" / ref
            if not path.is_file():
                raise Refused("file_ref_not_staged")
            if hashlib.sha256(path.read_bytes()).hexdigest() != ref:
                raise Refused("file_sha256_mismatch")
            if ref in seen:
                continue
            seen.add(ref)
            out.append((ref, _safe_name(item["name"]), path))
        return out

    @staticmethod
    def _add_files(folder: Path, record: dict, staged) -> list[dict]:
        packet = record["packet"]
        known = {p["sha256"] for p in packet}
        added = []
        for ref, name, path in staged:
            if ref in known:
                continue                                       # stored once per initiative
            size = path.stat().st_size
            if len(packet) + 1 > MAX_FILES:
                raise Refused("packet_too_many_files")
            if sum(p["bytes"] for p in packet) + size > MAX_TOTAL:
                raise Refused("packet_too_large")
            stored = f"{ref[:12]}-{name}"
            shutil.copyfile(path, folder / "packet" / stored)
            os.chmod(folder / "packet" / stored, 0o400)
            entry = {"sha256": ref, "name": name, "stored_as": stored, "bytes": size}
            packet.append(entry)
            known.add(ref)
            added.append(entry)
        return added

    # ---- writes -------------------------------------------------------------------------------------------
    def file(self, *, title, summary, files, source: dict, filed_by: str, operation_id: str, validate) -> dict:
        title, summary = _text(title, MAX_TITLE, "title_invalid"), _text(summary, MAX_SUMMARY, "summary_invalid")
        with self._locked():
            op_path, prior = self._replayed(operation_id)
            if prior is not None:
                return {**prior, "replayed": True}
            staged = self._staged(files)
            validate()                                          # authorization again, just before the write
            initiative_id = self._next_id()
            final = self.root / initiative_id
            work = self.root / (".new-" + initiative_id + "-" + uuid.uuid4().hex[:8])
            (work / "packet").mkdir(parents=True, mode=0o700)
            now = _now()
            record = {"id": initiative_id, "title": title, "summary": summary, "state": "parked", "revision": 1,
                      "created": now, "updated": now, "filed_by": filed_by, "source": source, "packet": []}
            try:
                self._add_files(work, record, staged)
                self._write_json(work / "initiative.json", record)
                os.rename(work, final)
            except BaseException:
                shutil.rmtree(work, ignore_errors=True)
                raise
            with open(final / "history.jsonl", "a", encoding="utf-8") as h:
                h.write(json.dumps({"at": now, "event": "filed", "by": filed_by, "source": source,
                                    "packet": [p["sha256"] for p in record["packet"]]}, sort_keys=True) + "\n")
            result = {"status": "filed", "initiative_id": initiative_id, "state": "parked", "revision": 1,
                      "packet": record["packet"]}
            self._write_json(op_path, result)
        return result

    def attach(self, *, initiative_id, files, source: dict, filed_by: str, operation_id: str, validate) -> dict:
        with self._locked():
            op_path, prior = self._replayed(operation_id)
            if prior is not None:
                return {**prior, "replayed": True}
            folder = self._folder(initiative_id)
            staged = self._staged(files)
            if not staged:
                raise Refused("packet_entry_invalid")
            validate()
            record = self._load(folder)
            work = folder / (".attach-" + uuid.uuid4().hex[:8])
            work.mkdir(mode=0o700)
            try:
                (work / "packet").mkdir(mode=0o700)
                added = self._add_files(work, record, staged)
                for entry in added:
                    os.replace(work / "packet" / entry["stored_as"], folder / "packet" / entry["stored_as"])
            finally:
                shutil.rmtree(work, ignore_errors=True)
            now = _now()
            if added:
                record["revision"] += 1
                record["updated"] = now
                self._write_json(folder / "initiative.json", record)
                with open(folder / "history.jsonl", "a", encoding="utf-8") as h:
                    h.write(json.dumps({"at": now, "event": "packet_added", "by": filed_by, "source": source,
                                        "packet": [p["sha256"] for p in added]}, sort_keys=True) + "\n")
            result = {"status": "attached" if added else "already_in_packet", "initiative_id": record["id"],
                      "state": record["state"], "revision": record["revision"], "added": added}
            self._write_json(op_path, result)
        return result

    # ---- reads --------------------------------------------------------------------------------------------
    def list(self) -> dict:
        items = []
        for folder in sorted(self.root.glob("PI-*")):
            if ID.match(folder.name) and (folder / "initiative.json").is_file():
                r = self._load(folder)
                items.append({k: r[k] for k in ("id", "title", "state", "revision", "updated")}
                             | {"packet_files": len(r["packet"])})
        return {"initiatives": items}

    def read(self, initiative_id) -> dict:
        record = self._load(self._folder(initiative_id))
        return {"initiative": record}

    def read_file(self, initiative_id, sha256) -> dict:
        folder = self._folder(initiative_id)
        if not isinstance(sha256, str) or not SHA.match(sha256):
            raise Refused("file_sha256_invalid")
        entry = next((p for p in self._load(folder)["packet"] if p["sha256"] == sha256), None)
        if entry is None:
            raise Refused("file_not_in_packet")
        data = (folder / "packet" / entry["stored_as"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != sha256:
            raise RuntimeError("packet_file_damaged")
        out = {"file": entry}
        if entry["name"].lower().endswith(TEXT_TYPES):
            text = data.decode("utf-8", errors="replace")
            out["text"] = text[:MAX_TEXT]
            out["truncated"] = len(text) > MAX_TEXT
        else:
            out["text"] = None
        return out


# ---- the authenticated operation surface -------------------------------------------------------------------
READ_OPS = {"list": set(), "read": {"initiative_id"}, "read_file": {"initiative_id", "sha256"}}
WRITE_OPS = {"file": ({"title", "summary", "files", "source_event_id"}, {"title", "summary", "source_event_id"}),
             "attach": ({"initiative_id", "files", "source_event_id"}, {"initiative_id", "files", "source_event_id"})}


def _authorize(svc, token, scope: str, need: str):
    b = svc.bindings.resolve(token, svc._clock())
    if (b is None or b.origin != "agent" or b.seat not in ("mc", "cos") or b.environment != svc.environment
            or scope not in b.read_scopes or need not in b.professional_initiatives
            or (need == "file" and (b.seat != "cos" or scope not in b.scopes))):
        raise PermissionError("initiative scope unavailable")
    return b


def execute(svc, token, root, payload) -> dict:
    """One JSON operation: ``{"operation", "scope", "arguments"}``. Raises Refused, PermissionError or ValueError."""
    if not isinstance(payload, dict) or set(payload) != {"operation", "scope", "arguments"}:
        raise ValueError("invalid initiative request")
    op, scope, args = payload["operation"], payload["scope"], payload["arguments"]
    if not isinstance(scope, str) or not isinstance(args, dict) or op not in (*READ_OPS, *WRITE_OPS):
        raise ValueError("invalid initiative operation")
    if op in READ_OPS:
        if set(args) != READ_OPS[op]:
            raise ValueError("invalid initiative arguments")
        bound = _authorize(svc, token, scope, "read")
        store = Store(root, bound.principal, bound.environment)
        return getattr(store, op)(*[args[k] for k in ("initiative_id", "sha256") if k in args])
    allowed, required = WRITE_OPS[op]
    if set(args) - allowed or required - set(args):
        raise ValueError("invalid initiative arguments")
    if args.get("files") is None and op == "file":
        args = {**args, "files": []}
    bound = _authorize(svc, token, scope, "file")
    source_id = args["source_event_id"]
    if not isinstance(source_id, str):
        raise ValueError("invalid source event")
    source = svc.get("source_event", source_id, token)
    if (source is None or source.get("producer") != "platform" or source.get("data_scope") != scope
            or source.get("source", {}).get("store") not in WRITE_STORES):
        raise PermissionError("kept owner source required")

    def validate():
        if _authorize(svc, token, scope, "file") != bound:
            raise PermissionError("initiative binding changed")
        if svc.get("source_event", source_id, token) != source:
            raise PermissionError("initiative source changed")

    from core.canonical_json import digest
    operation_id = op + ":" + digest([bound.seat, source_id, {k: v for k, v in args.items()}])
    kept = {"store": source["source"]["store"], "id": source["source"]["id"], "source_event_id": source_id}
    store = Store(root, bound.principal, bound.environment)
    if op == "file":
        return store.file(title=args["title"], summary=args["summary"], files=args["files"], source=kept,
                          filed_by=bound.seat, operation_id=operation_id, validate=validate)
    return store.attach(initiative_id=args["initiative_id"], files=args["files"], source=kept,
                        filed_by=bound.seat, operation_id=operation_id, validate=validate)


def stage_upload(svc, token, root, scope: str, sha256: str, data: bytes) -> dict:
    bound = _authorize(svc, token, scope, "file")
    return Store(root, bound.principal, bound.environment).stage(sha256, data)

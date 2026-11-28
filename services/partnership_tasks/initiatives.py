"""Private professional-initiative store under the task service (#680; Lane G spec v0.2 §2.6, changes 5-7).

A parked home for professional initiatives CoS files from the owner's own turn, each with its packet of files:

    <root>/<partition>/                      one partition per (principal, environment); no caller-chosen path
        counter.json                         {"year": 2026, "next": 2}, advanced under the partition lock
        PI-2026-0001/initiative.json         id, title, summary, state, revision, created, updated, filed_by, source, packet[]
        PI-2026-0001/packet/<sha12>-<name>   the bytes, written once
        PI-2026-0001/history.jsonl           append-only, written last
        staging/<sha256>                     uploaded files waiting to be named by ``file`` or ``attach`` (24 h)
        operations/<op digest>.json          the first result of each write, so a replay returns it

        PI-2026-0001/documents/<kind>/v0001.md   a kept document (brief, status, ...); a new save is a new version (#692)

IDs are ``PI-YYYY-NNNN``, assigned by the store, never by a model, and never ``INIT-`` (Planning Studio's series).
An initiative is filed ``parked``. ``update`` (#692) may move it between STATES; no state starts work: filing and
updating create no task, request or proposal. A document version is written once and never replaced.

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
STATES = ("parked", "started", "under_review", "active", "closed")   # a label for the owner; none starts work
KIND = re.compile(r"^[a-z][a-z0-9-]{0,39}$")                         # brief, status, purpose, decisions, prd, ...
MAX_DOCUMENT = 120_000                                                # characters, one document version
MAX_KINDS = 20
MAX_VERSIONS = 200
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


def _same_title(a: str, b: str) -> bool:
    norm = lambda t: re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()
    return norm(a) == norm(b)


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
            for folder in self.root.glob("PI-*"):                # find first: one title, one initiative (#692)
                if ID.match(folder.name) and (folder / "initiative.json").is_file() \
                        and _same_title(self._load(folder)["title"], title):
                    raise Refused("initiative_title_exists")
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

    def _history(self, folder: Path, event: dict) -> None:
        with open(folder / "history.jsonl", "a", encoding="utf-8") as h:
            h.write(json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n")

    def update(self, *, initiative_id, changes: dict, source: dict, filed_by: str, operation_id: str,
               validate) -> dict:
        """Change the title, summary or state. The earlier values go to the history; nothing is lost."""
        new = {}
        if "title" in changes:
            new["title"] = _text(changes["title"], MAX_TITLE, "title_invalid")
        if "summary" in changes:
            new["summary"] = _text(changes["summary"], MAX_SUMMARY, "summary_invalid")
        if "state" in changes:
            if changes["state"] not in STATES:
                raise Refused("state_invalid")
            new["state"] = changes["state"]
        if not new:
            raise Refused("nothing_to_update")
        with self._locked():
            op_path, prior = self._replayed(operation_id)
            if prior is not None:
                return {**prior, "replayed": True}
            folder = self._folder(initiative_id)
            record = self._load(folder)
            if "title" in new:
                for other in self.root.glob("PI-*"):
                    if other.name != record["id"] and ID.match(other.name) and (other / "initiative.json").is_file() \
                            and _same_title(self._load(other)["title"], new["title"]):
                        raise Refused("initiative_title_exists")
            before = {k: record[k] for k in new if record[k] != new[k]}
            validate()
            now = _now()
            if before:
                record.update({k: new[k] for k in before})
                record["revision"] += 1
                record["updated"] = now
                self._write_json(folder / "initiative.json", record)
                self._history(folder, {"at": now, "event": "updated", "by": filed_by, "source": source,
                                       "before": before, "after": {k: new[k] for k in before}})
            result = {"status": "updated" if before else "unchanged", "initiative_id": record["id"],
                      "state": record["state"], "title": record["title"], "revision": record["revision"],
                      "changed": sorted(before)}
            self._write_json(op_path, result)
        return result

    def document_save(self, *, initiative_id, kind, text, source: dict, filed_by: str, operation_id: str,
                      validate) -> dict:
        """Keep one document of the initiative (its brief, its status page, ...). Each save is a new version file,
        written once; an identical save to the current version changes nothing."""
        if not isinstance(kind, str) or not KIND.match(kind):
            raise Refused("document_kind_invalid")
        if not isinstance(text, str) or not text.strip():
            raise Refused("document_empty")
        if len(text) > MAX_DOCUMENT:
            raise Refused("document_too_large")
        data = text.encode("utf-8")
        sha = hashlib.sha256(data).hexdigest()
        with self._locked():
            op_path, prior = self._replayed(operation_id)
            if prior is not None:
                return {**prior, "replayed": True}
            folder = self._folder(initiative_id)
            record = self._load(folder)
            documents = record.setdefault("documents", {})
            current = documents.get(kind)
            if current is None and len(documents) >= MAX_KINDS:
                raise Refused("document_too_many_kinds")
            if current is not None and current["version"] >= MAX_VERSIONS:
                raise Refused("document_too_many_versions")
            validate()
            now = _now()
            if current is not None and current["sha256"] == sha:
                result = {"status": "unchanged", "initiative_id": record["id"], "kind": kind,
                          "version": current["version"], "revision": record["revision"]}
            else:
                version = (current["version"] if current else 0) + 1
                home = folder / "documents" / kind
                home.mkdir(parents=True, exist_ok=True, mode=0o700)
                path = home / f"v{version:04d}.md"
                if path.exists():                              # a crash after the file, before the record moved
                    os.chmod(path, 0o600)
                tmp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex[:8])
                tmp.write_bytes(data)
                os.replace(tmp, path)
                os.chmod(path, 0o400)
                documents[kind] = {"version": version, "sha256": sha, "bytes": len(data), "updated": now}
                record["revision"] += 1
                record["updated"] = now
                self._write_json(folder / "initiative.json", record)
                self._history(folder, {"at": now, "event": "document_saved", "by": filed_by, "source": source,
                                       "kind": kind, "version": version, "sha256": sha})
                result = {"status": "saved", "initiative_id": record["id"], "kind": kind, "version": version,
                          "sha256": sha, "bytes": len(data), "revision": record["revision"]}
            self._write_json(op_path, result)
        return result

    # ---- reads --------------------------------------------------------------------------------------------
    @staticmethod
    def _row(r: dict) -> dict:
        return ({k: r[k] for k in ("id", "title", "state", "revision", "updated")}
                | {"packet_files": len(r["packet"]),
                   "documents": {k: v["version"] for k, v in sorted(r.get("documents", {}).items())}})

    def list(self) -> dict:
        items = []
        for folder in sorted(self.root.glob("PI-*")):
            if ID.match(folder.name) and (folder / "initiative.json").is_file():
                items.append(self._row(self._load(folder)))
        return {"initiatives": items}

    def find(self, query) -> dict:
        """Initiatives whose id, title or summary holds every word of ``query``. Used before filing a new one."""
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            raise Refused("query_invalid")
        words = [w for w in re.split(r"[^a-z0-9]+", query.lower()) if w]
        if not words:
            raise Refused("query_invalid")
        items = []
        for folder in sorted(self.root.glob("PI-*")):
            if ID.match(folder.name) and (folder / "initiative.json").is_file():
                r = self._load(folder)
                hay = " ".join((r["id"], r["title"], r["summary"])).lower()
                if all(w in hay for w in words):
                    items.append(self._row(r) | {"summary": r["summary"][:400]})
        return {"initiatives": items, "query": query.strip()}

    def read_document(self, initiative_id, kind, version=None) -> dict:
        folder = self._folder(initiative_id)
        if not isinstance(kind, str) or not KIND.match(kind):
            raise Refused("document_kind_invalid")
        current = self._load(folder).get("documents", {}).get(kind)
        if current is None:
            raise Refused("document_not_found")
        if version is None:
            version = current["version"]
        if isinstance(version, bool) or not isinstance(version, int) or not 1 <= version <= current["version"]:
            raise Refused("document_version_not_found")
        data = (folder / "documents" / kind / f"v{version:04d}.md").read_bytes()
        if version == current["version"] and hashlib.sha256(data).hexdigest() != current["sha256"]:
            raise RuntimeError("document_damaged")
        text = data.decode("utf-8", errors="replace")
        return {"document": {"kind": kind, "version": version, "latest": current["version"],
                             "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)},
                "text": text[:MAX_DOCUMENT], "truncated": False}

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
READ_OPS_692 = {"find": ({"query"}, {"query"}),                           # (allowed, required), in call order
                "read_document": ({"initiative_id", "kind", "version"}, {"initiative_id", "kind"})}
WRITE_OPS = {"file": ({"title", "summary", "files", "source_event_id"}, {"title", "summary", "source_event_id"}),
             "attach": ({"initiative_id", "files", "source_event_id"}, {"initiative_id", "files", "source_event_id"}),
             "attach_text": ({"initiative_id", "name", "text", "source_event_id"},
                             {"initiative_id", "name", "text", "source_event_id"}),
             "update": ({"initiative_id", "title", "summary", "state", "source_event_id"},
                        {"initiative_id", "source_event_id"}),
             "document_save": ({"initiative_id", "kind", "text", "source_event_id"},
                               {"initiative_id", "kind", "text", "source_event_id"})}


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
    if not isinstance(scope, str) or not isinstance(args, dict) or op not in (*READ_OPS, *READ_OPS_692, *WRITE_OPS):
        raise ValueError("invalid initiative operation")
    if op in READ_OPS_692:
        allowed, required = READ_OPS_692[op]
        if set(args) - allowed or required - set(args):
            raise ValueError("invalid initiative arguments")
        bound = _authorize(svc, token, scope, "read")
        store = Store(root, bound.principal, bound.environment)
        if op == "find":
            return store.find(args["query"])
        return store.read_document(args["initiative_id"], args["kind"], args.get("version"))
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
    common = dict(source=kept, filed_by=bound.seat, operation_id=operation_id, validate=validate)
    if op == "update":
        return store.update(initiative_id=args["initiative_id"],
                            changes={k: args[k] for k in ("title", "summary", "state") if args.get(k) is not None},
                            **common)
    if op == "document_save":
        return store.document_save(initiative_id=args["initiative_id"], kind=args["kind"], text=args["text"],
                                   **common)
    files = args.get("files")
    if op == "attach_text":                                    # words pasted in the chat become a packet file
        text = args["text"]
        if not isinstance(text, str) or not text.strip():
            raise Refused("file_empty")
        name = _safe_name(args["name"])
        if not name.lower().endswith(TEXT_TYPES):
            name += ".md"
        store._folder(args["initiative_id"])                   # refuse an unknown initiative before staging
        data = text.encode("utf-8")
        files = [{"file_ref": store.stage(hashlib.sha256(data).hexdigest(), data)["file_ref"], "name": name}]
    return store.attach(initiative_id=args["initiative_id"], files=files, **common)


def stage_upload(svc, token, root, scope: str, sha256: str, data: bytes) -> dict:
    bound = _authorize(svc, token, scope, "file")
    return Store(root, bound.principal, bound.environment).stage(sha256, data)

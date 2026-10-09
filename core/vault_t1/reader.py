"""Read-only access to a shelf folder, scoped, with every file checked as it is read."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

import yaml

RECORD_DIRS = ("sessions-raw", "sessions", "notes", "turns", "snapshots", "briefs")
ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
EDITION_NAME = re.compile(r"^(\d+)--([0-9a-f]{12})\.([a-z0-9]+)$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_RECORD_BYTES = 32 * 1024 * 1024
MAX_EDITION_BYTES = 256 * 1024 * 1024


class VaultError(Exception):
    """A fixed reason code (never record text). ``code`` is stable; ``exit`` is the CLI exit status."""
    exit = 2

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class NotFound(VaultError):
    exit = 8


class Damaged(VaultError):
    exit = 5


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_file(path: Path, limit: int) -> bytes:
    """A regular, non-linked file's bytes, bounded."""
    try:
        st = os.lstat(path)
    except OSError:
        raise NotFound("file_missing") from None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise Damaged("not_a_regular_file")
    if st.st_size > limit:
        raise Damaged("file_too_large")
    with open(path, "rb") as handle:
        data = handle.read(limit + 1)
    if len(data) != st.st_size:
        raise Damaged("file_changed_while_read")
    return data


def split_record(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise Damaged("no_front_matter")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise Damaged("front_matter_not_closed")
    try:
        meta = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError:
        raise Damaged("front_matter_not_yaml") from None
    if not isinstance(meta, dict):
        raise Damaged("front_matter_not_a_mapping")
    return meta, text[end + 5:].lstrip("\n")


@dataclass
class Edition:
    number: int
    sha12: str
    ext: str
    path: Path

    @property
    def name(self) -> str:
        return self.path.name


@dataclass
class Record:
    id: str
    kind_dir: str
    stem: str
    path: Path
    meta: dict
    raw: bytes
    editions: list[Edition] = field(default_factory=list)

    @property
    def provider(self) -> str:
        return str(self.meta.get("source", "")).split(":", 1)[0]

    def current(self) -> Edition | None:
        want = self.meta.get("edition")
        return next((e for e in self.editions if e.number == want), None)


def parse_edition(data: bytes) -> tuple[dict, list[dict]]:
    """(header, rows). Rows are turn rows (with ``ordinal``) and note/ref rows; a line that is not JSON is damage."""
    lines = data.decode("utf-8").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    try:
        header = json.loads(lines[0])
        rows = [json.loads(line) for line in lines[1:]]
    except (ValueError, IndexError):
        raise Damaged("edition_not_json_lines") from None
    if not isinstance(header, dict) or not all(isinstance(r, dict) for r in rows):
        raise Damaged("edition_shape")
    return header, rows


def turns_of(rows: list[dict]) -> list[dict]:
    return [r for r in rows if "ordinal" in r]


class Vault:
    """A shelf folder as one caller sees it: owner scope by default; mandate-scoped records only with that mandate named."""

    def __init__(self, root: str, mandates: tuple[str, ...] = ()):
        self.root = Path(root)
        if not self.root.is_dir() or self.root.is_symlink():
            raise NotFound("vault_root_missing")
        self.mandates = tuple(m for m in mandates)
        self.unreadable = 0
        self._records: list[Record] | None = None

    # ── enumeration ───────────────────────────────────────────────────────────────────────────────────────────────
    def _all(self) -> list[Record]:
        if self._records is not None:
            return self._records
        out, self.unreadable = [], 0
        for kind in RECORD_DIRS:
            base = self.root / kind
            if not base.is_dir() or base.is_symlink():
                continue
            for entry in sorted(base.iterdir()):
                md = entry / f"{entry.name}.md" if entry.is_dir() and not entry.is_symlink() else None
                if md is None or not md.is_file():
                    continue
                try:
                    raw = read_file(md, MAX_RECORD_BYTES)
                    meta, _ = split_record(raw.decode("utf-8"))
                except (VaultError, UnicodeDecodeError):
                    self.unreadable += 1
                    continue
                if not ULID.match(str(meta.get("id", ""))):
                    self.unreadable += 1
                    continue
                rec = Record(meta["id"], kind, entry.name, md, meta, raw)
                folder = entry / "editions"
                if folder.is_dir() and not folder.is_symlink():
                    for f in sorted(folder.iterdir()):
                        m = EDITION_NAME.match(f.name)
                        if m and f.is_file() and not f.is_symlink():
                            rec.editions.append(Edition(int(m.group(1)), m.group(2), m.group(3), f))
                    rec.editions.sort(key=lambda e: e.number)
                out.append(rec)
        out.sort(key=lambda r: (str(r.meta.get("created", "")), r.id))
        self._records = out
        return out

    def permitted(self, rec: Record) -> bool:
        scope = str(rec.meta.get("scope", ""))
        return scope == "robert" or (scope.startswith("mandate:") and scope in {f"mandate:{m}" for m in self.mandates})

    def records(self) -> list[Record]:
        return [r for r in self._all() if self.permitted(r)]

    def withheld(self) -> int:
        """How many records exist outside this caller's scope. A count only: no title, no source, no ID."""
        return sum(1 for r in self._all() if not self.permitted(r))

    def find(self, ref: str) -> Record:
        if not isinstance(ref, str) or len(ref) < 6:
            raise NotFound("record_not_found")
        key = ref.upper()
        hits = [r for r in self.records() if r.id == key or r.id.startswith(key) or r.id.endswith(key)]
        if len(hits) != 1:
            raise NotFound("record_not_found" if not hits else "record_ref_ambiguous")
        return hits[0]

    # ── editions ──────────────────────────────────────────────────────────────────────────────────────────────────
    def edition_bytes(self, rec: Record, edition: Edition) -> bytes:
        """The exact edition bytes, refused unless they hash to the name they were stored under (and, for the current
        edition, to the hash the record declares)."""
        data = read_file(edition.path, MAX_EDITION_BYTES)
        digest = sha256(data)
        if digest[:12] != edition.sha12:
            raise Damaged("edition_hash_mismatch")
        if edition.number == rec.meta.get("edition") and rec.meta.get("edition_hash") not in (None, digest):
            raise Damaged("current_edition_differs_from_record")
        return data

    def open(self, ref: str, number: int | None = None) -> tuple[Record, Edition, bytes]:
        rec = self.find(ref)
        edition = rec.current() if number is None else next((e for e in rec.editions if e.number == number), None)
        if edition is None:
            raise NotFound("edition_not_found")
        return rec, edition, self.edition_bytes(rec, edition)

    # ── views ─────────────────────────────────────────────────────────────────────────────────────────────────────
    def summary(self, rec: Record) -> dict:
        norm = rec.meta.get("normalized") or {}
        return {"id": rec.id, "dir": rec.kind_dir, "kind": rec.meta.get("kind"), "chair": rec.meta.get("chair"), "source": rec.meta.get("source"),
                "provider": rec.provider, "created": rec.meta.get("created"), "tier": rec.meta.get("tier"), "scope": rec.meta.get("scope"),
                "title": norm.get("title") or rec.stem.split("--")[0], "edition": rec.meta.get("edition"),
                "editions": [e.number for e in rec.editions], "turns": norm.get("turns"), "turns_by_role": norm.get("turns_by_role") or {}}

    def list(self) -> dict:
        return {"records": [self.summary(r) for r in self.records()], "withheld_by_scope": self.withheld(), "unreadable": self.unreadable}

    def _workshop_events(self, rec: Record) -> list[dict]:
        cur = rec.current()
        if cur is None or rec.provider != "workshop":
            return []
        _, rows = parse_edition(self.edition_bytes(rec, cur))
        return [t["attrs"]["event"] for t in turns_of(rows) if isinstance(t.get("attrs"), dict) and isinstance(t["attrs"].get("event"), dict)]

    def topics(self) -> dict:
        tags: dict[str, int] = {}
        work: dict[str, int] = {}
        for rec in self.records():
            for tag in rec.meta.get("tags") or []:
                tags[str(tag)] = tags.get(str(tag), 0) + 1
            for ev in self._workshop_events(rec):
                if ev.get("topic"):
                    work[ev["topic"]] = work.get(ev["topic"], 0) + 1
        return {"tags": dict(sorted(tags.items())), "workshop_topics": dict(sorted(work.items())), "withheld_by_scope": self.withheld()}

    def topic_members(self, topic: str) -> list[Record]:
        out = []
        for rec in self.records():
            if topic in (rec.meta.get("tags") or []) or any(ev.get("topic") == topic for ev in self._workshop_events(rec)):
                out.append(rec)
        return out

    def coverage(self) -> dict:
        rows, totals, omitted, flagged, roles = [], {"records": 0, "turns": 0, "redacted_turns": 0, "malformed_lines": 0}, {}, 0, {}
        for rec in self.records():
            norm = rec.meta.get("normalized") or {}
            cov = norm.get("coverage") or {}
            row = {"id": rec.id, "source": rec.meta.get("source"), "turns": norm.get("turns", 0), "turns_by_role": norm.get("turns_by_role") or {},
                   "omitted": norm.get("omitted") or {}, "redacted_turns": norm.get("redacted_turns", 0),
                   "malformed_lines": norm.get("malformed_lines", 0), "coverage": cov, "flags": cov.get("flags") or []}
            rows.append(row)
            totals["records"] += 1
            totals["turns"] += row["turns"] or 0
            totals["redacted_turns"] += row["redacted_turns"] or 0
            totals["malformed_lines"] += row["malformed_lines"] or 0
            flagged += bool(row["flags"])
            for k, v in row["omitted"].items():
                omitted[k] = omitted.get(k, 0) + v
            for k, v in row["turns_by_role"].items():
                roles[k] = roles.get(k, 0) + v
        return {"records": rows, "totals": {**totals, "turns_by_role": dict(sorted(roles.items())), "omitted": dict(sorted(omitted.items())),
                                            "flagged_records": flagged},
                "withheld_by_scope": self.withheld(), "unreadable": self.unreadable,
                "words": {"saved": "a retained edition exists on disk (run verify to prove it intact)",
                          "unsupported_or_excluded": "counted under omitted and flags, never silently absent"}}

    def decisions(self) -> dict:
        memory, trail = [], []
        for rec in self.records():
            for e in rec.meta.get("events") or []:
                if isinstance(e, dict) and e.get("kind") != "edition-added":
                    memory.append({"record": rec.id, **{k: e.get(k) for k in ("kind", "by", "at", "via", "edition", "note")}})
            for ev in self._workshop_events(rec):
                if ev.get("kind") == "decision":
                    p = ev.get("payload") or {}
                    trail.append({"record": rec.id, "event_id": ev["event_id"], "seq": ev.get("seq"), "at": ev.get("at"), "actor": ev.get("actor"),
                                  "record_event_kind": p.get("record_event_kind"), "resolves": p.get("resolves") or [],
                                  "supersedes": ev.get("supersedes"), "cites_authority": ev.get("authority_ref") is not None,
                                  "text": ev.get("text"), "topic": ev.get("topic")})
        return {"memory_events": memory, "workshop_decisions": trail}

    def search(self, term: str, *, ignore_case: bool = False, all_editions: bool = False, limit: int = 50, context: int = 40) -> dict:
        """Literal text search over turn text. No regular expressions, no index, no ranking: a substring and where it is."""
        if not isinstance(term, str) or not term:
            raise VaultError("empty_search_term")
        needle = term.lower() if ignore_case else term
        hits, scanned, truncated = [], 0, False
        for rec in self.records():
            editions = rec.editions if all_editions else [e for e in [rec.current()] if e]
            for edition in editions:
                _, rows = parse_edition(self.edition_bytes(rec, edition))
                scanned += 1
                for t in turns_of(rows):
                    text = str(t.get("text", ""))
                    hay = text.lower() if ignore_case else text
                    at = hay.find(needle)
                    if at < 0:
                        continue
                    if len(hits) >= limit:
                        truncated = True
                        break
                    lo, hi = max(0, at - context), min(len(text), at + len(term) + context)
                    hits.append({"record": rec.id, "source": rec.meta.get("source"), "edition": edition.number, "ordinal": t["ordinal"],
                                 "speaker": t.get("speaker"), "who": (t.get("attrs") or {}).get("who"), "offset": at, "snippet": text[lo:hi]})
                if truncated:
                    break
            if truncated:
                break
        return {"term": term, "ignore_case": ignore_case, "hits": hits, "editions_scanned": scanned, "truncated": truncated,
                "withheld_by_scope": self.withheld()}

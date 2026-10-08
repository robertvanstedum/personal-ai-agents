"""Chat drop-off (v0.6 section 9; v0.7 Unit 2): a landing folder, a bounded header, one retained document and one entry.

``<workshop>/inbox/`` receives files saved by Robert from a chat. A handoff becomes a journal entry; a conversation is a memory
source and is held (``source_approval_required``) until its own source approval exists. Nothing is moved, deleted or executed:
the files stay where Robert put them and the ingester writes small manifests next to them:

    inbox/_processed/<read hash>.json   which entry and retained document this file produced (never replaced)
    inbox/_held/<read hash>.json        why it was not ingested (replaced when the reason changes, removed on success)

The header author is a *claim*, never an identity: the entry's origin is the inbox adapter with basis ``claimed``. A header
``decision`` is a proposal, a quoted "Robert approved" grants nothing, and no model reads or routes the text. The same bytes
posted again (any filename) are the same entry; different bytes are a new entry.
"""
from __future__ import annotations

import os
import re
import stat
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime

import yaml

from core.workshop_journal import artifacts as art
from core.workshop_journal import fsutil, schema, strictjson
from core.workshop_journal.errors import JournalError, Missing, SourceRefused
from core.workshop_journal.journal import Journal

INBOX, PROCESSED, HELD = "inbox", "_processed", "_held"
PARSER_VERSION = "1"
NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "minimoi:workshop:dropoff:v1")
CHAT_SENDERS = ("claude-chat", "grok-chat")
KINDS = ("handoff", "update", "decision", "question_for_robert", "receipt", "conversation")
HEADER_KEYS = ("workshop", "sender", "kind", "topic", "session_started", "session_ended", "reply_to", "supersedes", "recipients",
               "artifacts", "captured", "classification")
REQUIRED = ("workshop", "sender", "kind", "topic")
MAX_HANDOFF_BYTES = 2 * 1024 * 1024
MAX_CONVERSATION_BYTES = 64 * 1024 * 1024
MAX_HEADER_BYTES = 16 * 1024
MAX_HEADER_EVENTS = 400
MAX_DEPTH = 4
NAME_OK = re.compile(r"^[A-Za-z0-9._-]{1,200}$")
HANDOFF_NAME = re.compile(r"^HANDOFF_(?P<sender>[a-z0-9-]+)_(?P<topic>[a-z0-9-]+)_(?P<date>\d{4}-\d{2}-\d{2})_(?P<hm>\d{4})\.md$")
CONVERSATION_NAME = re.compile(r"^CONVERSATION_(?P<sender>[a-z0-9-]+)_(?P<topic>[a-z0-9-]+)_(?P<date>\d{4}-\d{2}-\d{2})\.md$")
TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
NULLS = ("", "~", "null", "Null", "NULL")


class HeaderError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# ── the header: strings only, no tags, anchors, aliases or implicit types ───────────────────────────────────────────
def split_header(data: bytes) -> tuple[bytes, bytes]:
    """(header bytes, body bytes). The header is the block between a first line of ``---`` and the next line of ``---``."""
    if not data.startswith(b"---\n"):
        raise HeaderError("no_header")
    end = data.find(b"\n---\n", 3)
    tail_marker = data.find(b"\n---", 3) if end < 0 else end
    if end < 0:
        if tail_marker >= 0 and data[tail_marker:].rstrip() == b"---":
            end, body = tail_marker, b""
        else:
            raise HeaderError("header_not_closed")
    else:
        body = data[end + 5:]
    header = data[4:end]
    if len(header) > MAX_HEADER_BYTES:
        raise HeaderError("header_too_large")
    return header, body


def parse_header(raw: bytes) -> dict:
    """The header as plain strings, nulls, lists and one level of mappings, or a fixed refusal. Keys must be unique strings."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HeaderError("header_not_utf8") from None
    try:
        events = list(yaml.parse(text, Loader=yaml.SafeLoader))
    except yaml.YAMLError:
        raise HeaderError("bad_yaml") from None
    if len(events) > MAX_HEADER_EVENTS:
        raise HeaderError("header_too_complex")
    for ev in events:
        if isinstance(ev, yaml.AliasEvent) or getattr(ev, "anchor", None) is not None:
            raise HeaderError("anchors_and_aliases_refused")
        if getattr(ev, "tag", None) is not None:
            raise HeaderError("tags_refused")
    pos = 0

    def node(depth: int):
        nonlocal pos
        if depth > MAX_DEPTH:
            raise HeaderError("header_too_deep")
        ev = events[pos]
        pos += 1
        if isinstance(ev, yaml.ScalarEvent):
            if ev.style is None and ev.value in NULLS:
                return None
            return ev.value                                           # every other scalar stays a string: yes/no/1/dates are text
        if isinstance(ev, yaml.SequenceStartEvent):
            out = []
            while not isinstance(events[pos], yaml.SequenceEndEvent):
                out.append(node(depth + 1))
            pos += 1
            return out
        if isinstance(ev, yaml.MappingStartEvent):
            out = {}
            while not isinstance(events[pos], yaml.MappingEndEvent):
                key = node(depth + 1)
                if not isinstance(key, str):
                    raise HeaderError("bad_key")
                if key in out:
                    raise HeaderError("duplicate_key")
                out[key] = node(depth + 1)
            pos += 1
            return out
        raise HeaderError("bad_yaml")

    # events: StreamStart, DocumentStart, <root>, DocumentEnd, StreamEnd (a second document is refused)
    kinds = [type(e).__name__ for e in events]
    if kinds.count("DocumentStartEvent") != 1:
        raise HeaderError("one_document_only" if kinds.count("DocumentStartEvent") > 1 else "empty_header")
    pos = kinds.index("DocumentStartEvent") + 1
    root = node(0)
    if not isinstance(root, dict):
        raise HeaderError("header_not_a_mapping")
    return root


def _time(value, field_name: str):
    if value is None:
        return None
    if not isinstance(value, str) or not TIME.fullmatch(value):
        raise HeaderError(f"bad_{field_name}")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HeaderError(f"bad_{field_name}") from None


@dataclass
class Header:
    workshop: str
    sender: str
    kind: str
    topic: str
    session_started: str | None = None
    session_ended: str | None = None
    reply_to: str | None = None
    supersedes: str | None = None
    recipients: list = field(default_factory=list)
    artifacts: list = field(default_factory=list)
    captured: str | None = None
    classification: str = "normal"


def validate_header(doc: dict, workshop: str, actors: tuple[str, ...]) -> Header:
    unknown = set(doc) - set(HEADER_KEYS)
    if unknown:
        raise HeaderError("unknown_header_field")
    missing = [k for k in REQUIRED if doc.get(k) is None]
    if missing:
        raise HeaderError(f"missing_{missing[0]}")
    for key in ("workshop", "sender", "kind", "topic"):
        if not isinstance(doc[key], str):
            raise HeaderError(f"bad_{key}")
    if doc["workshop"] != workshop:
        raise HeaderError("wrong_workshop")
    if doc["sender"] not in CHAT_SENDERS:
        raise HeaderError("unknown_sender")
    if doc["kind"] not in KINDS:
        raise HeaderError("unknown_kind")
    if not schema.TOPIC_RE.fullmatch(doc["topic"]):
        raise HeaderError("bad_topic")
    started, ended = _time(doc.get("session_started"), "session_started"), _time(doc.get("session_ended"), "session_ended")
    if started and ended and ended < started:
        raise HeaderError("session_ends_before_it_starts")
    for key in ("reply_to", "supersedes"):
        value = doc.get(key)
        if value is not None:
            if not isinstance(value, str):
                raise HeaderError(f"bad_{key}")
            try:
                schema.uuid_text(value, key)
            except schema.SchemaError:
                raise HeaderError(f"bad_{key}") from None
    recipients = doc.get("recipients")
    if recipients is None:
        recipients = []
    if not isinstance(recipients, list) or len(recipients) > 8 or not all(isinstance(r, str) and r in actors for r in recipients) \
            or len(set(recipients)) != len(recipients):
        raise HeaderError("bad_recipients")
    named = doc.get("artifacts")
    if named is None:
        named = []
    if not isinstance(named, list) or len(named) > 15 or not all(isinstance(n, str) and NAME_OK.fullmatch(n) for n in named):
        raise HeaderError("bad_artifacts")
    classification = doc.get("classification") or "normal"
    if classification not in ("normal", "private", "excluded"):
        raise HeaderError("bad_classification")
    captured = doc.get("captured")
    if captured is not None and captured not in ("handoff", "conversation"):
        raise HeaderError("bad_captured")
    return Header(doc["workshop"], doc["sender"], doc["kind"], doc["topic"], doc.get("session_started"), doc.get("session_ended"),
                  doc.get("reply_to"), doc.get("supersedes"), list(recipients), list(named), captured, classification)


def check_filename(name: str, header: Header) -> None:
    """The name is a convenience, but a name that disagrees with the header is held, never trusted either way."""
    match = HANDOFF_NAME.fullmatch(name) or CONVERSATION_NAME.fullmatch(name)
    if not match:
        raise HeaderError("filename_not_in_the_standard_form")
    is_conversation = name.startswith("CONVERSATION_")
    if (match["sender"], match["topic"]) != (header.sender, header.topic) or is_conversation != (header.kind == "conversation"):
        raise HeaderError("filename_header_mismatch")


def dropoff_id(workshop: str, original_sha256: str, retained_sha256: str) -> str:
    """The deterministic entry ID: the same bytes are the same entry, whatever the file is called."""
    return str(uuid.uuid5(NAMESPACE, strictjson.dumps([workshop, original_sha256, retained_sha256, PARSER_VERSION])))


# ── one file ───────────────────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class Outcome:
    name: str
    status: str                       # ingested | duplicate | held | source_approval_required | unstable | would_ingest
    reason: str = ""
    event_id: str | None = None
    original_sha256: str | None = None
    retained_sha256: str | None = None
    redactions: int = 0

    def to_json(self) -> dict:
        return {k: getattr(self, k) for k in ("name", "status", "reason", "event_id", "original_sha256", "retained_sha256", "redactions")}


def _envelope(h: Header, item: art.Prepared, event_id: str, name: str, journal: Journal, previous: str | None) -> dict:
    refs = [item.ref(f"inbox:{name}"[:128], name)] + [{"type": "artifact", "id": n, "availability": "pointer_only"} for n in h.artifacts]
    base = {"event_id": event_id, "actor": h.sender, "item": f"topic:{h.topic}", "topic": h.topic, "stage": None,
            "recipients": h.recipients, "in_reply_to": h.reply_to, "supersedes": h.supersedes or previous, "refs": refs}
    if h.kind in ("handoff", "update"):
        return {**base, "kind": "progress", "text": f"{h.kind.capitalize()} received from {h.sender} on {h.topic}.",
                "payload": {"action": "handoff_received" if h.kind == "handoff" else "update_received"}}
    if h.kind == "question_for_robert":
        incident = str(uuid.uuid5(NAMESPACE, "incident:" + event_id))
        return {**base, "kind": "needs_you", "recipients": ["robert"],
                "text": f"{h.sender} left a question for Robert on {h.topic}; the retained note holds it.",
                "payload": {"reason_code": "chat_question", "requested_action": "Open the retained note and answer it.",
                            "incident_id": incident}}
    if h.kind == "decision":
        return {**base, "kind": "decision", "text": f"{h.sender} proposed a decision on {h.topic}; it is a proposal, not an approval.",
                "payload": {"record_event_kind": "proposed", "resolves": [], "reason": "Proposed in a chat drop-off."}}
    if h.kind == "receipt":
        return {**base, "kind": "receipt", "text": f"{h.sender} acknowledged a request on {h.topic}.", "recipients": [],
                "payload": {"request_id": h.reply_to, "recipient": h.sender}}
    raise HeaderError("unsupported_kind")


@dataclass
class Inbox:
    journal: Journal
    settle: float = 2.0               # seconds a file must stay unchanged before it is read

    def __post_init__(self):
        self._sleep = time.sleep

    # folders -----------------------------------------------------------------------------------------------------
    def _folder(self, create: bool) -> int:
        base_fd = self.journal._open_base(create=create)
        try:
            return fsutil.open_subdir(base_fd, INBOX, create=create)
        finally:
            os.close(base_fd)

    def path(self) -> str:
        return os.path.join(self.journal.root, self.journal.id, INBOX)

    def _manifest_dir(self, inbox_fd: int, name: str, create: bool) -> int:
        return fsutil.open_subdir(inbox_fd, name, create=create)

    def _have_processed(self, inbox_fd: int, sha: str) -> dict | None:
        try:
            pfd = self._manifest_dir(inbox_fd, PROCESSED, False)
        except Missing:
            return None
        try:
            try:
                fd = fsutil.open_file(pfd, f"{sha}.json", os.O_RDONLY)
            except Missing:
                return None
            try:
                return strictjson.loads(fsutil.read_all(fd))
            finally:
                os.close(fd)
        finally:
            os.close(pfd)

    # one file ----------------------------------------------------------------------------------------------------
    def ingest_file(self, name: str, *, apply: bool = False) -> Outcome:
        if not NAME_OK.fullmatch(name) or name.startswith("."):
            return Outcome(name if NAME_OK.fullmatch(name) else "?", "held", "bad_filename")
        try:
            first = os.stat(os.path.join(self.path(), name), follow_symlinks=False)
        except OSError:
            return Outcome(name, "held", "file_unreadable")
        if not stat.S_ISREG(first.st_mode):
            return Outcome(name, "held", "not_a_regular_file")
        if self.settle:
            self._sleep(self.settle)                                  # two observations: still the same size and time afterwards
            try:
                second = os.stat(os.path.join(self.path(), name), follow_symlinks=False)
            except OSError:
                return Outcome(name, "unstable", "source_changed")
            if (first.st_size, first.st_mtime_ns, first.st_ino) != (second.st_size, second.st_mtime_ns, second.st_ino):
                return Outcome(name, "unstable", "source_changed")
        try:
            raw = art.capture_file(self.path(), name, limit=MAX_CONVERSATION_BYTES)
        except SourceRefused as exc:
            return Outcome(name, "unstable" if exc.reason == "source_changed" else "held", exc.reason)
        original = art.sha256(raw)
        known = self._known(original)
        if known is not None and known.get("parser_version") == PARSER_VERSION:
            return Outcome(name, "duplicate", "already_ingested", known.get("event_id"), original, known.get("retained_sha256"),
                           known.get("redactions", 0))
        return self._process(name, raw, original, apply, previous=known.get("event_id") if known else None)

    def _known(self, original: str) -> dict | None:
        try:
            inbox_fd = self._folder(create=False)
        except Missing:
            return None
        try:
            return self._have_processed(inbox_fd, original)
        finally:
            os.close(inbox_fd)

    def _process(self, name: str, raw: bytes, original: str, apply: bool, previous: str | None = None) -> Outcome:
        def hold(reason: str, status: str = "held", **extra) -> Outcome:
            out = Outcome(name, status, reason, original_sha256=original, **extra)
            if apply:
                self._write_held(out, len(raw))
            return out
        if len(raw) > MAX_HANDOFF_BYTES and not name.startswith("CONVERSATION_"):
            return hold("too_large")
        try:
            header_bytes, _ = split_header(raw)
            doc = parse_header(header_bytes)
            h = validate_header(doc, self.journal.id, self.journal.actors)
            check_filename(name, h)
        except HeaderError as exc:
            return hold(exc.reason)
        if h.classification != "normal":
            return hold("excluded_source")
        if h.kind == "conversation":
            return hold("source_approval_required", "source_approval_required")
        try:
            item = art.prepare(raw, source_class="handoff")
        except SourceRefused as exc:
            return hold(exc.reason)
        eid = dropoff_id(self.journal.id, item.original_sha256, item.retained_sha256)
        out = Outcome(name, "would_ingest", "", eid, original, item.retained_sha256, item.redactions)
        read = self.journal.read(deep=False)
        events = {e.get("event_id"): e for e in read.events}
        for value in (h.reply_to, h.supersedes):
            if value is not None and value not in events:
                return hold("unresolved_reference", event_id=eid)
        if h.kind == "receipt":
            target = events.get(h.reply_to) if h.reply_to else None
            if target is None or target.get("kind") != "request" or h.sender not in (target.get("recipients") or []):
                return hold("receipt_needs_an_addressed_request", event_id=eid)
        try:
            envelope = _envelope(h, item, eid, name, self.journal, previous)
        except HeaderError as exc:
            return hold(exc.reason)
        if not apply:
            return out
        result = self.journal.append(envelope, adapter="inbox", artifacts=[item])
        if not result.ok:
            return hold(f"journal_{result.status}", event_id=eid)
        self._write_processed(out, h, item, eid)
        self._clear_held(original)
        out.status = "duplicate" if result.status == "duplicate" else "ingested"
        return out

    # manifests ---------------------------------------------------------------------------------------------------
    def _write_processed(self, out: Outcome, h: Header, item: art.Prepared, eid: str) -> None:
        doc = strictjson.canonical_bytes({
            "v": 1, "original_sha256": out.original_sha256, "retained_sha256": item.retained_sha256, "redactions": item.redactions,
            "event_id": eid, "sender": h.sender, "kind": h.kind, "topic": h.topic, "filename": out.name,
            "parser_version": PARSER_VERSION, "adapter": "inbox"})
        inbox_fd = self._folder(create=True)
        try:
            pfd = self._manifest_dir(inbox_fd, PROCESSED, True)
            try:
                fsutil.publish_new(pfd, f"{out.original_sha256}.json", doc)
            finally:
                os.close(pfd)
        finally:
            os.close(inbox_fd)

    def _write_held(self, out: Outcome, size: int) -> None:
        doc = strictjson.canonical_bytes({"v": 1, "original_sha256": out.original_sha256, "reason": out.reason, "status": out.status,
                                          "filename": out.name, "size": size, "parser_version": PARSER_VERSION})
        inbox_fd = self._folder(create=True)
        try:
            hfd = self._manifest_dir(inbox_fd, HELD, True)
            try:
                fsutil.publish_replace(hfd, f"{out.original_sha256}.json", doc)
            finally:
                os.close(hfd)
        finally:
            os.close(inbox_fd)

    def _clear_held(self, original: str) -> None:
        try:
            inbox_fd = self._folder(create=False)
        except Missing:
            return
        try:
            try:
                hfd = self._manifest_dir(inbox_fd, HELD, False)
            except Missing:
                return
            try:
                os.unlink(f"{original}.json", dir_fd=hfd)
            except FileNotFoundError:
                pass
            finally:
                os.close(hfd)
        finally:
            os.close(inbox_fd)

    # a whole scan ------------------------------------------------------------------------------------------------
    def scan(self, *, apply: bool = False) -> list[Outcome]:
        """Every regular file in the landing folder, in name order. Dry run unless ``apply``. Links and folders are reported."""
        try:
            inbox_fd = self._folder(create=apply)
        except Missing:
            return []
        try:
            entries = sorted(os.scandir(inbox_fd), key=lambda e: e.name)
        finally:
            os.close(inbox_fd)
        out = []
        for entry in entries:
            if entry.name in (PROCESSED, HELD) or entry.name.startswith("."):
                continue
            if entry.is_symlink():
                out.append(Outcome(entry.name if NAME_OK.fullmatch(entry.name) else "?", "held", "file_is_a_link"))
            elif entry.is_dir(follow_symlinks=False):
                out.append(Outcome(entry.name if NAME_OK.fullmatch(entry.name) else "?", "held", "folders_are_not_ingested"))
            else:
                out.append(self.ingest_file(entry.name, apply=apply))
        return out

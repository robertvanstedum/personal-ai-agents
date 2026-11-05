"""CoS's bound ``cos_board`` tool (F2 M2, closed 5 Nov on Robert's "bind it now").

Before this, the agent held the Board key itself (``/run/secrets/cos-board-profile.json``) and signed its own calls:
any process in the agent container that could read that file could write the Board, and a write was recorded as
"CoS" with no turn. Now the agent's ``cos-bound`` plugin sends ``cos_board`` to the bound listener like
``cos_actions``. The listener resolves the sealed run and call ids to a live binding, admits the call against the
turn's quota, and only then does this module sign the request with a key that lives in cos-scheduler.

What comes from where:
- the operation and its own fields come from the agent (``OPS``), checked here before anything is sent;
- the key, principal, environment and Board come from the scheduler's profile file, never from arguments;
- the Board ``operation_id`` (its idempotency key) is derived from the bound call (``BoundCall.ref``), so a retried
  host call is the same Board operation and the Board's durable receipt answers it; a retried call id with
  different arguments is refused by the Board, never applied twice;
- the listener links the Board receipt to the call in the binding store, so every Board change traces to the turn
  (and for owner turns, the saved conversation turn) that caused it.

Scope: ``owner`` may read and write; ``meeting`` is refused (``cos-meeting`` and ``cos-closed`` never had the Board,
and the plugin denies it there too). Results are ``{"status", "body"}`` for the listener, mirroring the shapes the
agent already saw from the old plugin: ``state`` verified / not_applied / unknown, never "saved" without a receipt.

Transport: one fixed internal host and port from the scheduler's environment, no proxies, no redirects, no retries,
bounded response, signed response verified with ``core.domain_api.protocol``.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from core.domain_api import protocol
from core.domain_api.contract import DomainError, KeyProfile, packed

SCHEMA = "minimoi.cos.board_tool/2"
BASE = "/internal/domain-api/v1/guild/board"
# operation: (method, path, capability, allowed fields)
OPS = {
    "list": ("POST", "/notes/query", "notes.read", ("query", "state", "limit", "cursor")),
    "read": ("GET", "/notes/", "notes.read", ("note_ref",)),
    "save": ("POST", "/notes", "note.create", ("text", "label")),
    "update": ("PATCH", "/notes/", "note.update", ("note_ref", "expected_revision", "changes")),
    "result": ("GET", "/operations/", "operations.read", ("operation_ref",)),
    "undo": ("POST", "/operations/", "operation.undo", ("operation_ref", "expected_revision")),
    "capabilities": ("GET", "/capabilities", "capabilities.read", ()),
}
MUTATIONS = frozenset({"save", "update", "undo"})
SCOPES = {"owner": frozenset(OPS)}
LABELS = frozenset({"decide", "blocked", "remember", "followup", "idea", "fyi", None})
MAX_BODY = 16 * 1024
MAX_RESPONSE = 64 * 1024
TOTAL_S = 7.5
_NOTE_REF = re.compile(r"[1-9][0-9]{0,17}")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_CODE = re.compile(r"[a-z0-9_]{1,80}")
_IDENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


class BoardInvalid(ValueError):
    pass


def _label_ok(value):
    return value is None or (isinstance(value, str) and value in LABELS)


def _need(ok, why="board_request_invalid"):
    if not ok:
        raise BoardInvalid(why)


@dataclass(frozen=True)
class BoardProfile:
    """The scheduler's Board key for CoS. ``board`` is always ``cos-board``: CoS's own floor, never MC's."""
    key: KeyProfile
    board: str

    @property
    def principal(self):
        return self.key.principal

    @property
    def environment(self):
        return self.key.environment


def load_profile(path: str | os.PathLike) -> BoardProfile:
    """The owner-only profile file (same JSON the agent used to hold). Refuses anything else."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        _need(stat.S_ISREG(st.st_mode) and not st.st_mode & 0o077 and st.st_uid in {0, os.getuid()}
              and st.st_size <= 16384, "board_profile_not_private")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            value = json.loads(stream.read(16385).decode("utf-8"))
    finally:
        os.close(fd)
    _need(isinstance(value, dict), "board_profile_invalid")
    _need(value.get("actor") == "cos" and value.get("domain") == "guild" and value.get("board") == "cos-board"
          and value.get("environment") in {"dev", "prod"}, "board_profile_invalid")
    for k in ("key_id", "principal"):
        _need(isinstance(value.get(k), str) and _IDENT.fullmatch(value[k]), "board_profile_invalid")
    _need(isinstance(value.get("secret_hex"), str) and re.fullmatch(r"[a-f0-9]{64}", value["secret_hex"]),
          "board_profile_invalid")
    uid = value.get("numeric_user_id")
    _need(uid is None or (type(uid) is int and uid > 0), "board_profile_invalid")
    caps = frozenset("guild.board." + spec[2] for spec in OPS.values())
    key = KeyProfile(value["key_id"], value["principal"], "cos", value["environment"], "guild", caps,
                     bytes.fromhex(value["secret_hex"]), numeric_user_id=uid)
    return BoardProfile(key, "cos-board")


def operation_id(profile: BoardProfile, call_ref: str) -> str:
    """The Board idempotency key for one bound call: a UUID (version 8 layout) from the call's ref."""
    raw = bytearray(hashlib.sha256(packed(["minimoi.cos-board-tool/2", profile.principal, profile.environment,
                                            profile.board, call_ref])).digest()[:16])
    raw[6] = (raw[6] & 0x0F) | 0x80
    raw[8] = (raw[8] & 0x3F) | 0x80
    h = raw.hex()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def wire(op: str, args: dict):
    """(method, route, body bytes, capability) for one checked operation. Raises BoardInvalid before any send."""
    _need(op in OPS, "invalid_operation")
    method, path, cap, fields = OPS[op]
    _need(isinstance(args, dict) and set(args) <= set(fields), "unexpected_fields")
    route = BASE + path
    if op in ("read", "update"):
        _need(isinstance(args.get("note_ref"), str) and _NOTE_REF.fullmatch(args["note_ref"]), "note_ref_invalid")
        route += args["note_ref"]
    if op in ("result", "undo"):
        _need(isinstance(args.get("operation_ref"), str) and _UUID.fullmatch(args["operation_ref"]),
              "operation_ref_invalid")
        route += args["operation_ref"] + ("/undo" if op == "undo" else "")
    if op == "save":
        text = args.get("text")
        _need(isinstance(text, str) and text.strip() and len(text) <= 280, "text_invalid")
        _need(_label_ok(args.get("label")), "label_invalid")
    if op in ("update", "undo"):
        rev = args.get("expected_revision")
        _need(type(rev) is int and rev > 0, "expected_revision_invalid")
    if op == "update":
        ch = args.get("changes")
        _need(isinstance(ch, dict) and ch and set(ch) <= {"text", "label", "done"}, "changes_invalid")
        _need("text" not in ch or (isinstance(ch["text"], str) and ch["text"].strip() and len(ch["text"]) <= 280),
              "text_invalid")
        _need("label" not in ch or _label_ok(ch["label"]), "label_invalid")
        _need("done" not in ch or type(ch["done"]) is bool, "changes_invalid")
    if op == "list":
        _need("limit" not in args or (type(args["limit"]) is int and 1 <= args["limit"] <= 20), "limit_invalid")
        _need("state" not in args or (isinstance(args["state"], str) and args["state"] in {"active", "done", "all"}),
              "state_invalid")
        for k in ("query", "cursor"):
            _need(k not in args or (isinstance(args[k], str) and len(args[k]) <= 512), k + "_invalid")
    body = {k: args[k] for k in fields if k in args and k not in ("note_ref", "operation_ref")}
    raw = b"" if method == "GET" else packed(body)
    _need(len(raw) <= MAX_BODY, "body_too_large")
    return method, route, raw, "guild.board." + cap


def http_exchange(base_url: str):
    """A bounded sender to one fixed internal Board address. No proxies, redirects or retries."""
    session = requests.Session()
    session.trust_env = False

    def send(method, route, raw, headers):
        # One total deadline for the whole exchange (the old client's 7.5 s): a watchdog closes the response, so a
        # reply trickling in a byte at a time cannot hold a listener thread past it. Any failure is "unknown".
        deadline = time.monotonic() + TOTAL_S
        r = session.request(method, base_url.rstrip("/") + route, data=raw or None, headers=headers,
                            timeout=(2, 5), allow_redirects=False, stream=True)
        watchdog = threading.Timer(max(0.0, deadline - time.monotonic()), r.close)
        watchdog.daemon = True
        watchdog.start()
        try:
            body = bytearray()
            while len(body) <= MAX_RESPONSE:
                part = r.raw.read(4096, decode_content=False)
                if not part:
                    break
                body.extend(part)
        finally:
            watchdog.cancel()
            r.close()
        _need(time.monotonic() <= deadline, "board_deadline")
        _need(len(body) <= MAX_RESPONSE, "board_response_too_large")
        return r.status_code, bytes(body), r.headers.get("X-MiniMoi-Receipt") or ""
    return send


class CosBoardTool:
    def __init__(self, *, profile: BoardProfile | callable, exchange, clock=time.time):
        """``profile`` is a BoardProfile or a callable returning one (read fresh, so a rotated key applies at once).
        ``exchange(method, route, raw, headers) -> (status, body, signature)`` is the transport."""
        self._profile, self._exchange, self._clock = profile, exchange, clock

    def profile(self) -> BoardProfile:
        return self._profile() if callable(self._profile) else self._profile

    def serve(self, call, op: str, args: dict) -> dict:
        """Serve one admitted call (``actions_v2.BoundCall``). Never raises for a Board outcome: transport doubt is
        ``unknown`` with the operation id, so the agent recovers with ``result`` instead of writing again."""
        if call is None or not getattr(call, "call_id", None):
            return {"status": 403, "outcome": "refused", "body": {"error": "unbound"}}
        if op not in SCOPES.get(call.scope, frozenset()):
            return {"status": 403, "outcome": "refused", "body": {"error": "scope_not_allowed"}}
        try:
            method, route, raw, cap = wire(op, args)
            p = self.profile()
        except (BoardInvalid, DomainError, OSError, ValueError, TypeError, KeyError) as exc:
            code = exc.code if isinstance(exc, DomainError) else (str(exc) if isinstance(exc, BoardInvalid)
                                                                   else "board_request_invalid")
            return {"status": 422, "outcome": "refused", "body": {"state": "not_applied", "error": code}}
        mutation = op in MUTATIONS
        op_id = operation_id(p, call.ref) if mutation else ""
        try:
            headers = protocol.request_headers(p.key, method=method, route=route, raw=raw, operation=op_id,
                                               now=self._clock())
        except DomainError as exc:
            return {"status": 422, "outcome": "refused", "body": {"state": "not_applied", "error": exc.code}}
        try:
            status, body, signature = self._exchange(method, route, raw, headers)
            _need(type(status) is int and 200 <= status < 600 and not 300 <= status < 400, "status")
            _need(isinstance(body, bytes) and len(body) <= MAX_RESPONSE, "body")
            protocol.verify_response(p.key, nonce=headers["X-MiniMoi-Nonce"], status=status, raw=body,
                                     signature=signature)
            value = json.loads(body.decode("utf-8"))
            _need(isinstance(value, dict), "shape")
            if status >= 400:
                _need(value.get("state") in {"not_applied", "unknown"} and isinstance(value.get("error"), str)
                      and _CODE.fullmatch(value["error"]), "shape")
                return {"status": 200, "outcome": "refused" if value["state"] == "not_applied" else "unknown",
                        "body": {"schema": SCHEMA, "state": value["state"], "code": value["error"],
                                 "operation_id": op_id or None}}
            if mutation or op == "result":
                _need(value.get("state") in {"verified", "not_applied", "unknown"}, "shape")
                if value["state"] == "verified":
                    rc = value.get("receipt")
                    _need(isinstance(rc, dict)
                          and rc.get("operation_id") == (op_id if mutation else args["operation_ref"])
                          and rc.get("principal") == p.principal and rc.get("executor") == "cos"
                          and rc.get("environment") == p.environment and rc.get("board") == p.board
                          and (not mutation or rc.get("capability") == cap), "receipt")
        except Exception:
            # Neither an unsigned error nor a dropped reply proves that nothing was saved.
            return {"status": 200, "outcome": "unknown", "body": {
                "schema": SCHEMA, "state": "unknown", "operation_id": op_id or None, "code": "board_result_unconfirmed",
                "instruction": "Read the operation result before attempting another write. "
                               "Do not create a replacement note."}}
        if mutation or op == "result":
            outcome = {"verified": "done", "not_applied": "refused"}.get(value["state"], "unknown")
        else:
            outcome = "done"                     # a signed 2xx read; a note's own "state" is its active/done flag
        # The tool's own fields are written last, so a Board field of the same name can never replace them.
        return {"status": 200, "outcome": outcome, "body": {**value, "schema": SCHEMA, "operation_id": op_id or None}}


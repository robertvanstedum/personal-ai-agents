#!/usr/bin/env python3
"""MiniMoi 1.2's production operator: writes the by-operator files minimoi_1_2_prod_setup.py lists, and nothing else.

  check [--inputs FILE]           the default; read-only. Names and states only, never a value.
  apply --only GROUPS --inputs FILE   needs MINIMOI_1_2_SETUP_APPROVED=1. GROUPS, comma separated, each when its wait clears:
      local     new tokens, the Board key pair, seat bindings, the empty task store, hashes in .env. Waits on setup apply.
      db        roles partnership_worker and cos_board, their grants, partnership/db.url and cos-board/db.url. Waits on the
                migrations (it names missing tables).
      gateway   Guildhall's discussion keys and the voice summary keys, $20 per 30 days each. Waits on the gateway's key database.
      records   Records-issued tokens (teammates, workers, observe). Waits on Records running.
  cos-auth                        read-only: which of CoS's three agents see a healthy plan sign-in, names and states only.
  inputs-template                 prints an unapproved input file to stdout. It holds no secret.

Rules it keeps: never overwrites (an existing file is kept, a differing pair or hash is refused); refuses symlinks; writes
atomically with the consumer's uid and mode 0600; never prints, logs or puts a value in argv (values travel by stdin and
pipes it reads itself); nothing is copied from another host. No key is made without a $20 cap, and a key whose budget doesn't
read back as $20 is deleted, not kept. Anything only a person may decide comes from the input file, and a missing or
unapproved field stops the run. It has no way to open an authority or launch gate.

On the host it needs this file and minimoi_1_2_prod_setup.py beside it, and the Python standard library.
"""
from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time

if __package__:
    from . import minimoi_1_2_prod_setup as setup
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import minimoi_1_2_prod_setup as setup

DOCKER = "docker"
GATEWAY = "minimoi-model-gateway"
POSTGRES = "postgres-ai-agents"
RECORDS = "minimoi-records"
COS_AGENT = "minimoi-cos-agent-a"
COS_AGENTS = ("cos-agent-a", "cos-meeting", "cos-closed")
RECORDS_BINDING = "/etc/minimoi/records-production.json"
RECORDS_DATA = "/var/lib/minimoi-records"       # docker-compose.records-production.yml
GROUPS = ("local", "db", "gateway", "records")

S = setup.SECRETS
COS_BOUND = f"{S}/cos-bound/token"
TASKS_COS = f"{S}/cos-partnership/tasks-cos.token"
TASKS_PLATFORM = f"{S}/cos-partnership/tasks-platform.token"
PORTAL_MC = f"{S}/portal-partnership/mc.token"
# The platform seat's token, held beside MC's: the portal's "build it" records its source event with it (mc_tools_wiring.tasks_adapter).
PORTAL_PLATFORM = f"{S}/portal-partnership/platform.token"
DRAINER = f"{S}/partnership/drainer.token"
NOTICES = f"{S}/partnership/notices.token"
BINDINGS = f"{S}/partnership/bindings.json"
GRANTS = f"{S}/partnership/grants.json"
RUNNER_SEATS = f"{S}/partnership/runner-seats.json"
DB_URL = f"{S}/partnership/db.url"
BOARD_DB_URL = f"{S}/cos-board/db.url"
BOARD_PROFILE = f"{S}/cos-partnership/board-profile.json"
BOARD_KEYS = f"{S}/cos-board/profiles.json"
COS_SERVICE = f"{S}/cos-guildhall/cos-service.token"
COS_SERVICE_COPY = f"{S}/guildhall-cos/cos-service.token"
MC_SERVICE = f"{S}/guildhall-mc/mc-service.token"
# The MC worker reads this file at start, then uses the portal path (ROOMS_MC_PORTAL=1, services/rooms_worker/worker.py), so
# it is a new random value that opens nothing. Production's real relay token isn't copied into a second place.
MC_RELAY = f"{S}/guildhall-mc/mc-relay.token"
STORE_JOURNAL = f"{setup.STATE}/partnership/store/journal/journal.jsonl"     # http_api refuses to start without it

# Which token each seat holds. Seats themselves are Robert's: the input file names what each may do.
SEAT_TOKENS = {"cos": TASKS_COS, "platform": TASKS_PLATFORM, "mc": PORTAL_MC, "outbox-drainer": DRAINER}
OWNER = "robert"       # docker-compose.minimoi-1-2-production.yml pins BOARD_API_OWNER and COS_OWNER_PRINCIPAL to this
INPUT_SCHEMA = "minimoi.1-2-prod-bind.inputs/1"
INPUT_FIELDS = {"schema", "environment", "approved_by", "owner_principal", "board", "seats", "grants", "runner_seats"}
SCOPE = re.compile(r"^(personal|shared:[a-z0-9][a-z0-9-]{0,40})$")
SEAT = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
# contracts/partnership/v1/command.schema.json; a test holds them equal.
COMMAND_KINDS = frozenset((
    "source_event.record", "interpretation.record", "interpretation.dispose", "request.record", "request.refine",
    "request.answer", "request.set_state", "request.promote", "request.link_source", "task.transition", "task.assign",
    "task.accept", "task.reopen", "correction.apply", "task.request_admission", "outbox.record", "outbox.approve",
    "task.adopt_interpretation", "task.submit_artifacts", "task.record_review", "task.delegate", "task.lead_unavailable"))
# domains/cos/board_tool.py OPS; a test holds them equal.
BOARD_CAPABILITIES = ("guild.board.notes.read", "guild.board.note.create", "guild.board.note.update",
                      "guild.board.operations.read", "guild.board.operation.undo", "guild.board.capabilities.read")

KEY_CAP = 20.0
KEY_DURATION = "30d"
CHAT_ROUTES = ["/v1/chat/completions", "/chat/completions"]
VOICE_MODELS = ["minimoi-voice-summary", "minimoi-voice-intent"]     # scripts/staging/mc_keys.sh
GATEWAY_KEYS = {
    f"{S}/guildhall-cos/discussion-gateway.token": {"models": ["minimoi-cos-agent"], "rpm": 10, "alias": "guildhall-discussion-cos-"},
    f"{S}/guildhall-mc/discussion-gateway.token": {"models": ["minimoi-mc-agent"], "rpm": 10, "alias": "guildhall-discussion-mc-"},
    # The portal and cos-scheduler read these through <variable>_FILE (core/realtime_voice/summary_gateway.py), from their
    # own mounted folder; the compose layer names both. Without the files the summary reads "unavailable".
    f"{S}/cos-partnership/voice-summary-cos.key": {"models": VOICE_MODELS, "rpm": 60, "alias": "voice-summary-cos-"},
    f"{S}/portal-partnership/voice-summary-mc.key": {"models": VOICE_MODELS, "rpm": 60, "alias": "voice-summary-mc-"},
}
KEY_FORM = re.compile(r"^sk-[A-Za-z0-9_-]{16,200}$")

DB_ROLE = "partnership_worker"       # services/partnership_tasks/worker.py
DB_NAME = "personal_agents"
DB_HOST = "postgres"                 # the worker and the Board sit on the main network, where the service answers to this name
# What the worker's own path needs: DispatcherStore (admit, lookup, fence, stale, revoke), GrantStore and the runner door.
# No DELETE, nothing outside these. A test holds this list to the tables those modules name.
WORKER_TABLES = {
    "guild.mc_delegations": "SELECT, INSERT, UPDATE",
    "guild.mc_delegation_events": "SELECT, INSERT",
    "guild.mc_delegation_runners": "SELECT",
    "guild.mc_delegation_reservations": "SELECT, INSERT, UPDATE",
    "guild.mc_task_fences": "SELECT, INSERT, UPDATE",
    "guild.mc_task_operations": "SELECT, INSERT, UPDATE",
    "guild.mc_action_grants": "SELECT, INSERT, UPDATE",
    "guild.mc_action_grant_consumptions": "SELECT, INSERT",
    "guild.mc_action_receipts": "SELECT",
    "guild.supervision_decisions": "SELECT",
}
OPTIONAL_TABLES = frozenset({"guild.supervision_decisions"})     # read on one path only; granted when it exists

BOARD_DB_ROLE = "cos_board"          # minimoi_portal/guild_ui/board_headless_runner.py
# What the Board API's own path does: board_headless -> BoardActions.{query,read,create,update,result,undo} on FloorStores.
# The session advisory lock needs no grant. Undo bins a note, it never deletes, so no DELETE. Not here because that path never
# reaches them: floor_requests (the browser's ui_write only), floor_messages, floor_continue, media.*. domains/guild/queue_store.py
# is a file, not a table, and the layer sets no BOARD_API_QUEUE_FILE. A test runs the path and holds this list to it.
BOARD_TABLES = {
    "guild.floor_postits": "SELECT, INSERT, UPDATE",
    "guild.floor_board_meta": "SELECT, INSERT, UPDATE",
    "guild.board_operations": "SELECT, INSERT",
}
# A BIGSERIAL default needs USAGE on its sequence. The identity columns on the worker's tables need none.
BOARD_SEQUENCES = ("guild.floor_postits_id_seq",)
DB_ROLES = (
    {"url_file": DB_URL, "role": DB_ROLE, "tables": WORKER_TABLES, "optional": OPTIONAL_TABLES, "sequences": ()},
    {"url_file": BOARD_DB_URL, "role": BOARD_DB_ROLE, "tables": BOARD_TABLES, "optional": frozenset(), "sequences": BOARD_SEQUENCES},
)

RECORDS_TEAMMATES = {
    "cos": {"label": "Chief of Staff", "worker": "rooms-worker-cos", "teammate_file": f"{S}/guildhall-cos/cos.token",
            "worker_file": f"{S}/guildhall-cos/rooms-worker-cos.token", "observe_file": f"{S}/cos-guildhall/meeting-observe.token"},
    "mc": {"label": "Master Craftsman", "worker": "rooms-worker", "teammate_file": f"{S}/guildhall-mc/mc.token",
           "worker_file": f"{S}/guildhall-mc/rooms-worker.token", "observe_file": f"{S}/mc-rooms-work/work.token"},
}
RECORDS_DAYS = 365      # Records' tokens end at this age; a re-issue is a deliberate rotation


def refuse(why: str):
    raise SystemExit(f"refused: {why}")


# ---- files ------------------------------------------------------------------------------------------------------------

_fchown = os.fchown      # tests replace this: only root can give a file to a container's uid


def _sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_new(root: Path, rel: str, data: bytes, owner=None) -> bool:
    """True when written, False when the file was already there (kept as it is). Atomic, and link() can't overwrite."""
    if setup._linked(root, rel):
        refuse(f"a symlink on the way to {rel}")
    path = root / rel
    if os.path.lexists(path):
        return False
    if not path.parent.is_dir():
        refuse(f"the folder for {rel} is missing: run minimoi_1_2_prod_setup.py apply first")
    uid, gid = owner if owner is not None else setup.consumer(rel)
    temp = path.parent / f".{path.name}.{secrets.token_hex(6)}"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            try:
                _fchown(fd, uid, gid)
            except OSError:
                refuse(f"could not give {rel} to uid {uid} gid {gid}")
            os.fchmod(fd, 0o600)
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.link(temp, path)
        except FileExistsError:
            return False
    finally:
        os.unlink(temp)
    _sync_dir(path.parent)
    return True


def read_private(root: Path, rel: str) -> str:
    """One private file's text, stripped. The caller keeps it to itself."""
    if setup._linked(root, rel):
        refuse(f"a symlink on the way to {rel}")
    try:
        fd = os.open(root / rel, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        refuse(f"{rel} is missing or unreadable")
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            refuse(f"{rel} is not a private regular file")
        with os.fdopen(os.dup(fd), "r", encoding="utf-8") as handle:
            return handle.read().strip()
    finally:
        os.close(fd)


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_token() -> bytes:
    return secrets.token_urlsafe(48).encode("ascii")      # 64 characters; the plugin takes [A-Za-z0-9_-]{32,256}


class Outcome:
    def __init__(self):
        self.written, self.kept = [], []

    def note(self, rel: str, wrote: bool) -> None:
        (self.written if wrote else self.kept).append(rel)

    def as_dict(self) -> dict:
        return {"written": sorted(self.written), "kept": sorted(self.kept)}


# ---- inputs -----------------------------------------------------------------------------------------------------------

def template() -> dict:
    """Read from the code that holds each seat: every value still needs Robert's review, then his name in approved_by."""
    personal = {"scopes": ["personal"], "read_scopes": ["personal"]}
    return {
        "schema": INPUT_SCHEMA, "environment": "prod", "approved_by": "", "owner_principal": OWNER,
        "board": {"numeric_user_id": None},
        "seats": {
            "cos": {"origin": "agent", **personal, "operations": ["interpretation.record"]},
            "platform": {"origin": "platform", **personal, "operations": ["source_event.record"]},
            "mc": {"origin": "agent", **personal, "operations": [
                "task.delegate", "task.assign", "task.request_admission", "task.transition", "task.submit_artifacts",
                "task.accept", "task.adopt_interpretation", "interpretation.record", "interpretation.dispose", "request.record", "request.promote", "task.record_review"]},
            "outbox-drainer": {"origin": "platform", **personal, "operations": ["outbox.record"]},
        },
        "grants": [],
        "runner_seats": [],
    }


def _no_duplicates(rows):
    out = {}
    for key, value in rows:
        if key in out:
            refuse("the input file repeats a field")
        out[key] = value
    return out


def validate_inputs(doc) -> dict:
    """Fails closed, naming the field and never its value."""
    if not isinstance(doc, dict) or set(doc) != INPUT_FIELDS:
        refuse(f"the input file must hold exactly: {sorted(INPUT_FIELDS)}")
    if doc["schema"] != INPUT_SCHEMA:
        refuse("input schema")
    if doc["environment"] != "prod":
        refuse("input environment: this writes production's files only, nothing from another host")
    if not isinstance(doc["approved_by"], str) or not SEAT.fullmatch(doc["approved_by"]):
        refuse("input approved_by: a person reviews this file and names themselves")
    if doc["owner_principal"] != OWNER:
        refuse("input owner_principal: the compose layer pins the owner, so this must match it")
    board = doc["board"]
    if not isinstance(board, dict) or set(board) != {"numeric_user_id"}:
        refuse("input board")
    uid = board["numeric_user_id"]
    if not (uid is None or (type(uid) is int and uid > 0)):
        refuse("input board.numeric_user_id")
    seats = doc["seats"]
    if not isinstance(seats, dict) or set(seats) != set(SEAT_TOKENS):
        refuse(f"input seats: exactly {sorted(SEAT_TOKENS)}")
    for name, row in seats.items():
        if not isinstance(row, dict) or set(row) != {"origin", "scopes", "read_scopes", "operations"}:
            refuse(f"input seat {name}")
        if row["origin"] == "owner_session":
            refuse(f"input seat {name}: no token may carry owner authority")
        if row["origin"] not in ("agent", "platform") or (name == "outbox-drainer" and row["origin"] != "platform"):
            refuse(f"input seat {name}: origin")
        for field in ("scopes", "read_scopes"):
            values = row[field]
            if not isinstance(values, list) or not values or not all(isinstance(v, str) and SCOPE.fullmatch(v) for v in values):
                refuse(f"input seat {name}: {field}")
        if not set(row["scopes"]) <= set(row["read_scopes"]):
            refuse(f"input seat {name}: it writes a scope it cannot read")
        ops = row["operations"]
        if not isinstance(ops, list) or not ops or len(set(ops)) != len(ops) or not set(ops) <= COMMAND_KINDS:
            refuse(f"input seat {name}: operations")
    grants = doc["grants"]
    if not isinstance(grants, list):
        refuse("input grants")
    for row in grants:
        if (not isinstance(row, dict) or set(row) != {"grant_id", "seats", "scopes", "expires_at"}
                or not isinstance(row["grant_id"], str) or not row["grant_id"]
                or not isinstance(row["seats"], list) or not set(row["seats"]) <= set(SEAT_TOKENS)
                or not isinstance(row["scopes"], list) or not all(isinstance(v, str) and SCOPE.fullmatch(v) for v in row["scopes"])
                or not (row["expires_at"] is None or isinstance(row["expires_at"], str))):
            refuse("input grants: a row")
    if doc["runner_seats"] != []:
        refuse("input runner_seats: runner gates stay closed, so production's runner seats stay empty")
    return doc


def load_inputs(path: str) -> dict:
    if not path:
        refuse("this group needs --inputs")
    file = Path(path)
    if setup._lexical_symlink(file):
        refuse("a symlink in the input file's path")
    try:
        fd = os.open(file, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        refuse("the input file is missing or unreadable")
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_nlink != 1
                or info.st_uid not in (0, os.geteuid()) or info.st_size > 65536):
            refuse("the input file must be a private regular file of its owner, 0600")
        with os.fdopen(os.dup(fd), "r", encoding="utf-8") as handle:
            try:
                doc = json.load(handle, object_pairs_hook=_no_duplicates)
            except ValueError:
                refuse("the input file is not valid JSON")
    finally:
        os.close(fd)
    return validate_inputs(doc)


# ---- .env: hashes only ------------------------------------------------------------------------------------------------

def _env_lines(root: Path):
    path = root / ".env"
    if path.is_symlink() or not path.is_file():
        refuse(".env must be a regular file on this host")
    return path, path.read_text(encoding="utf-8").splitlines()


def _env_value(lines, name):
    prefix = name + "="
    found = [line[len(prefix):] for line in lines if line.startswith(prefix)]
    return found[-1] if found else None


def _merged_notice_tokens(current, digest):
    """The existing entries, kept, plus partnership's. A different partnership hash is refused."""
    rows = [part.strip() for part in (current or "").split(",") if part.strip()]
    mine = [row for row in rows if row.startswith("partnership:")]
    if mine and mine != [f"partnership:{digest}"]:
        refuse(".env: MINIMOI_ACTION_NOTICES_AGENT_TOKENS names a different partnership hash")
    return ",".join(rows if mine else [*rows, f"partnership:{digest}"])


def set_env_hashes(root: Path, wanted: dict, notices_digest: str, out: Outcome) -> None:
    """Adds what is missing. An existing value that differs is refused: nothing here overwrites."""
    path, lines = _env_lines(root)
    add = {}
    for name, value in wanted.items():
        have = _env_value(lines, name)
        if have is None or have == "":
            add[name] = value
        elif have != value:
            refuse(f".env: {name} already holds a different value")
    tokens = _merged_notice_tokens(_env_value(lines, "MINIMOI_ACTION_NOTICES_AGENT_TOKENS"), notices_digest)
    if tokens != _env_value(lines, "MINIMOI_ACTION_NOTICES_AGENT_TOKENS"):
        add["MINIMOI_ACTION_NOTICES_AGENT_TOKENS"] = tokens
    if not add:
        out.kept.append(".env")
        return
    kept = [line for line in lines if not any(line.startswith(name + "=") for name in add)]
    info = path.stat()
    backup = path.with_name(f".env.bak-bind-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}")
    shutil.copy2(path, backup)
    os.chmod(backup, stat.S_IMODE(info.st_mode))
    temp = path.with_name(f".env.bind.{secrets.token_hex(6)}")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join([*kept, *(f"{k}={v}" for k, v in add.items())]) + "\n")
            handle.flush()
            try:
                _fchown(handle.fileno(), info.st_uid, info.st_gid)
            except OSError:
                refuse("could not keep .env's owner")
            os.fchmod(handle.fileno(), stat.S_IMODE(info.st_mode))
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            os.unlink(temp)
    out.written.append(".env: " + ", ".join(sorted(add)))


# ---- group: local -----------------------------------------------------------------------------------------------------

def _setup_ready(root: Path) -> None:
    state = setup.report(root)
    unsafe = [rel for group in ("folders", "generated", "by_operator") for rel, v in state[group].items() if v == "unsafe"]
    if unsafe:
        refuse(f"unsafe paths, nothing changed: {sorted(unsafe)}")
    missing = [rel for group in ("folders", "generated") for rel, v in state[group].items() if v != "present"]
    if missing:
        refuse(f"run minimoi_1_2_prod_setup.py apply first; missing: {sorted(missing)}")


BOARD_PAIR_FIELDS = ("key_id", "principal", "actor", "environment", "domain", "secret_hex", "numeric_user_id")


def _board_pair_problem(root: Path) -> str:
    """Names the fields that differ between the Board's two sides, never their values. '' when they agree."""
    try:
        rows = json.loads(read_private(root, BOARD_KEYS))["profiles"]
        profile = json.loads(read_private(root, BOARD_PROFILE))
    except (OSError, ValueError, KeyError, TypeError):
        return "unreadable"
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict) or not isinstance(profile, dict):
        return "not one profile"
    row = rows[0]
    fields = [name for name in BOARD_PAIR_FIELDS if name not in row or name not in profile or row[name] != profile[name]]
    if row.get("environment") != "prod" and "environment" not in fields:
        fields.append("environment")
    if set(row.get("capabilities") or ()) != set(BOARD_CAPABILITIES):
        fields.append("capabilities")
    return ", ".join(fields)


def _board_files(root: Path, doc: dict, out: Outcome) -> None:
    have = [os.path.lexists(root / rel) for rel in (BOARD_PROFILE, BOARD_KEYS)]
    if all(have):
        problem = _board_pair_problem(root)
        if problem:
            refuse(f"the Board key pair does not agree, nothing changed: {problem}")
        out.kept += [BOARD_PROFILE, BOARD_KEYS]
        return
    if any(have):
        refuse("the Board key is on one side only: the pair must be written together")
    secret_hex = secrets.token_hex(32)
    key_id = "cos-board-prod-" + secrets.token_hex(4)
    uid = doc["board"]["numeric_user_id"]
    profile = {"actor": "cos", "domain": "guild", "board": "cos-board", "environment": "prod", "key_id": key_id,
               "principal": doc["owner_principal"], "secret_hex": secret_hex, "numeric_user_id": uid}
    row = {"key_id": key_id, "principal": doc["owner_principal"], "actor": "cos", "environment": "prod", "domain": "guild",
           "capabilities": list(BOARD_CAPABILITIES), "secret_hex": secret_hex, "numeric_user_id": uid}
    out.note(BOARD_KEYS, write_new(root, BOARD_KEYS, json.dumps({"profiles": [row]}, indent=1).encode()))
    out.note(BOARD_PROFILE, write_new(root, BOARD_PROFILE, json.dumps(profile, indent=1).encode()))


def seat_bindings(root: Path, doc: dict) -> list:
    return [{"binding_id": f"bind-prod-{seat}","token_sha256": sha256(read_private(root, rel)), "seat": seat,
             "principal": doc["owner_principal"], "environment": "prod", "origin": doc["seats"][seat]["origin"],
             "scopes": doc["seats"][seat]["scopes"], "read_scopes": doc["seats"][seat]["read_scopes"],
             "operations": doc["seats"][seat]["operations"], "expires_at": None, "revoked_at": None}
            for seat, rel in SEAT_TOKENS.items()]


def _seats_match(root: Path) -> dict:
    """{seat: True/False} from the bindings file, comparing hashes only."""
    try:
        rows = json.loads(read_private(root, BINDINGS))
    except ValueError:
        refuse(f"{BINDINGS} is not valid JSON")
    by_seat = {row.get("seat"): row.get("token_sha256") for row in rows}
    return {seat: os.path.lexists(root / rel) and by_seat.get(seat) == sha256(read_private(root, rel))
            for seat, rel in SEAT_TOKENS.items()}


def _task_store(root: Path, out: Outcome) -> None:
    journal = root / STORE_JOURNAL
    if setup._linked(root, STORE_JOURNAL):
        refuse(f"a symlink on the way to {STORE_JOURNAL}")
    # A blank journal is only right for a new store. Over existing records it would make them unaccounted for.
    store = journal.parent.parent
    if not os.path.lexists(journal) and store.exists():
        held = [p for p in store.rglob("*") if not p.is_dir()]
        if held:
            refuse(f"the task store holds {len(held)} file(s) but no journal: not a new store, nothing changed")
    for folder in (journal.parent.parent, journal.parent):
        if not folder.exists():
            folder.mkdir(mode=0o700)
            fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
            try:
                _fchown(fd, *setup.ROOT)
            except OSError:
                refuse(f"could not give {folder.name} to root")
            finally:
                os.close(fd)
    out.note(STORE_JOURNAL, write_new(root, STORE_JOURNAL, b"", owner=setup.ROOT))


def group_local(root: Path, doc: dict, out: Outcome) -> None:
    _setup_ready(root)
    for rel in (COS_BOUND, TASKS_COS, TASKS_PLATFORM, PORTAL_MC, MC_RELAY):
        out.note(rel, write_new(root, rel, new_token()))
    out.note(PORTAL_PLATFORM, write_new(root, PORTAL_PLATFORM, read_private(root, TASKS_PLATFORM).encode()))
    if read_private(root, PORTAL_PLATFORM) != read_private(root, TASKS_PLATFORM):
        refuse(f"{PORTAL_PLATFORM} differs from {TASKS_PLATFORM}")
    out.note(COS_SERVICE_COPY, write_new(root, COS_SERVICE_COPY, read_private(root, COS_SERVICE).encode()))
    if read_private(root, COS_SERVICE_COPY) != read_private(root, COS_SERVICE):
        refuse(f"{COS_SERVICE_COPY} differs from {COS_SERVICE}")
    _board_files(root, doc, out)
    if os.path.lexists(root / BINDINGS):
        out.kept.append(BINDINGS)
        wrong = [seat for seat, same in _seats_match(root).items() if not same]
        if wrong:
            refuse(f"{BINDINGS} doesn't match the tokens of: {wrong}")
    else:
        out.note(BINDINGS, write_new(root, BINDINGS, json.dumps(seat_bindings(root, doc), indent=1).encode()))
    out.note(GRANTS, write_new(root, GRANTS, json.dumps(doc["grants"], indent=1).encode()))
    out.note(RUNNER_SEATS, write_new(root, RUNNER_SEATS, b"{}\n"))
    _task_store(root, out)
    set_env_hashes(root, {
        "MINIMOI_COS_TOOL_LISTENER_TOKEN_SHA256": sha256(read_private(root, COS_BOUND)),
        "MINIMOI_MC_ROOMS_TOKEN_SHA256": sha256(read_private(root, MC_SERVICE)),
        "MINIMOI_PARTNERSHIP_OWNER_SUBJECT": f"owner:{doc['owner_principal']}",     # the portal signs owner:<id>
    }, sha256(read_private(root, NOTICES)), out)


# ---- docker -----------------------------------------------------------------------------------------------------------

DOCKER_TARGET_VARS = ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH")


def docker(*args, stdin=None, timeout=300):
    """Output comes back to this process only, and is never echoed. Values go by stdin, never argv."""
    # This host's own daemon only: a remote target would write credentials here and change services there.
    env = {k: v for k, v in os.environ.items() if k not in DOCKER_TARGET_VARS}
    return subprocess.run([DOCKER, "--context", "default", *args], input=stdin, capture_output=True, text=True, timeout=timeout,
                          check=False, env=env)


def _running(container: str) -> bool:
    done = docker("inspect", "--format", "{{.State.Running}}", container, timeout=30)
    return done.returncode == 0 and done.stdout.strip() == "true"


# ---- group: db --------------------------------------------------------------------------------------------------------

def scram_verifier(password: str, salt: bytes = None, iterations: int = 4096) -> str:
    """Postgres stores this as it is, so the password itself never reaches the server or its statement log."""
    salt = salt if salt is not None else secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    stored = hashlib.sha256(hmac.new(salted, b"Client Key", "sha256").digest()).digest()
    server = hmac.new(salted, b"Server Key", "sha256").digest()
    b64 = lambda raw: base64.b64encode(raw).decode("ascii")     # noqa: E731
    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored)}:{b64(server)}"


def role_sql(spec: dict, verifier: str, tables) -> str:
    role = spec["role"]
    grants = [f"GRANT {spec['tables'][t]} ON {t} TO {role};" for t in sorted(tables)]
    grants += [f"GRANT USAGE ON SEQUENCE {s} TO {role};" for s in spec["sequences"]]
    return (f"BEGIN;\nCREATE ROLE {role} LOGIN PASSWORD '{verifier}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
            f"NOBYPASSRLS NOINHERIT CONNECTION LIMIT 8;\nGRANT CONNECT ON DATABASE {DB_NAME} TO {role};\n"
            f"GRANT USAGE ON SCHEMA guild TO {role};\n" + "\n".join(grants) + "\nCOMMIT;\n")


def _psql_read(sql: str) -> str:
    done = docker("exec", "-i", POSTGRES, "psql", "-U", "postgres", "-d", DB_NAME, "-At", "-v", "ON_ERROR_STOP=1", "-f", "-",
                  stdin=sql, timeout=60)
    if done.returncode != 0:
        refuse(f"could not read the database (psql exit {done.returncode})")
    return done.stdout


def _make_role(root: Path, spec: dict, found: set, out: Outcome) -> None:
    role, rel = spec["role"], spec["url_file"]
    password = secrets.token_hex(32)
    url = f"postgresql://{role}:{password}@{DB_HOST}:5432/{DB_NAME}\n".encode("ascii")
    # Role first, then the file: a failed write leaves a role nobody can log in as, and the check before it stops a blind retry.
    done = docker("exec", "-i", POSTGRES, "psql", "-U", "postgres", "-d", DB_NAME, "-q", "-v", "ON_ERROR_STOP=1", "-f", "-",
                  stdin=role_sql(spec, scram_verifier(password), found & set(spec["tables"])), timeout=60)
    if done.returncode != 0:
        refuse(f"the role {role} was not made (psql exit {done.returncode}); no file was written for it")
    out.note(rel, write_new(root, rel, url))
    out.written.append(f"database role {role}")


def group_db(root: Path, out: Outcome) -> None:
    todo = []
    for spec in DB_ROLES:
        rel = spec["url_file"]
        if setup.status(root, rel, directory=False) == "present":
            out.kept.append(rel)
        elif os.path.lexists(root / rel):
            refuse(f"{rel} is unsafe")
        else:
            todo.append(spec)
    if not todo:
        return
    if not _running(POSTGRES):
        refuse(f"{POSTGRES} is not running")
    # Every check comes first, for every role: a missing table leaves no role half the group made.
    wanted = sorted({name for spec in todo for name in (*spec["tables"], *spec["sequences"])})
    found = set(_psql_read("\n".join(f"SELECT '{t}' WHERE to_regclass('{t}') IS NOT NULL;" for t in wanted)).split())
    absent = sorted({name for spec in todo for name in (*spec["tables"], *spec["sequences"])
                     if name not in found and name not in spec["optional"]})
    if absent:
        refuse(f"nothing changed; these tables don't exist yet, run the migrations first: {absent}")
    for spec in todo:
        role = spec["role"]
        if _psql_read(f"SELECT 1 FROM pg_roles WHERE rolname='{role}';").strip():
            refuse(f"role {role} exists but {spec['url_file']} doesn't: its password is unknown. Drop the role on purpose, then run again")
    for spec in todo:
        _make_role(root, spec, found, out)


# ---- group: gateway ---------------------------------------------------------------------------------------------------

def keygen_program(models, rpm: int, alias: str) -> str:
    """Runs inside the gateway on stdin, with the master key from the gateway's own environment. It makes one key, reads its
    budget back with the master key, and deletes the key unless that budget is the cap. Prints the key, then its facts."""
    return f"""
import json, os, sys, urllib.request as u
base = "http://127.0.0.1:4000"
master = os.environ["LITELLM_MASTER_KEY"]
headers = {{"Authorization": "Bearer " + master, "Content-Type": "application/json"}}
cap, duration, models = {KEY_CAP!r}, {KEY_DURATION!r}, {json.dumps(sorted(models))}
def post(path, body):
    req = u.Request(base + path, data=json.dumps(body).encode(), method="POST", headers=headers)
    with u.urlopen(req, timeout=60) as r:
        return json.loads(r.read())
made = post("/key/generate", {{"models": models, "allowed_routes": {json.dumps(CHAT_ROUTES)}, "max_budget": cap,
                              "budget_duration": duration, "rpm_limit": {int(rpm)}, "key_alias": {alias!r} + os.urandom(3).hex(),
                              "metadata": {{"caller": "minimoi_1_2_prod_bind"}}}})
key = made["key"]
def discard():
    try:
        post("/key/delete", {{"keys": [key]}})
    except Exception:
        print("the key could not be deleted", file=sys.stderr)
try:
    # By the key's hashed id: a query string reaches the gateway's access log, so the key itself never goes in one.
    req = u.Request(base + "/key/info?key=" + made["token"], headers=headers)
    with u.urlopen(req, timeout=60) as r:
        info = json.loads(r.read())["info"]
    facts = {{"max_budget": info.get("max_budget"), "budget_duration": info.get("budget_duration"), "models": sorted(info.get("models") or []),
             "key_alias": info.get("key_alias")}}
except Exception:
    discard()
    sys.exit(4)
if key == master or facts["max_budget"] != cap or facts["budget_duration"] != duration or facts["models"] != models:
    discard()
    sys.exit(3)
print(key)
print(json.dumps(facts))
"""


def mint_key(rel: str, spec: dict) -> bytes:
    done = docker("exec", "-i", GATEWAY, "python3", "-", stdin=keygen_program(spec["models"], spec["rpm"], spec["alias"]), timeout=120)
    if done.returncode != 0:
        refuse(f"the gateway did not make a capped key for {rel} (exit {done.returncode}); nothing written for it")
    lines = done.stdout.splitlines()
    if len(lines) != 2 or not KEY_FORM.fullmatch(lines[0]):
        refuse(f"the gateway's answer for {rel} was not a key; nothing written for it")
    try:
        facts = json.loads(lines[1])
    except ValueError:
        refuse(f"the gateway's answer for {rel} had no budget; nothing written for it")
    if (facts.get("max_budget") != KEY_CAP or facts.get("budget_duration") != KEY_DURATION
            or facts.get("models") != sorted(spec["models"])):
        refuse(f"the key for {rel} does not read back as capped; nothing written for it")
    return lines[0].encode("ascii")


def group_gateway(root: Path, out: Outcome) -> None:
    todo = [rel for rel in GATEWAY_KEYS if not os.path.lexists(root / rel)]
    out.kept += [rel for rel in GATEWAY_KEYS if rel not in todo]
    if not todo:
        return
    done = docker("inspect", "-f", "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}", GATEWAY, timeout=30)
    if done.returncode != 0 or done.stdout.strip() != "healthy":
        refuse("the model gateway is not healthy; no key was made")
    for rel in todo:
        if setup._linked(root, rel) or not (root / rel).parent.is_dir():
            refuse(f"the folder for {rel} is not ready: run minimoi_1_2_prod_setup.py apply first")
    for rel in todo:
        out.note(rel, write_new(root, rel, mint_key(rel, GATEWAY_KEYS[rel])))


# ---- group: records ---------------------------------------------------------------------------------------------------

def observe_program(worker: str, installation: str) -> str:
    """Runs inside Records on stdin: a heartbeat-only credential on the worker's installation. Prints the token only."""
    return f"""
import sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, "/app")
from domains.records.store import Store
expires = (datetime.now(timezone.utc) + timedelta(days={RECORDS_DAYS})).isoformat()
done = Store({RECORDS_DATA!r}).platform_access.issue("robert", dict(principal={worker!r}, label="Rooms observe {worker}",
    expires_at=expires, scope="observe", installation_id={installation!r}))
print(done["access_token"])
"""


def _records_state(binding: str) -> Path:
    try:
        doc = json.loads(Path(binding).read_text("utf-8"))
        state = doc["inputs"]["state"]
    except (OSError, ValueError, KeyError, TypeError):
        refuse(f"cannot read Records' state folder from {binding}")
    path = Path(state)
    if not path.is_absolute() or setup._lexical_symlink(path) or not path.is_dir():
        refuse("Records' state folder is not a plain existing folder")
    return path


def group_records(root: Path, binding: str, out: Outcome) -> None:
    state = None
    for name, spec in RECORDS_TEAMMATES.items():
        files = [spec["teammate_file"], spec["worker_file"], spec["observe_file"]]
        have = [os.path.lexists(root / rel) for rel in files]
        if all(have):
            out.kept += files
            continue
        if any(have):
            refuse(f"{name}'s Records tokens are partly there; revoke them in Records and remove the files first")
        if state is None:
            if not _running(RECORDS):
                refuse(f"{RECORDS} is not running")
            state = _records_state(binding)
        outbox = state / f".bind-outbox-{name}"
        if os.path.lexists(outbox):
            refuse(f"an earlier run left {outbox.name} in Records' state folder; inspect it first")
        done = docker("exec", RECORDS, "python", "-m", "domains.records.manage", "provision-rooms", "--data-dir", RECORDS_DATA,
                      "--out", f"{RECORDS_DATA}/.bind-outbox-{name}", "--teammate", name, "--label", spec["label"],
                      "--worker-principal", spec["worker"], "--days", str(RECORDS_DAYS))
        if done.returncode != 0:
            refuse(f"Records did not provision {name} (exit {done.returncode}); nothing written for it")
        try:
            installation = json.loads(done.stdout)["hosted"]["installation_id"]
        except (ValueError, KeyError, TypeError):
            refuse(f"Records' answer for {name} had no installation; inspect {outbox.name}")
        observed = docker("exec", "-i", RECORDS, "python", "-", stdin=observe_program(spec["worker"], installation), timeout=60)
        token = observed.stdout.strip()
        if observed.returncode != 0 or not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
            refuse(f"Records did not issue {name}'s observe credential (exit {observed.returncode}); inspect {outbox.name}")
        out.note(spec["teammate_file"], write_new(root, spec["teammate_file"], (outbox / f"{name}.token").read_bytes()))
        out.note(spec["worker_file"], write_new(root, spec["worker_file"], (outbox / f"{spec['worker']}.token").read_bytes()))
        out.note(spec["observe_file"], write_new(root, spec["observe_file"], token.encode("ascii")))
        for left in (f"{name}.token", f"{spec['worker']}.token"):
            os.unlink(outbox / left)
        os.rmdir(outbox)


# ---- cos-auth (G14) ---------------------------------------------------------------------------------------------------

def cos_auth() -> dict:
    """Which of CoS's agents see one healthy plan profile. OpenClaw treats an OAuth profile as not portable, because its
    refresh token may rotate, so nothing here moves one: an agent reads the shared store, or Robert signs it in."""
    agents, ids = {}, set()
    for agent in COS_AGENTS:
        done = docker("exec", COS_AGENT, "node", "/app/openclaw.mjs", "models", "status", "--agent", agent, "--json", timeout=120)
        if done.returncode != 0:
            agents[agent] = {"state": "unreadable"}
            continue
        try:
            text = done.stdout
            profiles = json.loads(text[text.index("{"):])["auth"]["oauth"]["profiles"]
        except (ValueError, KeyError, TypeError):
            agents[agent] = {"state": "unreadable"}
            continue
        good = [p.get("profileId") for p in profiles if p.get("provider") == "openai" and p.get("type") == "oauth" and p.get("status") == "ok"]
        agents[agent] = {"state": "signed in" if len(good) == 1 else "not signed in", "profile": good[0] if len(good) == 1 else None}
        ids.update(good if len(good) == 1 else [])
    ok = all(row["state"] == "signed in" for row in agents.values()) and len(ids) == 1
    lacking = [agent for agent, row in agents.items() if row["state"] != "signed in"]
    return {"ok": ok, "agents": agents, "same_profile": len(ids) == 1,
            "robert_runs": [f"sudo docker exec -it {COS_AGENT} node /app/openclaw.mjs models auth login --agent {agent} "
                            "--provider openai --device-code" for agent in lacking]}


# ---- check ------------------------------------------------------------------------------------------------------------

def check(root: Path, inputs: str = "") -> dict:
    files = {rel: setup.status(root, rel, directory=False) for rel in setup.BY_OPERATOR}
    files[STORE_JOURNAL] = _plain_status(root, STORE_JOURNAL)
    agree, env_lines = {}, []
    if (root / ".env").is_file() and not (root / ".env").is_symlink():
        env_lines = _env_lines(root)[1]

    def same_hash(name, rel, variable):
        if setup.status(root, rel, directory=False) == "present":
            agree[name] = _env_value(env_lines, variable) == sha256(read_private(root, rel))

    if files[COS_SERVICE_COPY] == "present" and setup.status(root, COS_SERVICE, directory=False) == "present":
        agree["cos-service pair"] = read_private(root, COS_SERVICE) == read_private(root, COS_SERVICE_COPY)
    same_hash("cos-bound hash in .env", COS_BOUND, "MINIMOI_COS_TOOL_LISTENER_TOKEN_SHA256")
    same_hash("mc-service hash in .env", MC_SERVICE, "MINIMOI_MC_ROOMS_TOKEN_SHA256")
    if setup.status(root, NOTICES, directory=False) == "present":
        wanted = "partnership:" + sha256(read_private(root, NOTICES))
        agree["notices hash in .env"] = wanted in (_env_value(env_lines, "MINIMOI_ACTION_NOTICES_AGENT_TOKENS") or "").split(",")
    if setup.status(root, BINDINGS, directory=False) == "present":
        agree.update({f"seat {seat} matches its token": same for seat, same in _seats_match(root).items()})
    if files[BOARD_KEYS] == files[BOARD_PROFILE] == "present":
        agree["Board key pair"] = not _board_pair_problem(root)
    subject = _env_value(env_lines, "MINIMOI_PARTNERSHIP_OWNER_SUBJECT")
    if subject is not None:
        agree["owner subject form"] = subject == f"owner:{OWNER}"
    report = {"files": files, "agree": agree, "counts": {}}
    for value in files.values():
        report["counts"][value] = report["counts"].get(value, 0) + 1
    report["inputs"] = "not given"
    if inputs:
        load_inputs(inputs)
        report["inputs"] = "valid"
    report["ok"] = "unsafe" not in files.values() and all(agree.values())
    return report


def _plain_status(root: Path, rel: str) -> str:
    path = root / rel
    if not os.path.lexists(path):
        return "missing"
    if setup._linked(root, rel):
        return "unsafe"
    info = path.lstat()
    return "present" if stat.S_ISREG(info.st_mode) and not info.st_mode & 0o077 else "unsafe"


# ---- main -------------------------------------------------------------------------------------------------------------

def apply(root: Path, only, inputs: str, records_binding: str) -> dict:
    if os.environ.get("MINIMOI_1_2_SETUP_APPROVED") != "1":
        refuse("apply needs MINIMOI_1_2_SETUP_APPROVED=1, set by the reviewed operator")
    if setup._lexical_symlink(root):
        refuse("a symlink in the root path")
    if "minimoi-staging" in root.parts:
        refuse("this is production's operator; it never writes to Dev")
    unknown = sorted(set(only) - set(GROUPS))
    if unknown or not only:
        refuse(f"--only takes {list(GROUPS)}")
    out = Outcome()
    doc = load_inputs(inputs) if "local" in only else None
    if not (root / S).is_dir():
        refuse("run minimoi_1_2_prod_setup.py apply first")
    os.umask(0o077)
    with open(root / S / ".bind.lock", "a") as lock:     # one writer at a time
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            refuse("another run holds the lock")
        if "local" in only:
            group_local(root, doc, out)
        if "db" in only:
            group_db(root, out)
        if "gateway" in only:
            group_gateway(root, out)
        if "records" in only:
            group_records(root, records_binding, out)
    return out.as_dict()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", nargs="?", choices=("check", "apply", "cos-auth", "inputs-template"), default="check")
    parser.add_argument("--root", default=str(setup.PRODUCTION))
    parser.add_argument("--inputs", default="")
    parser.add_argument("--only", default="")
    parser.add_argument("--records-binding", default=RECORDS_BINDING)
    args = parser.parse_args(argv)
    root = Path(args.root)
    code = 0
    try:
        if args.mode == "inputs-template":
            result = template()
        elif args.mode == "cos-auth":
            result = cos_auth()
            code = 0 if result["ok"] else 1
        elif args.mode == "apply":
            result = apply(root, [g for g in args.only.split(",") if g], args.inputs, args.records_binding)
        else:
            result = check(root, args.inputs)
            code = 0 if result["ok"] else 1
    except subprocess.TimeoutExpired:
        raise SystemExit("failed: a docker command timed out") from None   # its message would name its stdin
    except OSError as exc:
        raise SystemExit(f"failed: {type(exc).__name__}") from None
    print(json.dumps(result, indent=1, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())

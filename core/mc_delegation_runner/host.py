"""A version 2 host's own configuration, ``host.yaml`` (schema ``minimoi.host.v1``, host runtime spec v0.5 R2.3-a), its
per-(host, environment) lock (G6), its pinned interpreter, and its bundle digest (R2.2-a, G3).

* **The format is JSON**, which is valid YAML 1.2, so the file keeps its spec name and any YAML reader reads it; this
  module reads it with the standard library alone, because the runner's bundle must also run on the Mac's Python 3.9
  without PyYAML (the same choice as the authority's file). Strict: one JSON object of at most 64 KiB, no duplicate
  key, no key outside the schema, every value checked. A refusal names a fixed code, never the file's content.
* **Values that apply to the whole environment** (§5.3: contact interval and timeout, renew interval, offline hold,
  the gap threshold) are not here (R2.3-b): they live in the authority's file (``Authority.hosts``), which
  ``authority.values`` names. A host given none refuses to start: a missing value admits nothing.
* **One instance per (host, environment)** (G6): an exclusive ``flock`` on ``~/minimoi-runtime/locks/<host_id>.
  <environment>.lock``, held for the runtime's life and released by the kernel when it dies. The folder is fixed, never
  set per file, so two configurations of the same host and environment always meet at the same lock (review N8). The
  production runner (version 1, no ``host.yaml``) takes no lock and is unchanged.
* **The pinned interpreter**: its path and ``major.minor`` version. The runtime refuses to start when it runs under any
  other, or when a role it holds needs a newer one. Version 2 at the authority (and a worker that reaches it by DSN,
  which builds the authority's store in-process) needs Python 3.10 or later (``GrantStore``); a worker that reaches it
  by HTTPS does not.
* **do_not_resume** (R6.4) always holds the two billed sessions; a host may add ids, never remove them.
* **Pinned tools** (R6.7-d step 1): ``tools`` maps an actor to ``{path, sha256}``, the one binary the runtime starts
  for it. ``claude-code`` and ``codex`` start only from their pin (``binary_unpinned`` otherwise; never a bare name on
  ``PATH``). ``fakework`` runs the bundle's own ``fakework.py`` unless pinned to a ``fakecli`` build (§9.3, a test
  instrument, refused in ``prod``). Updating a CLI is a change of this file: the new build's path and digest.
* **API-route keys by reference** (R6.7-f): ``route_keys`` maps an API route to a credential reference,
  ``keychain:<service>/<account>`` or, in ``dev`` only, ``test:<name>`` read from ``test_credentials`` (a private file
  of test values made for an acceptance run). A reference, never a value.

Stdlib only; Python 3.9-compatible.
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from core.mc_delegation_dispatcher.door import DOORS as _DOORS     # one allowlist of door URLs, the door's (N4)

SCHEMA = "minimoi.host.v1"
PRESEEDED_DO_NOT_RESUME = ("01a17dde-fb26-7923-8105-c7478428d17d", "8c4e1a20-6f3b-4d9e-a7c1-2b5d9e0f4a11")
ROLES = ("authority", "worker", "witness", "watch:staging")
OFFLINE_SCOPES = ("none", "workspace_only")
V2_MIN_PYTHON = (3, 10)            # core.minimoi_actions (GrantStore) uses slotted dataclasses and X | None at runtime
LOCK_ROOT = "~/minimoi-runtime/locks"        # G6's one fixed folder (per user, as the runtime runs as its user)
MAX_BYTES = 64 * 1024
_ID = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")          # the authority's id rule (no '/': reserved for its own rows)
_HOST = re.compile(r"^[A-Za-z0-9._-]{1,56}$")          # no ':', so the owner identity host_id:runner splits one way
_CAP = re.compile(r"^[a-z][a-z0-9_-]{0,15}:[A-Za-z0-9._:-]{1,64}$")
_SESSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,99}$")
_VERSION = re.compile(r"^3\.([0-9]{1,2})$")
_KEYS = {"schema", "host_id", "environment", "roles", "expected_availability", "unreachable_grace_s", "service_manager",
         "authority", "capabilities", "concurrency", "offline_scope_default", "do_not_resume", "artifact_root",
         "workspace_root", "mirrors", "interpreter", "bundle_root", "gap_detection", "stop_timeout_s",
         "effect_sink", "principal", "note", "tools", "route_keys", "test_credentials"}
_REQUIRED = _KEYS - {"do_not_resume", "gap_detection", "stop_timeout_s", "effect_sink", "note",
                     "service_manager", "offline_scope_default", "tools", "route_keys", "test_credentials"}
TOOL_ACTORS = ("claude-code", "codex", "fakework")


class HostRefused(ValueError):
    """A host configuration, lock or interpreter the runtime will not start with: a fixed code only."""


def _need(ok, code):
    if not ok:
        raise HostRefused(code)


@dataclass(frozen=True)
class Authority:
    endpoint_id: str
    transport: str                     # 'dsn' (the Mac-only interim, R2.4-a) or 'door' (HTTPS, gate D)
    dsn_file: Path | None
    url: str | None
    credential_ref: str | None         # a reference, never a value (G5)
    values: Path                       # the authority's own file (minimoi.authority.v1), read for Authority.hosts


@dataclass(frozen=True)
class Interpreter:
    path: Path
    version: tuple


@dataclass(frozen=True)
class EffectSink:
    """The acceptance effect sink (§9.3), a file sink in S1-D (fakework.FileSink); test environments only."""
    path: Path
    lookup: bool


@dataclass(frozen=True)
class ToolPin:
    """One pinned tool binary (R6.7-d step 1): an absolute path and its 'sha256:<hex>'."""
    path: str
    sha256: str


@dataclass(frozen=True)
class HostConfig:
    host_id: str
    environment: str
    principal: str
    roles: tuple
    expected_availability: str
    unreachable_grace_s: int
    service_manager: str
    authority: Authority
    capabilities: tuple
    concurrency: int
    offline_scope_default: str
    do_not_resume: tuple
    artifact_root: Path
    workspace_root: Path
    mirrors: dict
    interpreter: Interpreter
    bundle_root: Path
    gap_detection: bool
    stop_timeout_s: int
    effect_sink: EffectSink | None
    tools: dict = field(default_factory=dict)            # actor -> ToolPin
    route_keys: dict = field(default_factory=dict)       # API route -> credential reference (never a value)
    test_credentials: Path | None = None                 # dev only: the acceptance run's test credential file
    source: Path | None = None

    @property
    def runner_id(self) -> str:
        """The owner identity (R2.3-a: ``host_id/runner``). The authority's id rule has no '/' (it marks its own rows),
        so the same shape is written with ':'."""
        return f"{self.host_id}:runner"

    @property
    def lock_path(self) -> Path:
        """G6's lock, derived from (host_id, environment) in the one fixed folder (review N8)."""
        return Path(os.path.expanduser(LOCK_ROOT)) / f"{self.host_id}.{self.environment}.lock"

    def actors(self) -> tuple:
        return tuple(c.split(":", 1)[1] for c in self.capabilities if c.startswith("actor:"))

    def scopes(self) -> tuple:
        return tuple(c.split(":", 1)[1] for c in self.capabilities if c.startswith("scope:"))


def _no_duplicates(pairs):
    out = {}
    for k, v in pairs:
        _need(k not in out, "host_config_duplicate_key")
        out[k] = v
    return out


def _path(value, code) -> Path:
    _need(type(value) is str and 0 < len(value) <= 1024 and "\0" not in value, code)
    p = Path(os.path.expanduser(value))
    _need(p.is_absolute(), code)
    return p


def load(path) -> HostConfig:
    path = Path(path)
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_BYTES + 1)
    except OSError:
        raise HostRefused("host_config_unreadable") from None
    _need(len(raw) <= MAX_BYTES, "host_config_too_large")
    try:
        doc = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except HostRefused:
        raise
    except (ValueError, UnicodeDecodeError):
        raise HostRefused("host_config_unreadable") from None
    cfg = parse(doc)
    return HostConfig(**{**cfg.__dict__, "source": path})


def parse(doc) -> HostConfig:
    _need(type(doc) is dict and set(doc) <= _KEYS, "host_config_invalid")
    _need(_REQUIRED <= set(doc), "host_config_value_missing")       # a missing value admits nothing (R2.3-b)
    _need(doc["schema"] == SCHEMA, "host_schema_invalid")
    _need(type(doc.get("note", "")) is str and len(doc.get("note", "")) <= 2000, "host_note_invalid")
    _need(type(doc["host_id"]) is str and _HOST.match(doc["host_id"]), "host_id_invalid")
    _need(doc["environment"] in ("dev", "prod"), "host_environment_invalid")
    _need(type(doc["principal"]) is str and _ID.match(doc["principal"]), "host_principal_invalid")
    roles = doc["roles"]
    _need(type(roles) is list and roles and all(r in ROLES for r in roles) and len(set(roles)) == len(roles),
          "host_roles_invalid")
    _need(doc["expected_availability"] in ("always_on", "intermittent"), "host_availability_invalid")
    grace = doc["unreachable_grace_s"]
    _need(type(grace) is int and 1 <= grace <= 7 * 86400, "host_grace_invalid")
    _need(doc.get("service_manager", "launchd") in ("launchd", "systemd", "container", "none"),
          "host_service_manager_invalid")
    authority = _authority(doc["authority"], doc["environment"])
    caps = doc["capabilities"]
    _need(type(caps) is list and len(caps) <= 64 and all(type(c) is str and _CAP.match(c) for c in caps)
          and len(set(caps)) == len(caps), "host_capabilities_invalid")
    _need(doc["concurrency"] == 1 and type(doc["concurrency"]) is int, "host_concurrency_unsupported")  # serial runner
    scope = doc.get("offline_scope_default", "none")
    _need(scope in OFFLINE_SCOPES, "host_offline_scope_invalid")
    extra = doc.get("do_not_resume", [])
    _need(type(extra) is list and len(extra) <= 256 and all(type(s) is str and _SESSION.match(s) for s in extra),
          "host_do_not_resume_invalid")
    dnr = tuple(dict.fromkeys(list(PRESEEDED_DO_NOT_RESUME) + [s.lower() for s in extra]))   # never fewer than the two
    mirrors = doc["mirrors"]
    _need(type(mirrors) is dict and 1 <= len(mirrors) <= 16 and all(type(k) is str and _ID.match(k) for k in mirrors),
          "host_mirrors_invalid")
    mirrors = {k: _path(v, "host_mirrors_invalid") for k, v in mirrors.items()}
    interp = doc["interpreter"]
    _need(type(interp) is dict and set(interp) == {"path", "version"} and type(interp["version"]) is str
          and _VERSION.match(interp["version"]), "host_interpreter_invalid")
    version = (3, int(interp["version"].split(".")[1]))
    needs = required_python(roles, authority.transport)
    _need(version >= needs, "interpreter_too_old")           # refused at load: a role this pin cannot run
    gap = doc.get("gap_detection", "on")
    _need(gap in ("on", "off"), "host_gap_detection_invalid")
    stop = doc.get("stop_timeout_s", 20)
    _need(type(stop) is int and 2 <= stop <= 600, "host_stop_timeout_invalid")
    sink = doc.get("effect_sink")
    _need(sink is None or (type(sink) is dict and set(sink) == {"path", "lookup"} and type(sink["lookup"]) is bool),
          "host_effect_sink_invalid")
    _need(sink is None or doc["environment"] != "prod", "host_test_instrument_in_production")
    tools, route_keys, test_credentials = _launch_config(doc)
    return HostConfig(
        host_id=doc["host_id"], environment=doc["environment"], principal=doc["principal"], roles=tuple(roles),
        expected_availability=doc["expected_availability"], unreachable_grace_s=grace,
        service_manager=doc.get("service_manager", "launchd"), authority=authority, capabilities=tuple(caps),
        concurrency=1, offline_scope_default=scope, do_not_resume=dnr,
        artifact_root=_path(doc["artifact_root"], "host_artifact_root_invalid"),
        workspace_root=_path(doc["workspace_root"], "host_workspace_root_invalid"), mirrors=mirrors,
        interpreter=Interpreter(_path(interp["path"], "host_interpreter_invalid"), version),
        bundle_root=_path(doc["bundle_root"], "host_bundle_root_invalid"),
        gap_detection=gap == "on", stop_timeout_s=stop,
        effect_sink=None if sink is None else EffectSink(_path(sink["path"], "host_effect_sink_invalid"),
                                                          sink["lookup"]),
        tools=tools, route_keys=route_keys, test_credentials=test_credentials)


def _launch_config(doc) -> tuple:
    """``tools``, ``route_keys`` and ``test_credentials`` (the launch contract's host side), checked."""
    from . import launch, launch_env
    prod = doc["environment"] == "prod"
    raw = doc.get("tools", {})
    _need(type(raw) is dict and set(raw) <= set(TOOL_ACTORS), "host_tools_invalid")
    tools = {}
    for actor, t in raw.items():
        _need(type(t) is dict and set(t) == {"path", "sha256"} and launch.pin(t["sha256"]) is not None,
              "host_tools_invalid")
        tools[actor] = ToolPin(str(_path(t["path"], "host_tools_invalid")), launch.pin(t["sha256"]))
    _need(not (prod and "fakework" in tools), "host_test_instrument_in_production")   # a fakecli pin is a test tool
    keys = doc.get("route_keys", {})
    _need(type(keys) is dict, "host_route_keys_invalid")
    for name, ref in keys.items():
        r = launch_env.ROUTES.get(name)
        _need(r is not None and r["kind"] == "api" and launch.credential_ref(ref) is not None, "host_route_keys_invalid")
        _need(not (prod and (r.get("test") or ref.startswith("test:"))), "host_test_instrument_in_production")
    creds = doc.get("test_credentials")
    _need(creds is None or not prod, "host_test_instrument_in_production")
    creds = None if creds is None else _path(creds, "host_test_credentials_invalid")
    _need(creds is not None or not any(ref.startswith("test:") for ref in keys.values()), "host_test_credentials_missing")
    return tools, dict(keys), creds


def _authority(a, environment) -> Authority:
    _need(type(a) is dict and {"endpoint_id", "values"} <= set(a)
          and set(a) <= {"endpoint_id", "values", "dsn_file", "url", "credential_ref"}, "host_authority_invalid")
    _need(type(a["endpoint_id"]) is str and _ID.match(a["endpoint_id"]), "host_authority_invalid")
    has_dsn, has_url = "dsn_file" in a, "url" in a
    _need(has_dsn != has_url, "host_authority_transport_invalid")         # exactly one way to reach it
    if has_url:
        _need(a["url"] in _DOORS and type(a.get("credential_ref")) is str
              and re.match(r"^keychain:[A-Za-z0-9_.-]{1,64}/[A-Za-z0-9_.-]{1,64}$", a["credential_ref"]),
              "host_authority_door_invalid")
        _need((a["url"] == _DOORS[0]) == (environment == "prod"), "host_authority_environment_mismatch")
    else:
        _need("credential_ref" not in a, "host_authority_invalid")
    return Authority(a["endpoint_id"], "door" if has_url else "dsn",
                     _path(a["dsn_file"], "host_authority_invalid") if has_dsn else None,
                     a.get("url"), a.get("credential_ref"), _path(a["values"], "host_authority_invalid"))


def required_python(roles, transport) -> tuple:
    """The interpreter a role set needs: the authority's code (GrantStore) needs 3.10, in the authority itself and in a
    worker that builds the authority's store in-process over a DSN."""
    if "authority" in roles or transport == "dsn":
        return V2_MIN_PYTHON
    return (3, 9)


def interpreter_problems(cfg: HostConfig, *, executable=None, version=None) -> list:
    """Why the running interpreter is not the pinned one, as fixed codes (empty when it is)."""
    executable = sys.executable if executable is None else executable
    version = tuple(sys.version_info[:2]) if version is None else tuple(version[:2])
    out = []
    if not executable or os.path.realpath(executable) != os.path.realpath(str(cfg.interpreter.path)):
        out.append("interpreter_unpinned")
    if version != cfg.interpreter.version:
        out.append("interpreter_version_mismatch")
    if version < required_python(cfg.roles, cfg.authority.transport):
        out.append("interpreter_too_old")
    return out


def tree_digest(root) -> tuple:
    """The bundle digest, exactly as P0 computed it (evidence/p0-2026-10-28/tools/treedigest.py): SHA-256 over the
    sorted lines ``<relative path>\\t<file sha256>\\n`` of every regular file under ``root``, skipping ``__pycache__``,
    ``*.pyc``, ``.DS_Store`` and symbolic links. Returns ('sha256:<hex>', file count)."""
    root = str(root)
    lines = []
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x != "__pycache__")
        for f in sorted(files):
            if f == ".DS_Store" or f.endswith(".pyc"):
                continue
            p = os.path.join(d, f)
            if os.path.islink(p) or not os.path.isfile(p):
                continue
            with open(p, "rb") as fh:
                h = hashlib.sha256(fh.read()).hexdigest()
            lines.append(f"{os.path.relpath(p, root)}\t{h}\n")
    lines.sort()
    return "sha256:" + hashlib.sha256("".join(lines).encode()).hexdigest(), len(lines)


class HostLock:
    """The one-instance lock (G6). ``acquire`` raises ``HostRefused('host_lock_held')`` when another live process holds
    it; the holder's pid is written into the file for ``doctor``. The descriptor is not inherited by children."""

    def __init__(self, cfg: HostConfig):
        self.path = cfg.lock_path
        self._fd = None

    def acquire(self, now_iso: str):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                raise HostRefused("host_lock_held") from None
            raise
        os.ftruncate(fd, 0)
        os.write(fd, json.dumps({"pid": os.getpid(), "since": now_iso}).encode())
        os.fsync(fd)
        self._fd = fd

    def release(self):
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None

    def holder(self) -> dict:
        """{'state': 'free'} or {'state': 'held', 'pid': ..., 'since': ...}, without taking the lock for long."""
        if not self.path.exists():
            return {"state": "free"}
        fd = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except OSError:
                try:
                    info = json.loads(os.read(fd, 4096) or b"{}")
                except ValueError:
                    info = {}
                return {"state": "held", "pid": info.get("pid"), "since": info.get("since")}
            fcntl.flock(fd, fcntl.LOCK_UN)
            return {"state": "free"}
        finally:
            os.close(fd)

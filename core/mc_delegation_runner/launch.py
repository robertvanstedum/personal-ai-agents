"""The launch contract's steps around the environment (host runtime spec v0.5 R6.7-d to R6.7-f), applied from the one
policy file that ``launch_env`` reads: the pinned binary, the checks before start, the route on the item, the key by
reference for an API route, and the launch record. Used by the runtime (``runner.HostRuntime._launch``), the two
front-door connections and the fixed-send probe; the fallback door (operations) applies the same functions later.

1. **Pinned binary** (``verify_binary``). A tool's path and SHA-256 are configuration: ``host.yaml``'s ``tools`` for the
   runtime, ``--claude-bin-sha256`` / ``--codex-bin-sha256`` for a connection, ``claude_send_guard.BINARY_SHA256`` for
   the probe. A missing pin, a missing or linked file, or another digest is ``binary_unpinned``, before any spawn.
   Updating a CLI is therefore a configuration change: install the new build at a versioned path, record its digest
   in that configuration, and the next start uses it; nothing ever starts a bare name found on ``PATH``.
2. **The environment** is ``launch_env.child_env`` (the policy's profile; never inherited wholesale).
3. **Checks before start** (``check_tool``), per the policy's ``tools`` entry, failing closed and naming only:
   - on a subscription route, the sign-in must be present, or ``signin_missing`` names the login command; it never
     falls back to a key (R6.7-e);
   - no key setting in the user, managed or project configuration (``key_config_present`` names the file, as its
     template, and the setting; never its value). A tool started with its "ignore user configuration" flag
     (Codex's ``--ignore-user-config``) has its user-scope files skipped, because the CLI does not read them.
   Configuration is read structurally (JSON, or TOML through the standard library's ``tomllib``); a file that exists
   but cannot be read refuses (``key_config_unreadable``). The Keychain is never read here.
4. **The launch record** (``record``): start path, tool, profile, route, the binary's path and digest, the policy's
   version and digest, the environment names given and the names removed, the key's name and reference on an API
   route, and the checks' outcomes. Never a value. It is handed to the census seam (S1-F): the runtime writes
   ``<task>.launch.json`` beside the task's other files; a connection hands it to its manager, which appends it to
   ``connection/<agent>/launch.jsonl``; the probe publishes ``launch.json`` in its request folder.

**API routes (R6.7-f).** ``api_key`` reads the route's key by reference through a credential store (``keychain:`` on
the Mac, ``test:`` in acceptance), for one child, under the route's own name from the policy. The value is returned to
the caller only to be placed in that child's environment: never in the runtime's own environment, a file, a log, a
pending record or a launch record, and never through ``core/get_secret.py``. A route the policy marks unqualified
(R4.5-d: the key's reach into the CLI's tool processes is not shown) is refused ``api_route_unqualified``.

Stdlib only; Python 3.9-compatible (``tomllib`` is imported only when a TOML file exists).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import uuid
from typing import Mapping

from core.mc_delegation_runner import launch_env

RECORD_SCHEMA = "minimoi.launch.v1"
START_PATHS = ("runtime", "connection", "probe", "fallback")
MAX_CONFIG_BYTES = 32 * 1024 * 1024
_PIN = re.compile(r"^(?:sha256:)?([0-9a-f]{64})$")
_REF = re.compile(r"^(?:keychain:[A-Za-z0-9_.-]{1,64}/[A-Za-z0-9_.-]{1,64}|test:[A-Za-z0-9_.-]{1,64})$")


class LaunchRefused(ValueError):
    """A start the contract refuses, before any spawn: a fixed code, and details that are names, templates and
    commands, never a value."""

    def __init__(self, code: str, **detail):
        self.code, self.detail = code, detail
        super().__init__(code + ("" if not detail else ": " + ", ".join(f"{k}={v}" for k, v in sorted(detail.items()))))


# ── step 1: the pinned binary ─────────────────────────────────────────────────────────────────────────────────────
def pin(value) -> str | None:
    """A configured digest as 'sha256:<hex>' (64 lower-case hex digits, with or without the prefix), else None."""
    m = _PIN.match(value) if isinstance(value, str) else None
    return None if m is None else "sha256:" + m.group(1)


def file_digest(path) -> str:
    """'sha256:<hex>' of a regular file, read without following a link at its last component; the file read is the one
    the name pointed at when it was opened (device and inode checked)."""
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode):
        raise OSError("not a regular file")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        got = os.fstat(fd)
        if (got.st_dev, got.st_ino) != (st.st_dev, st.st_ino):
            raise OSError("file changed while opening")
        h = hashlib.sha256()
        for chunk in iter(lambda: os.read(fd, 1024 * 1024), b""):
            h.update(chunk)
        return "sha256:" + h.hexdigest()
    finally:
        os.close(fd)


def verify_binary(path, pinned) -> str:
    """The binary's digest when it is exactly the pinned one; ``binary_unpinned`` otherwise (no pin, no such regular
    executable file at an absolute path, or another digest)."""
    want = pin(pinned)
    if want is None:
        raise LaunchRefused("binary_unpinned", reason="no_pin")
    if not isinstance(path, str) or not os.path.isabs(path) or not os.access(path, os.X_OK):
        raise LaunchRefused("binary_unpinned", reason="binary_missing")
    try:
        got = file_digest(path)
    except OSError:
        raise LaunchRefused("binary_unpinned", reason="binary_unreadable") from None
    if got != want:
        raise LaunchRefused("binary_unpinned", reason="digest_mismatch")
    return got


# ── the route on the item (R6.7-e) ────────────────────────────────────────────────────────────────────────────────
def route(name, tool) -> dict:
    """The policy's entry for a route the item names, for this tool; never inferred from the environment."""
    r = launch_env.ROUTES.get(name) if isinstance(name, str) else None
    if r is None:
        raise LaunchRefused("route_not_in_launch_policy")
    if tool not in r["tools"]:
        raise LaunchRefused("route_tool_mismatch", route=name, tool=tool)
    if r["kind"] == "api" and not r["qualified"]:
        raise LaunchRefused("api_route_unqualified", route=name)             # R4.5-d, R6.7-f: reach not shown (N5)
    return r


# ── step 3: checks before start ───────────────────────────────────────────────────────────────────────────────────
def _path(template, home, project):
    return template.replace("{home}", home or "", 1).replace("{project}", project or "", 1)


def _read(path):
    """(raw bytes or None when the file does not exist). OSError when it exists and cannot be read."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_CONFIG_BYTES + 1)
    except FileNotFoundError:
        return None
    except NotADirectoryError:
        return None
    if len(raw) > MAX_CONFIG_BYTES:
        raise OSError("too large")
    return raw


def _parse(raw, fmt):
    if fmt == "json":
        return json.loads(raw.decode("utf-8"))
    try:
        import tomllib
    except ImportError:                                    # Python < 3.11: a TOML file that exists cannot be checked
        raise ValueError("toml_reader_unavailable") from None
    return tomllib.loads(raw.decode("utf-8"))


def _signin_ok(rule, home, project) -> bool:
    path = _path(rule["file"], home, project)
    if rule.get("exists"):
        return os.path.isfile(path)
    try:
        raw = _read(path)
        doc = None if raw is None else _parse(raw, "json")
    except (OSError, ValueError):
        return False
    if not isinstance(doc, dict):
        return False
    if "nonempty" in rule:
        return bool(doc.get(rule["nonempty"]))
    key, want = rule["equals"]
    return doc.get(key) == want


def _resolve(doc, dotted):
    """Every (path, value) a dotted key path reaches, '*' matching any one key."""
    out = [("", doc)]
    for part in dotted.split("."):
        nxt = []
        for prefix, node in out:
            if not isinstance(node, dict):
                continue
            for k in (node if part == "*" else [part]):
                if k in node:
                    nxt.append((f"{prefix}.{k}" if prefix else str(k), node[k]))
        out = nxt
    return out


def _walk(node, prefix=""):
    if isinstance(node, dict):
        for k, v in node.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            yield path, k, v
            yield from _walk(v, path)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v, prefix)


def _set(value) -> bool:
    return value is not None and value != "" and value != {} and value != []


def _settings(rule, doc) -> list:
    """The key settings a configuration document holds under one rule: names and dotted paths only."""
    found = []
    for dotted in rule.get("keys", []):
        found += [p for p, v in _resolve(doc, dotted) if _set(v)]
    names = set(rule.get("names_anywhere", []))
    if names:
        found += [p for p, k, v in _walk(doc) if k in names and _set(v)]
    for dotted in rule.get("env_tables", []):
        for p, table in _resolve(doc, dotted):
            if isinstance(table, dict):
                found += [f"{p}.{n}" for n in table if isinstance(n, str) and launch_env.denied(n)]
    for dotted, allowed in rule.get("values_allowed", {}).items():
        found += [p for p, v in _resolve(doc, dotted) if v not in allowed]     # the value itself is never reported
    return sorted(set(found))


def check_tool(tool, *, route_kind, home, project=None, argv=()) -> dict:
    """Step 3 for one start: the sign-in (subscription routes) and the key-setting checks. Returns what was checked
    (names only); raises LaunchRefused. ``project`` None skips project-scope files (the runtime checks them once the
    workspace exists); ``home`` is the HOME the child will get."""
    t = launch_env.TOOLS.get(tool)
    if t is None:
        raise LaunchRefused("tool_not_in_launch_policy", tool=tool)
    out = {"tool": tool, "signin": "not_required", "user_config": "checked"}
    if route_kind == "subscription":
        if not any(_signin_ok(rule, home, project) for rule in t["signin"]):
            raise LaunchRefused("signin_missing", tool=tool, login=t["login_command"])   # never a key instead
        out["signin"] = "present"
    flag = t.get("ignores_user_config_flag")
    ignores_user = bool(flag) and flag in list(argv)
    if ignores_user:
        out["user_config"] = f"ignored ({flag})"
    for rule in t["config"]:
        if rule["scope"] == "user" and ignores_user or rule["scope"] == "project" and project is None:
            continue
        try:
            raw = _read(_path(rule["file"], home, project))
            if raw is None:
                continue
            doc = _parse(raw, rule["format"])
        except (OSError, ValueError, UnicodeDecodeError):
            raise LaunchRefused("key_config_unreadable", tool=tool, file=rule["file"]) from None
        found = _settings(rule, doc)
        if found:
            raise LaunchRefused("key_config_present", tool=tool, file=rule["file"], setting="|".join(found))
    if project is not None:
        out["project_config"] = "checked"
    return out


# ── API routes: a key by reference, to one child (R6.7-f) ─────────────────────────────────────────────────────────
class CredentialStore:
    """Reads one key by reference, for one launch. Implementations never write the value anywhere."""
    scheme = ""

    def read(self, ref: str) -> str:
        raise NotImplementedError


class TestCredentialStore(CredentialStore):
    """Acceptance's store (§9.3, T19e): test values made for the run, by name (``test:<name>``). In memory, or from a
    private file (0600, a JSON object of name → value) for a runtime started as a process."""
    scheme = "test"
    __test__ = False                       # not a pytest class

    def __init__(self, values: Mapping[str, str]):
        self._values = dict(values)

    @classmethod
    def from_file(cls, path):
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode) & 0o077:
            raise LaunchRefused("route_key_store_unsafe")
        with open(path, "rb") as fh:
            doc = json.loads(fh.read(1024 * 1024).decode("utf-8"))
        if not isinstance(doc, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in doc.items()):
            raise LaunchRefused("route_key_store_invalid")
        return cls(doc)

    def read(self, ref: str) -> str:
        value = self._values.get(ref.split(":", 1)[1])
        if value is None:
            raise LaunchRefused("route_key_unavailable", ref=ref)
        return value


class KeychainCredentialStore(CredentialStore):
    """The Mac's login Keychain (``keychain:<service>/<account>``), read with ``security`` at launch, by reference.
    Built for the Mac; not exercised in S1 (no Keychain read happens in its tests or acceptance)."""
    scheme = "keychain"

    def __init__(self, run=subprocess.run):
        self._run = run

    def read(self, ref: str) -> str:
        service, _, account = ref.split(":", 1)[1].partition("/")
        try:
            got = self._run(["security", "find-generic-password", "-s", service, "-a", account, "-w"],
                            capture_output=True, text=True, timeout=10, env={"PATH": "/usr/bin:/bin"})
        except (OSError, subprocess.SubprocessError):
            raise LaunchRefused("route_key_unavailable", ref=ref) from None
        value = got.stdout.rstrip("\n") if got.returncode == 0 else ""
        if not value:
            raise LaunchRefused("route_key_unavailable", ref=ref)
        return value


def credential_ref(value) -> str | None:
    """A reference in the shape the stores read, else None."""
    return value if isinstance(value, str) and _REF.match(value) else None


def api_key(route_name, ref, stores: Mapping[str, CredentialStore]) -> tuple:
    """(the route's own key name, the value) for one API-route child. The value goes only into that child's
    environment; the caller drops it once the child is started."""
    r = launch_env.ROUTES.get(route_name)
    if r is None or r["kind"] != "api":
        raise LaunchRefused("route_not_api", route=route_name)
    if credential_ref(ref) is None:
        raise LaunchRefused("route_key_unbound", route=route_name)
    store = stores.get(ref.split(":", 1)[0])
    if store is None:
        raise LaunchRefused("route_key_store_missing", route=route_name, scheme=ref.split(":", 1)[0])
    value = store.read(ref)
    if not isinstance(value, str) or not 0 < len(value) <= 8192 or "\0" in value or "\n" in value:
        raise LaunchRefused("route_key_invalid", route=route_name)
    return r["key_name"], value


# ── step 4: the launch record ─────────────────────────────────────────────────────────────────────────────────────
def record(*, start_path, tool, profile, route_name, binary_path, binary_digest, pinned_by, env_names, parent_names,
           checks, at, key=None, **ids) -> dict:
    """The launch record (R6.7-d step 4, R6.6-a ``launch``): names, digests and references only. ``key`` is
    ``{name, ref}`` on an API route; the value is never passed here."""
    if start_path not in START_PATHS:
        raise ValueError("start_path_invalid")
    r = launch_env.ROUTES.get(route_name, {})
    given = sorted(set(env_names))
    return {"schema": RECORD_SCHEMA, "launch_id": str(uuid.uuid4()), "at": at, "start_path": start_path,
            "tool": tool, "profile": profile, "route": route_name, "route_kind": r.get("kind"),
            "binary": {"path": binary_path, "sha256": binary_digest, "pinned_by": pinned_by},
            "policy": {"version": launch_env.POLICY_VERSION, "sha256": launch_env.POLICY_DIGEST},
            "env_names": given, "removed": sorted(set(parent_names) - set(given)),
            "key": None if key is None else {"name": key["name"], "ref": key["ref"]},
            "checks": checks, **{k: v for k, v in ids.items() if v is not None}}


def connection_start(*, tool, route_name, binary, pinned, env, parent, project, argv, at, **ids) -> dict:
    """Steps 1, 3 and 4 for a front-door connection, whose environment (step 2) is already built from the policy: its
    launch record, or LaunchRefused before any spawn. A connection runs on a subscription route only."""
    r = route(route_name, tool)
    if r["kind"] != "subscription":
        raise LaunchRefused("connection_route_not_subscription", route=route_name)
    digest = verify_binary(binary, pinned)
    checks = check_tool(tool, route_kind=r["kind"], home=env.get("HOME") or os.path.expanduser("~"),
                        project=project, argv=argv)
    return record(start_path="connection", tool=tool, profile="unattended", route_name=route_name,
                  binary_path=binary, binary_digest=digest, pinned_by="argument", env_names=list(env),
                  parent_names=list(parent), checks=checks, at=at, **ids)

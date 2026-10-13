"""Rooms identity from the mini-moi sign-in (design: _working/guild-beta/ROOMS_MINIMOI_IDENTITY_DESIGN_2026-10-11.md, draft 3).

The portal has signed the person in. This module decides whether that person may use Rooms and, if so, which Records principal they act as,
and builds the signed assertion the bridge sends to Records for ONE exact request. Nothing here is a hardcoded list of people: access is a
binding row in a small data file the owner changes (audited), and a missing, unreadable, inactive or invalid binding is refused, never
replaced by another identity.

Bindings file ``bindings.json`` in the Rooms access folder::

    {"version": 1, "bindings": [{"account": "account:robert", "principal": "robert", "active": true,
                                  "granted_by": "seed", "granted_at": "2026-10-11T00:00:00Z"}]}

The assertion format is the one Records verifies (prototype-lab/projects/project-records-room-poc/portal_identity.py); tests check the two sides
agree. The key file holds one line ``<kid>:<secret>`` and is never printed.
"""
from __future__ import annotations

import base64
import contextlib
import fcntl
import hashlib
import hmac
import json
import os
import re
import time
from urllib.parse import parse_qsl, urlencode

AUDIENCE = "records"
MAX_LIFETIME = 30
PRINCIPAL = re.compile(r"[a-z0-9][a-z0-9_-]{0,59}")
OWNER_PRINCIPAL = "robert"


def principal_for(account: str) -> str:
    """The Records principal a granted account acts as: derived from the account, so granting twice never makes two people, and always
    u-prefixed so it can never be the owner or an agent (Records refuses anything else)."""
    name = re.sub(r"[^a-z0-9]+", "-", account.split(":", 1)[-1].lower()).strip("-")[:30] or "user"
    return f"u-{name}-{hashlib.sha256(account.encode()).hexdigest()[:6]}"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def canonical_target(path: str, query) -> str:
    if isinstance(query, bytes):
        query = query.decode("utf-8", "replace")
    pairs = sorted(parse_qsl(query or "", keep_blank_values=True))
    return path + ("?" + urlencode(pairs) if pairs else "")


def load_key(path):
    """(kid, secret) or None when the file is missing or not a usable key."""
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            line = fh.readline().strip()
    except OSError:
        return None
    kid, sep, secret = line.partition(":")
    if not sep or not kid or len(secret) < 32:
        return None
    return kid, secret


def sign(key, *, principal, method, target, body: bytes, env, now=None, nonce=None) -> str:
    kid, secret = key
    now = int(time.time()) if now is None else int(now)
    claims = {"aud": AUDIENCE, "env": env, "kid": kid, "sub": principal, "iat": now, "exp": now + MAX_LIFETIME,
              "nonce": nonce or _b64(os.urandom(18)), "method": method.upper(), "target": target,
              "body": hashlib.sha256(body or b"").hexdigest()}
    payload = "v2." + _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    mac = hmac.new(secret.encode("utf-8") if isinstance(secret, str) else secret, payload.encode("ascii"), hashlib.sha256).digest()
    return payload + "." + _b64(mac)


class Decision:
    """What the bridge does for one signed-in person: ok (act as principal), no_access (403) or unavailable (503)."""
    def __init__(self, kind, principal=None, reason=""):
        self.kind, self.principal, self.reason = kind, principal, reason

    @property
    def ok(self):
        return self.kind == "ok"


class RoomsAccess:
    """The binding file and its audit trail. Read on every decision (a withdrawn grant applies on the very next request)."""
    def __init__(self, directory):
        self.directory = directory
        self.path = os.path.join(directory, "bindings.json") if directory else None
        self.audit_path = os.path.join(directory, "audit.jsonl") if directory else None

    def _read(self):
        """The bindings list, [] when the file does not exist (nobody has access), or None when it cannot be trusted (unreadable, damaged)."""
        if not self.path:
            return None
        try:
            with open(self.path, "rb") as fh:
                raw = fh.read(1_000_000)
        except FileNotFoundError:
            return []
        except OSError:
            return None
        try:
            doc = json.loads(raw.decode("utf-8"))
            rows = doc["bindings"]
            if doc.get("version") != 1 or not isinstance(rows, list):
                return None
            for r in rows:
                if not (isinstance(r, dict) and isinstance(r.get("account"), str) and isinstance(r.get("principal"), str)
                        and isinstance(r.get("active"), bool)):
                    return None
            return rows
        except (ValueError, KeyError, UnicodeDecodeError, TypeError, AttributeError):
            return None

    def decide(self, account, *, account_active=True) -> Decision:
        if not account or not account_active:
            return Decision("no_access", reason="account is not active")
        rows = self._read()
        if rows is None:
            return Decision("unavailable", reason="the Rooms access list cannot be read")
        for r in rows:
            if r["account"] == account:
                if r.get("kind", "human") != "human" or r["principal"] in {"mc", "codex", "claude-code"}:
                    return Decision("no_access", reason="agent identity is not a portal account")
                if r["active"] is not True:
                    return Decision("no_access", reason="access withdrawn")
                if not PRINCIPAL.fullmatch(r["principal"]):
                    return Decision("unavailable", reason="the binding is not valid")
                return Decision("ok", principal=r["principal"])
        return Decision("no_access", reason="no Rooms access granted")

    # ── the owner's grant and withdraw (Slice 2): a locked read-modify-write of the same file, every change audited ──
    @contextlib.contextmanager
    def _locked(self):
        os.makedirs(self.directory, exist_ok=True)
        fd = os.open(os.path.join(self.directory, "bindings.lock"), os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)                                   # closing releases the lock

    def _write(self, rows):
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps({"version": 1, "bindings": rows}, indent=2).encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, self.path)

    def listing(self):
        """Every binding row, or None when the file cannot be trusted."""
        return self._read()

    def begin_grant(self, account, label, by):
        """Record the intention to give an account Rooms access: a PENDING row (not active) with its derived principal. Idempotent: a second call
        for the same account returns the same row. Never changes the principal of an existing row and never touches the owner's."""
        principal = principal_for(account)
        with self._locked():
            rows = self._read()
            if rows is None:
                raise ValueError("the Rooms access list cannot be read")
            for r in rows:
                if r["account"] == account:
                    if r["principal"] != principal:
                        raise ValueError("this account is already bound to another identity")
                    if r["active"]:
                        return dict(r)
                    r.update({"pending": True, "label": label, "granted_by": by})
                    self._write(rows)
                    self.audit({"event": "grant_retry", "account": account, "principal": principal, "by": by})
                    return dict(r)
            row = {"account": account, "principal": principal, "label": label, "active": False, "pending": True, "granted_by": by,
                   "granted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            rows.append(row)
            self._write(rows)
            self.audit({"event": "grant_started", "account": account, "principal": principal, "by": by})
            return dict(row)

    def complete_grant(self, account, by):
        """Activate a pending grant, only after its Records person exists."""
        with self._locked():
            rows = self._read()
            if rows is None:
                raise ValueError("the Rooms access list cannot be read")
            for r in rows:
                if r["account"] == account:
                    r.update({"active": True, "pending": False})
                    self._write(rows)
                    self.audit({"event": "grant_completed", "account": account, "principal": r["principal"], "by": by})
                    return dict(r)
        raise ValueError("no such grant")

    def withdraw(self, account, by):
        """Switch an account's access off at once (the next call is refused). The owner's own binding cannot be withdrawn here (repair is a
        deliberate file-level act); the person's past contributions stay in the rooms."""
        with self._locked():
            rows = self._read()
            if rows is None:
                raise ValueError("the Rooms access list cannot be read")
            for r in rows:
                if r["account"] == account:
                    if r["principal"] == OWNER_PRINCIPAL:
                        raise ValueError("the owner's own access is not withdrawn from here")
                    r.update({"active": False, "pending": False})
                    self._write(rows)
                    self.audit({"event": "withdrawn", "account": account, "principal": r["principal"], "by": by})
                    return dict(r)
        raise ValueError("no such grant")

    def audit(self, event: dict) -> None:
        """Append one line (who did what, when). Never holds a secret."""
        if not self.audit_path:
            return
        os.makedirs(self.directory, exist_ok=True)
        line = json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **event}, separators=(",", ":"), sort_keys=True)
        fd = os.open(self.audit_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

    def seed_owner(self, account, principal, granted_by="seed"):
        """Create the owner's binding if the file does not exist (provisioning). Never overwrites an existing file."""
        if not self.path:
            raise ValueError("no access folder")
        if os.path.exists(self.path):
            return False
        if not PRINCIPAL.fullmatch(principal):
            raise ValueError("principal is not valid")
        os.makedirs(self.directory, exist_ok=True)
        doc = {"version": 1, "bindings": [{"account": account, "principal": principal, "active": True, "granted_by": granted_by,
                                           "granted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}]}
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, json.dumps(doc, indent=2).encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, self.path)
        self.audit({"event": "seed", "account": account, "principal": principal, "by": granted_by})
        return True

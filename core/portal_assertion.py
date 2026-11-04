"""Portal-signed request assertions for any audience (partnership v1.1 B0-2/F3; spec A02).

The portal has signed the person in. For one request it forwards, it adds an assertion naming ONE subject for ONE exact
request (method, path, sorted query, body digest), signed with a secret only the portal and that audience hold. The
receiver checks it and trusts no forwarded header for the same purpose. A bad, stale, replayed or altered assertion is
refused; it never falls back to a header.

Same wire format as the Records assertion (domains/records/portal_identity.py, minimoi_portal/rooms_identity.py), with
the audience as a parameter:
``v2.<base64url(claims JSON)>.<base64url(HMAC-SHA256(secret, "v2." + base64url(claims)))>``.
Claims: aud, env, kid, sub, iat, exp (at most MAX_LIFETIME after iat), nonce, method, target, body (SHA-256 hex).
A key file holds one line ``<kid>:<secret>`` (secret at least 32 characters); it is never printed or logged.
Each audience has its own key, so an assertion for one can never be replayed at another.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

MAX_LIFETIME = 30        # seconds an assertion may be valid
FUTURE_SKEW = 5          # seconds an issue time may be ahead of the receiver's clock


class Refused(Exception):
    """Not acceptable. ``status`` is 401 for a bad assertion, 503 when the receiver has no key or nonce store."""
    def __init__(self, message, status=401):
        super().__init__(message)
        self.message, self.status = message, status


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _bytes(secret):
    return secret.encode("utf-8") if isinstance(secret, str) else secret


def canonical_target(path: str, query) -> str:
    """The path as the receiver sees it (decoded) plus the query, parsed and sorted so both sides agree."""
    if isinstance(query, bytes):
        query = query.decode("utf-8", "replace")
    pairs = sorted(parse_qsl(query or "", keep_blank_values=True))
    return path + ("?" + urlencode(pairs) if pairs else "")


def load_key(path):
    """(kid, secret bytes) from the key file, or None when there is no usable key."""
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
    return kid, secret.encode("utf-8")


def sign(key, *, audience, subject, method, target, body: bytes, env, now=None, nonce=None,
         lifetime=MAX_LIFETIME) -> str:
    kid, secret = key[0], _bytes(key[1])
    now = int(time.time()) if now is None else int(now)
    claims = {"aud": audience, "env": env, "kid": kid, "sub": subject, "iat": now, "exp": now + lifetime,
              "nonce": nonce or b64(os.urandom(18)), "method": method.upper(), "target": target,
              "body": hashlib.sha256(body or b"").hexdigest()}
    payload = "v2." + b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return payload + "." + b64(hmac.new(secret, payload.encode("ascii"), hashlib.sha256).digest())


def verify(header, key, *, audience, env, method, target, body, spend, now=None) -> str:
    """The subject the assertion names, or Refused. ``spend(nonce, expires)`` must record the nonce durably and return
    False when it was already used (a second delivery is refused, across restarts and concurrent copies). ``body`` is
    the bytes or a callable returning them: a callable is read only after the MAC, claims and times have passed, so an
    unsigned caller never makes the receiver buffer a body."""
    if key is None or spend is None:
        raise Refused("Signed owner requests are not set up here", 503)
    if not isinstance(header, str) or header.count(".") != 2 or len(header) > 4096:
        raise Refused("Portal assertion is malformed")
    version, claims_b64, mac_b64 = header.split(".")
    if version != "v2":
        raise Refused("Portal assertion version is not supported")
    kid, secret = key[0], _bytes(key[1])
    expected = hmac.new(secret, f"v2.{claims_b64}".encode("ascii"), hashlib.sha256).digest()
    try:
        given = unb64(mac_b64)
        claims = json.loads(unb64(claims_b64).decode("utf-8"))
    except Exception:
        raise Refused("Portal assertion is malformed") from None
    if not hmac.compare_digest(expected, given) or not isinstance(claims, dict):
        raise Refused("Portal assertion is not valid")
    now = time.time() if now is None else now
    try:
        iat, exp = float(claims["iat"]), float(claims["exp"])
        if (claims["aud"] != audience or claims["env"] != env or claims["kid"] != kid
                or claims["method"] != method.upper() or claims["target"] != target):
            raise Refused("Portal assertion is not for this request")
        sub, nonce, body_digest = claims["sub"], claims["nonce"], claims["body"]
    except (KeyError, TypeError, ValueError):
        raise Refused("Portal assertion is malformed") from None
    if not isinstance(sub, str) or not sub or len(sub) > 80 or not isinstance(nonce, str) or not 16 <= len(nonce) <= 80:
        raise Refused("Portal assertion is malformed")
    if exp - iat > MAX_LIFETIME or exp <= iat:
        raise Refused("Portal assertion lifetime is not acceptable")
    if iat > now + FUTURE_SKEW:
        raise Refused("Portal assertion is dated in the future")
    if exp < now:
        raise Refused("Portal assertion has expired")
    raw = body() if callable(body) else body
    if not isinstance(body_digest, str) or not hmac.compare_digest(body_digest, hashlib.sha256(raw or b"").hexdigest()):
        raise Refused("Portal assertion is not for this request")
    if not spend(nonce, exp + FUTURE_SKEW):
        raise Refused("Portal assertion was already used")
    return sub


class NonceFile:
    """A durable spent-nonce store in one SQLite file (the receiver's own; file-first, no shared database). The primary
    key decides who was first when two copies arrive at once."""
    def __init__(self, path):
        self.path = Path(path)

    def __call__(self, nonce, expires, now=None):
        """Every step runs inside one BEGIN IMMEDIATE write transaction, so concurrent callers wait on the busy timeout
        instead of failing (no journal-mode switch: that is not covered by the busy timeout)."""
        now = time.time() if now is None else now
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS nonces(nonce TEXT PRIMARY KEY, expires REAL NOT NULL)")
            db.execute("DELETE FROM nonces WHERE expires < ?", (now,))
            try:
                db.execute("INSERT INTO nonces VALUES(?,?)", (nonce, expires))
            except sqlite3.IntegrityError:
                db.execute("ROLLBACK")
                return False
            db.execute("COMMIT")
            return True
        finally:
            db.close()

"""Test-only stand-in for the portal's F3 signing (partnership v1.1 B0-2).

``install(monkeypatch, folder)`` configures CoS's owner-assertion key, environment and nonce file, and wraps Flask so a
request carrying ``X-Test-Sign-Owner: <uid>`` is signed exactly as the portal signs it (its real method, path, query and
body; audience cos-owner; a fresh nonce). CoS then runs its real verification. Like the portal, it also signs every
other owner write (tier owner, forwarded id) and /chat without an owner as service:mc; X-Test-Unsigned: 1 opts out,
for the tests that prove an unsigned or header-only write is refused. Installing twice is harmless: the first
wrapper to see a request decides for it, and the others pass it through unchanged."""
from __future__ import annotations

import io

OWNER_SIGNING_KEY = ("test-kid", "test-cos-owner-secret-0123456789abcdef")


def install(monkeypatch, folder):
    from flask import Flask
    from core import portal_assertion as PA
    folder.mkdir(parents=True, exist_ok=True)
    key_file = folder / "cos-owner.key"
    key_file.write_text(":".join(OWNER_SIGNING_KEY) + "\n")
    monkeypatch.setenv("COS_OWNER_ASSERTION_KEY_FILE", str(key_file))
    monkeypatch.setenv("COS_OWNER_ASSERTION_ENV", "test")
    monkeypatch.setenv("COS_OWNER_ASSERTION_NONCE_FILE", str(folder / "cos-owner-nonces.sqlite3"))
    original = Flask.__call__

    def signing_portal(self, environ, start_response):
        if environ.get("minimoi.test_portal_signed"):            # installed twice: one wrapper decides, once
            return original(self, environ, start_response)
        environ["minimoi.test_portal_signed"] = True
        owner = environ.pop("HTTP_X_TEST_SIGN_OWNER", "")
        unsigned = environ.pop("HTTP_X_TEST_UNSIGNED", "")
        subject = f"owner:{owner}" if owner else None
        if (not owner and not unsigned and environ["REQUEST_METHOD"] not in ("GET", "HEAD", "OPTIONS")
                and "HTTP_X_PORTAL_OWNER_ASSERTION" not in environ):
            # What the portal now does: it signs every owner write it forwards (the forwarded owner id), and Master
            # Craftsman's /chat calls as service:mc. X-Test-Unsigned: 1 sends a request the portal did not sign.
            uid = environ.get("HTTP_X_MINIMOI_AUTH_ID") or environ.get("HTTP_X_MINIMOI_USERNAME")
            if uid and environ.get("HTTP_X_MINIMOI_USER_TIER", "owner") == "owner":     # every /app/cos route is owner-only
                subject = f"owner:{uid}"
            elif environ.get("PATH_INFO") == "/chat":
                subject = "service:mc"
            elif not uid:
                subject = "owner:test-owner"      # a test that set no identity headers: still a portal-forwarded write
        if subject:
            body = environ["wsgi.input"].read(int(environ.get("CONTENT_LENGTH") or 0))
            environ["wsgi.input"] = io.BytesIO(body)
            path = environ.get("SCRIPT_NAME", "") + environ.get("PATH_INFO", "")
            environ["HTTP_X_PORTAL_OWNER_ASSERTION"] = PA.sign(
                OWNER_SIGNING_KEY, audience="cos-owner", subject=subject, method=environ["REQUEST_METHOD"],
                target=PA.canonical_target(path, environ.get("QUERY_STRING", "")), body=body, env="test")
        return original(self, environ, start_response)
    monkeypatch.setattr(Flask, "__call__", signing_portal)

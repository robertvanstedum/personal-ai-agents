"""CoS owner writes need a portal-signed assertion, not forwarded headers (partnership v1.1 B0-2/F3; spec A02).

The portal adds ``X-Portal-Owner-Assertion`` to the CoS owner writes it has already gated (owner sign-in and CSRF):
the voice writes and In flight's writes. The assertion is bound to that exact request and spent once. Forwarded
``X-Minimoi-*`` headers are still read for display and reads, but never make a write the owner's: a container on the
same network can set a header, not sign an assertion.

Configuration (all three, or every owner write is refused):
  COS_OWNER_ASSERTION_KEY_FILE    one line ``<kid>:<secret>``, the same key the portal signs with
  COS_OWNER_ASSERTION_ENV         the environment name the portal signs for (dev, staging, production)
  COS_OWNER_ASSERTION_NONCE_FILE  SQLite file for spent nonces (the CoS data volume)
"""
from __future__ import annotations

import os

from flask import g, request

from core import portal_assertion as PA

AUDIENCE = "cos-owner"
HEADER = "X-Portal-Owner-Assertion"
SUBJECT_PREFIX = "owner:"


def _config(environ):
    key = PA.load_key(environ.get("COS_OWNER_ASSERTION_KEY_FILE"))
    env = environ.get("COS_OWNER_ASSERTION_ENV") or None
    nonce_file = environ.get("COS_OWNER_ASSERTION_NONCE_FILE")
    return key, env, (PA.NonceFile(nonce_file) if nonce_file else None)


def verified_owner_write(environ=None):
    """The owner's id from the portal's signed assertion for THIS request, or None. Checked once per request (the
    nonce is spent on the first check; later checks in the same request reuse the answer)."""
    cached = getattr(g, "_cos_owner_assertion", False)
    if cached is not False:
        return cached
    answer = None
    key, env, spend = _config(os.environ if environ is None else environ)
    header = request.headers.get(HEADER)
    if header and key is not None and env and spend is not None:
        try:
            subject = PA.verify(header, key, audience=AUDIENCE, env=env, method=request.method,
                                target=PA.canonical_target(request.path, request.query_string),
                                body=request.get_data(cache=True), spend=spend)
        except PA.Refused:
            subject = None
        if subject and subject.startswith(SUBJECT_PREFIX) and len(subject) > len(SUBJECT_PREFIX):
            answer = subject[len(SUBJECT_PREFIX):]
    g._cos_owner_assertion = answer
    return answer

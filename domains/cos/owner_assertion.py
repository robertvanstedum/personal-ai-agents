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

import logging
import os
import sqlite3

from flask import request

from core import portal_assertion as PA

AUDIENCE = "cos-owner"
HEADER = "X-Portal-Owner-Assertion"
SUBJECT_PREFIX = "owner:"
SERVICE_PREFIX = "service:"


def _config(environ):
    key = PA.load_key(environ.get("COS_OWNER_ASSERTION_KEY_FILE"))
    env = environ.get("COS_OWNER_ASSERTION_ENV") or None
    nonce_file = environ.get("COS_OWNER_ASSERTION_NONCE_FILE")
    return key, env, (PA.NonceFile(nonce_file) if nonce_file else None)


_CACHE = "minimoi.cos_owner_assertion"
MAX_BODY = 1_000_000          # default cap: owner writes are small JSON; uploads and audio pass their own route cap

_log = logging.getLogger(__name__)


class _TooLarge(Exception):
    pass


def verified_owner_write(environ=None, *, max_body=MAX_BODY):
    """The owner's id from the portal's signed assertion for THIS request, or None."""
    subject = verified_subject(environ, max_body=max_body)
    if subject and subject.startswith(SUBJECT_PREFIX) and len(subject) > len(SUBJECT_PREFIX):
        return subject[len(SUBJECT_PREFIX):]
    return None


def verified_subject(environ=None, *, max_body=MAX_BODY):
    """The subject ("owner:<id>" or "service:<name>") of the portal's signed assertion for THIS request, or None.
    Checked once per request (the nonce is spent on the first check; later checks in the same request reuse the
    answer, cached on the request itself, never on an app context that could outlive it). Every failure refuses; none
    is a 500."""
    cache = request.environ
    if _CACHE in cache:
        return cache[_CACHE]
    cache[_CACHE] = None
    header = request.headers.get(HEADER)
    if not header:
        return None
    key, env, spend = _config(os.environ if environ is None else environ)
    if key is None or not env or spend is None:
        _log.warning("cos owner write refused: signed owner requests are not configured (COS_OWNER_ASSERTION_*)")
        return None
    def body():                                       # read only after the MAC and claims have passed
        length = request.content_length
        if (length is None and request.headers.get("Transfer-Encoding")) or (length or 0) > max_body:
            raise _TooLarge()                         # an unsized or oversized body is never read for an owner write
        return request.get_data(cache=True)
    try:
        subject = PA.verify(header, key, audience=AUDIENCE, env=env, method=request.method,
                            target=PA.canonical_target(request.path, request.query_string),
                            body=body, spend=spend)
    except (PA.Refused, _TooLarge):
        return None
    except (sqlite3.Error, OSError):
        _log.exception("cos owner write refused: the spent-nonce store failed")
        return None
    if subject.startswith((SUBJECT_PREFIX, SERVICE_PREFIX)):
        cache[_CACHE] = subject
    return cache[_CACHE]

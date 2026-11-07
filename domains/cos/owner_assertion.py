"""CoS owner writes need a portal-signed assertion, not forwarded headers (partnership v1.1 B0-2/F3; spec A02).

The portal adds ``X-Portal-Owner-Assertion`` to the CoS owner writes it has already gated (owner sign-in and CSRF):
the voice writes and In flight's writes. The assertion is bound to that exact request and spent once. Forwarded
``X-Minimoi-*`` headers are still read for display and reads, but never make a write the owner's: a container on the
same network can set a header, not sign an assertion.

Configuration (key, environment and nonce file, or every signed write is refused):
  COS_OWNER_ASSERTION_KEY_FILE      the portal's key, one line ``<kid>:<secret>``: may sign owner:<id> for any gated
                                    write, and service:mc for /chat and MC's reply path only
  COS_TELEGRAM_ASSERTION_KEY_FILE   optional, the Telegram bot's own key: may sign service:telegram for /chat only
  COS_OWNER_ASSERTION_ENV           the environment name both sign for (dev, staging, production)
  COS_OWNER_ASSERTION_NONCE_FILE    SQLite file for spent nonces (the CoS data volume)
Each key may sign only the subjects and paths mapped to it here, on the server (F2 design v0.3 D4): a valid MAC from
the Telegram key naming owner:<id>, or any path but /chat, is refused.
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
MC_REPLY_PATH = "/internal/cos/v1/mc-reply"      # MC's answer to a send through CoS (domains/cos/mc_send.py)


def _portal_may(subject, path):
    return (subject.startswith(SUBJECT_PREFIX) and len(subject) > len(SUBJECT_PREFIX)) or \
        (subject == "service:mc" and path in ("/chat", MC_REPLY_PATH))


def _telegram_may(subject, path):
    return subject == "service:telegram" and path == "/chat"


def _config(environ):
    """({kid: (key, may)}, env, spend). Two keys with the same kid are a configuration error: neither is used."""
    keys = {}
    for name, may in (("COS_OWNER_ASSERTION_KEY_FILE", _portal_may), ("COS_TELEGRAM_ASSERTION_KEY_FILE", _telegram_may)):
        key = PA.load_key(environ.get(name))
        if key is None:
            continue
        if key[0] in keys:
            _log.warning("cos signed writes refused: two assertion keys share kid %s", key[0])
            return {}, None, None
        keys[key[0]] = (key, may)
    env = environ.get("COS_OWNER_ASSERTION_ENV") or None
    nonce_file = environ.get("COS_OWNER_ASSERTION_NONCE_FILE")
    return keys, env, (PA.NonceFile(nonce_file) if nonce_file else None)


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
    keys, env, spend = _config(os.environ if environ is None else environ)
    if not keys or not env or spend is None:
        _log.warning("cos owner write refused: signed owner requests are not configured (COS_OWNER_ASSERTION_*)")
        return None
    def body():                                       # read only after the MAC and claims have passed
        length = request.content_length
        if (length is None and request.headers.get("Transfer-Encoding")) or (length or 0) > max_body:
            raise _TooLarge()                         # an unsized or oversized body is never read for an owner write
        return request.get_data(cache=True)
    chosen = keys.get(PA.claimed_kid(header))
    if chosen is None:
        return None
    key, may = chosen
    try:
        subject = PA.verify(header, key, audience=AUDIENCE, env=env, method=request.method,
                            target=PA.canonical_target(request.path, request.query_string),
                            body=body, spend=spend)
    except (PA.Refused, _TooLarge):
        return None
    except (sqlite3.Error, OSError):
        _log.exception("cos owner write refused: the spent-nonce store failed")
        return None
    if may(subject, request.path):                   # this key may sign this subject for this path (server-side map)
        cache[_CACHE] = subject
    else:
        _log.warning("cos signed write refused: key %s may not sign %s for %s", key[0], subject, request.path)
    return cache[_CACHE]

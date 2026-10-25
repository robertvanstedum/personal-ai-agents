"""Purpose/version-separated HMAC envelopes; identities never come from JSON."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets

from .contract import MAX_REQUEST, MAX_RESPONSE, operation_id, packed, require, route_path

VERSION = '1'
REQUEST_PURPOSE = 'minimoi.domain-request/1'
RESPONSE_PURPOSE = 'minimoi.domain-response/1'
NONCE = re.compile(r'[a-f0-9]{32}')
SIGNATURE = re.compile(r'[a-f0-9]{64}')
HEADER_NAMES = ('X-MiniMoi-Domain-Version', 'X-MiniMoi-Key', 'X-MiniMoi-Audience',
                'X-MiniMoi-Environment', 'X-MiniMoi-Time', 'X-MiniMoi-Nonce',
                'Idempotency-Key', 'X-MiniMoi-Signature')


def checked_body(method, route, raw, profile, operation):
    require(method in {'GET', 'POST', 'PATCH'}, 'method_invalid')
    route_path(route, profile.domain)
    require(isinstance(raw, bytes) and len(raw) <= MAX_REQUEST, 'body_bound', 413)
    if method == 'GET':
        require(raw == b'' and operation == '', 'get_body_or_operation')
    else:
        if not (method == 'POST' and route == '/internal/domain-api/v1/guild/board/notes/query'):
            operation_id(operation)
        else:
            require(operation == '', 'query_operation_refused')
        require(bool(raw), 'json_required')


def request_signature(profile, *, method, route, raw, timestamp, nonce, operation):
    envelope = [REQUEST_PURPOSE, profile.key_id, profile.domain, profile.environment,
                profile.principal, profile.actor, profile.numeric_user_id, method,
                route, timestamp, nonce, operation, hashlib.sha256(raw).hexdigest()]
    return hmac.new(profile.secret, packed(envelope), hashlib.sha256).hexdigest()


def request_headers(profile, *, method, route, raw, operation='', now, nonce=None):
    checked_body(method, route, raw, profile, operation)
    nonce = secrets.token_hex(16) if nonce is None else nonce
    require(isinstance(nonce, str) and NONCE.fullmatch(nonce), 'nonce_invalid')
    timestamp = str(int(now))
    require(re.fullmatch(r'[0-9]{1,12}', timestamp), 'timestamp_invalid')
    headers = dict(zip(HEADER_NAMES, (VERSION, profile.key_id, profile.domain, profile.environment,
        timestamp, nonce, operation, request_signature(profile, method=method, route=route, raw=raw,
                                                      timestamp=timestamp, nonce=nonce, operation=operation))))
    headers['Accept'] = 'application/json'
    if method in {'POST', 'PATCH'}:
        headers['Content-Type'] = 'application/json'
    return headers


def response_signature(profile, *, nonce, status, raw):
    require(isinstance(nonce, str) and NONCE.fullmatch(nonce), 'nonce_invalid')
    require(type(status) is int and 100 <= status <= 599, 'status_invalid')
    require(isinstance(raw, bytes) and len(raw) <= MAX_RESPONSE, 'response_bound', 413)
    prefix = packed([RESPONSE_PURPOSE, profile.key_id, profile.domain, profile.environment,
                     profile.principal, profile.actor, profile.numeric_user_id, nonce, status])
    return hmac.new(profile.secret, prefix + b'\n' + raw, hashlib.sha256).hexdigest()


def verify_response(profile, *, nonce, status, raw, signature):
    require(isinstance(signature, str) and SIGNATURE.fullmatch(signature), 'response_signature_invalid', 401)
    require(hmac.compare_digest(signature, response_signature(profile, nonce=nonce, status=status, raw=raw)),
            'response_signature_invalid', 401)

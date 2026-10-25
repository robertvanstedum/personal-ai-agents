"""Host-owned profiles and bounded wire values for domain API v1.

This is independent of MC grants, browser sessions, and Library intake v1.
Keys must be provisioned separately per actor/domain/environment.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from uuid import UUID

MAX_REQUEST = 16 * 1024
MAX_RESPONSE = 64 * 1024
DOMAINS = frozenset({'german', 'portuguese', 'curator', 'guild'})
IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}')


class DomainError(ValueError):
    def __init__(self, code, status=400, *, state='not_applied'):
        super().__init__(code)
        self.code, self.status, self.state = code, status, state


class TransportUnknown(DomainError):
    def __init__(self):
        super().__init__('domain_outcome_unconfirmed', 503, state='unknown')


def require(condition, code, status=400):
    if not condition:
        raise DomainError(code, status)


def operation_id(value):
    try:
        require(isinstance(value, str) and str(UUID(value)) == value, 'operation_id_invalid')
    except (ValueError, AttributeError):
        raise DomainError('operation_id_invalid') from None
    return value


def packed(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise DomainError('json_invalid') from None


def decode(raw, *, maximum=MAX_REQUEST):
    require(isinstance(raw, bytes) and 0 < len(raw) <= maximum, 'body_bound', 413)
    def pairs(rows):
        result = {}
        for key, value in rows:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    def constant(_):
        raise DomainError('json_invalid')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
        require(type(value) is dict, 'json_object_required')
        return value
    except DomainError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise DomainError('json_invalid') from None


def route_path(value, domain):
    require(domain in DOMAINS and isinstance(value, str)
            and re.fullmatch(r'/internal/domain-api/v1/' + domain + r'/[A-Za-z0-9_/-]{1,256}', value) is not None
            and '//' not in value and not value.endswith('/'), 'route_invalid')
    return value


@dataclass(frozen=True)
class KeyProfile:
    key_id: str
    principal: str
    actor: str
    environment: str
    domain: str
    capabilities: frozenset[str]
    secret: bytes = field(repr=False)
    enabled: bool = True
    requests_per_minute: int = 60
    numeric_user_id: int | None = None

    def __post_init__(self):
        require(all(isinstance(v, str) and IDENTIFIER.fullmatch(v) for v in (self.key_id, self.principal, self.actor)),
                'key_profile_invalid')
        require(self.environment in {'dev', 'prod'} and self.domain in DOMAINS, 'key_profile_invalid')
        require(isinstance(self.secret, bytes) and 32 <= len(self.secret) <= 256, 'key_profile_invalid')
        require(type(self.enabled) is bool and type(self.requests_per_minute) is int
                and 1 <= self.requests_per_minute <= 600, 'key_profile_invalid')
        require(self.numeric_user_id is None or type(self.numeric_user_id) is int and self.numeric_user_id > 0,
                'key_profile_invalid')
        require(isinstance(self.capabilities, (set, frozenset)) and 0 < len(self.capabilities) <= 32
                and all(isinstance(c, str) and c.startswith(self.domain + '.') and IDENTIFIER.fullmatch(c)
                        for c in self.capabilities), 'key_profile_invalid')
        object.__setattr__(self, 'capabilities', frozenset(self.capabilities))


@dataclass(frozen=True)
class VerifiedRequest:
    profile: KeyProfile
    method: str
    route: str
    nonce: str
    operation_id: str
    request_digest: str
    capability: str
    expires_at: float

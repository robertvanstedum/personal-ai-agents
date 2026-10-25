"""Live key-scope checks and bounded replay/quota admission.

lookup must read the installed current profile, not a startup snapshot. Call
revalidate immediately before committing a mutation. Nonces are process-local
transport replay defense; domain operation receipts must provide durable effect
deduplication across processes and restarts.
"""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
import hashlib
import hmac
import re
import threading
import time

from .contract import MAX_REQUEST, KeyProfile, VerifiedRequest, require
from . import protocol


class KeyRegistry:
    """Atomically replace operator-provisioned profiles, rejecting shared secrets.

    No master-secret derivation, browser/session-key fallback, or automatic key
    creation. A file/config loader must validate and replace the whole set.
    """
    def __init__(self, profiles):
        self._lock = threading.RLock()
        self.replace(profiles)

    def replace(self, profiles):
        require(isinstance(profiles, (list, tuple)) and len(profiles) <= 256, 'key_registry_invalid')
        values, secrets = {}, set()
        for profile in profiles:
            require(isinstance(profile, KeyProfile), 'key_registry_invalid')
            fingerprint = hashlib.sha256(profile.secret).digest()
            require(profile.key_id not in values and fingerprint not in secrets, 'key_not_independent')
            values[profile.key_id] = profile
            secrets.add(fingerprint)
        with self._lock:
            self._profiles = values

    def lookup(self, key_id):
        with self._lock:
            return self._profiles.get(key_id)

    @contextmanager
    def admission(self):
        """Serialize operator replacement with an in-flight admitted transaction."""
        with self._lock:
            yield


class ReplayWindow:
    def __init__(self, *, capacity=512, max_keys=256, clock=time.monotonic):
        self.capacity, self.max_keys = capacity, max_keys
        self.clock = clock
        self._lock = threading.Lock()
        self._seen, self._rates = {}, {}

    def claim(self, profile, nonce):
        with self._lock:
            now = self.clock()
            self._seen = {key: until for key, until in self._seen.items() if until >= now}
            for key, times in list(self._rates.items()):
                while times and times[0] <= now - 60:
                    times.popleft()
                if not times:
                    del self._rates[key]
            key = (profile.key_id, nonce)
            require(key not in self._seen, 'nonce_replayed', 401)
            # Capacity is per key: one actor cannot consume another's share.
            require(sum(k[0] == profile.key_id for k in self._seen) < self.capacity, 'nonce_capacity', 429)
            require(profile.key_id in self._rates or len(self._rates) < self.max_keys, 'rate_capacity', 429)
            times = self._rates.get(profile.key_id, deque())
            require(len(times) < profile.requests_per_minute, 'rate_limited', 429)
            self._seen[key] = now + 33  # includes maximum future skew and the inclusive 30-second boundary
            times.append(now)
            self._rates[profile.key_id] = times


def _headers(headers):
    result = {}
    for key, value in headers.items():
        lower = key.lower()
        require(lower not in result and isinstance(value, str), 'headers_invalid')
        result[lower] = value
    return result


class Authenticator:
    def __init__(self, lookup, *, domain, environment, clock=time.time, replay=None, monotonic=None, startup_quarantine=0):
        from .contract import DOMAINS
        require(domain in DOMAINS and environment in {'dev', 'prod'}, 'installation_scope_invalid')
        self.lookup, self.domain, self.environment = lookup, domain, environment
        monotonic = time.monotonic if monotonic is None else monotonic
        self.clock, self.monotonic = clock, monotonic
        self._clock_lock = threading.Lock()
        self._wall_high_water = float('-inf')
        self._last_tick = monotonic()
        self._startup_until = self._last_tick + startup_quarantine
        self._quarantine = None
        self.replay = ReplayWindow() if replay is None else replay

    def _now(self):
        # Retain nonces across a clock correction. Resume only after one full
        # freshness window with a wall clock advancing alongside monotonic time.
        with self._clock_lock:
            tick, wall = self.monotonic(), self.clock()
            require(tick >= self._startup_until, 'startup_quarantine', 503)
            predicted = self._wall_high_water + max(0, tick - self._last_tick)
            self._last_tick = tick
            if predicted - wall > 32 or self._quarantine is not None:
                if self._quarantine is None:
                    self._quarantine = (tick, wall)
                started, first_wall = self._quarantine
                if abs((wall - first_wall) - (tick - started)) > 2:
                    self._quarantine = (tick, wall)
                    started = tick
                if tick - started < 33:
                    self._wall_high_water = max(predicted, wall)
                    require(False, 'clock_quarantine', 503)
                self._quarantine = None
                self._wall_high_water = wall
            else:
                self._wall_high_water = max(predicted, wall)
            return self._wall_high_water

    def verify(self, *, method, route, raw, headers, capability):
        values = _headers(headers)
        version, key_id, audience, environment, timestamp, nonce, operation, signature = (
            values.get(name.lower(), '') for name in protocol.HEADER_NAMES)
        require(version == protocol.VERSION and re.fullmatch(r'[0-9]{1,12}', timestamp)
                and protocol.NONCE.fullmatch(nonce) and protocol.SIGNATURE.fullmatch(signature),
                'authentication_required', 401)
        profile = self.lookup(key_id)
        require(isinstance(profile, KeyProfile) and profile.key_id == key_id, 'authentication_required', 401)
        require(isinstance(raw, bytes) and len(raw) <= MAX_REQUEST, 'authentication_required', 401)
        expected = protocol.request_signature(profile, method=method, route=route, raw=raw,
                                              timestamp=timestamp, nonce=nonce, operation=operation)
        require(hmac.compare_digest(signature, expected), 'authentication_required', 401)
        require((audience, environment) == (profile.domain, profile.environment) == (self.domain, self.environment),
                'scope_denied', 403)
        protocol.checked_body(method, route, raw, profile, operation)
        require(not values.get('content-encoding'), 'compression_refused', 400)
        if method in {'POST', 'PATCH'}:
            require(values.get('content-type', '').lower() in {'application/json', 'application/json; charset=utf-8'},
                    'json_required', 400)
        now = self._now()
        require(-2 <= now - int(timestamp) <= 30, 'signature_expired', 401)
        require(profile.enabled and capability in profile.capabilities, 'scope_denied', 403)
        self.replay.claim(profile, nonce)
        return VerifiedRequest(profile, method, route, nonce, operation, hashlib.sha256(raw).hexdigest(),
                               capability, int(timestamp) + 30)

    def revalidate(self, request, *, capability):
        """Fence a revoked key, changed identity/mapping or changed permission before mutation."""
        require(isinstance(request, VerifiedRequest), 'authentication_required', 401)
        require(request.capability == capability and self._now() <= request.expires_at, 'scope_denied', 403)
        current = self.lookup(request.profile.key_id)
        require(isinstance(current, KeyProfile) and current.enabled, 'scope_denied', 403)
        old = request.profile
        require((current.key_id, current.principal, current.actor, current.domain, current.environment,
                 current.numeric_user_id, current.capabilities) ==
                (old.key_id, old.principal, old.actor, old.domain, old.environment, old.numeric_user_id, old.capabilities)
                and (current.domain, current.environment) == (self.domain, self.environment)
                and hmac.compare_digest(current.secret, old.secret)
                and capability in current.capabilities, 'scope_denied', 403)
        return current

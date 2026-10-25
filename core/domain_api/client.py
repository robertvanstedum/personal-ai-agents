"""Fixed internal destination, bounded transport, authenticated response.

No URL/model-supplied destination, proxy inheritance, redirects, or retries.
The caller retains its UUID and reconciles an unknown outcome via read-back.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time

from .contract import DOMAINS, MAX_REQUEST, MAX_RESPONSE, DomainError, TransportUnknown, decode, packed, require
from . import protocol


@dataclass(frozen=True)
class Endpoint:
    domain: str
    host: str
    port: int
    tls: bool = False

    def __post_init__(self):
        require(self.domain in DOMAINS and type(self.port) is int and 1 <= self.port <= 65535
                and type(self.tls) is bool, 'endpoint_invalid')
        names = {self.domain, self.domain + '-dev', 'minimoi-' + self.domain,
                 'localhost', 'host.docker.internal'}
        internal = self.host in names if isinstance(self.host, str) else False
        if not internal:
            try:
                address = ipaddress.ip_address(self.host)
                if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
                    address = address.ipv4_mapped
                internal = address.is_loopback or (address.is_private and not (
                    address.is_link_local or address.is_multicast or address.is_unspecified or address.is_reserved))
                if isinstance(address, ipaddress.IPv6Address) and address in ipaddress.ip_network('fd00:ec2::/32'):
                    internal = False
            except ValueError:
                internal = False
        require(internal, 'endpoint_not_internal')


@dataclass(frozen=True)
class ReadReply:
    status: int
    body: dict


@dataclass(frozen=True)
class Verified:
    receipt: dict
    body: dict
    state: str = field(default='verified', init=False)


@dataclass(frozen=True)
class NotApplied:
    operation_id: str
    code: str
    state: str = field(default='not_applied', init=False)


@dataclass(frozen=True)
class Unknown:
    operation_id: str
    state: str = field(default='unknown', init=False)


_RESOLVER_SLOTS = threading.BoundedSemaphore(4)


def _connect(endpoint, deadline):
    # getaddrinfo cannot be cancelled portably. At most four daemon workers
    # may remain stuck; further calls fail closed rather than queue or leak
    # unlimited threads. The caller's single deadline includes DNS and TLS.
    require(_RESOLVER_SLOTS.acquire(blocking=False), 'resolver_busy')
    complete, result = threading.Event(), []
    def resolve():
        try:
            result.extend(socket.getaddrinfo(endpoint.host, endpoint.port, type=socket.SOCK_STREAM))
        except OSError:
            pass
        finally:
            _RESOLVER_SLOTS.release()
            complete.set()
    worker = threading.Thread(target=resolve, daemon=True)
    try:
        worker.start()
    except Exception:
        _RESOLVER_SLOTS.release()
        raise
    require(complete.wait(max(0, deadline - time.monotonic())) and result, 'resolution_failed')
    # Validate all answers, then connect directly to those IPs: no second DNS
    # lookup between validation and connect, including TLS/SNI connections.
    for _, _, _, _, address in result:
        Endpoint(endpoint.domain, address[0], endpoint.port)
    for family, kind, proto, _, address in result:
        remaining = deadline - time.monotonic()
        require(remaining > 0, 'connection_deadline')
        sock = socket.socket(family, kind, proto)
        try:
            sock.settimeout(remaining)
            sock.connect(address)
            if endpoint.tls:
                context = ssl.create_default_context()
                remaining = deadline - time.monotonic()
                require(remaining > 0, 'connection_deadline')
                sock.settimeout(remaining)
                sock = context.wrap_socket(sock, server_hostname=endpoint.host)
            require(time.monotonic() <= deadline, 'connection_deadline')
            return sock
        except Exception:
            sock.close()
    raise DomainError('connection_failed')


def _verified_receipt(body, *, profile, operation, capability, digest):
    receipt = body.get('receipt')
    require(type(receipt) is dict, 'receipt_missing')
    require(receipt.get('schema') == 'minimoi.domain-operation/1'
            and receipt.get('state') == 'verified'
            and receipt.get('operation_id') == operation
            and receipt.get('request_digest') == digest
            and receipt.get('domain') == profile.domain
            and receipt.get('actor') == profile.actor
            and receipt.get('capability') == capability.removeprefix(profile.domain + '.'), 'receipt_mismatch')
    require(type(receipt.get('target_revision')) is int and receipt['target_revision'] > 0
            and type(receipt.get('replayed')) is bool, 'receipt_invalid')
    for key in ('result', 'target_ref', 'recorded_at', 'content_state'):
        require(isinstance(receipt.get(key), str) and 0 < len(receipt[key]) <= 512
                and not any(ord(c) < 32 for c in receipt[key]), 'receipt_invalid')
    require(receipt['target_ref'].startswith(profile.domain + ':'), 'receipt_invalid')
    recorded = datetime.fromisoformat(receipt['recorded_at'].replace('Z', '+00:00'))
    require(recorded.tzinfo is not None and recorded.utcoffset() == timezone.utc.utcoffset(recorded), 'receipt_invalid')
    undo = receipt.get('undo')
    require(type(undo) is dict and type(undo.get('available')) is bool, 'receipt_invalid')
    if undo['available']:
        require(undo.get('operation_ref') == operation, 'receipt_invalid')
    return receipt


def _exchange(endpoint, method, route, raw, headers):
    """2-second total DNS/connect/TLS and 5-second post-connect deadline.

    The watchdog shuts down the connected socket, including while HTTP headers
    are trickling in. A per-read inactivity timeout alone is not a total bound.
    http.client never reads HTTP_PROXY or follows Location headers.
    """
    conn = http.client.HTTPConnection(endpoint.host, endpoint.port, timeout=2)
    timer = None
    try:
        # TLS, if configured, is already established by _connect; HTTPConnection
        # only serializes HTTP on the supplied socket and preserves Host.
        conn.sock = _connect(endpoint, time.monotonic() + 2)
        sock = conn.sock
        sock.settimeout(5)
        deadline = time.monotonic() + 5
        def abort():
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        timer = threading.Timer(5, abort)
        timer.daemon = True
        timer.start()
        conn.request(method, route, body=raw, headers=headers)
        response = conn.getresponse()
        require(not response.getheader('Content-Encoding'), 'compressed_response')
        require((response.getheader('Content-Type') or '').lower() in
                {'application/json', 'application/json; charset=utf-8'}, 'response_type_invalid')
        content_length = response.getheader('Content-Length')
        if content_length is not None:
            require(content_length.isascii() and content_length.isdigit() and int(content_length) <= MAX_RESPONSE,
                    'response_bound')
        body = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            require(remaining > 0, 'response_deadline')
            sock.settimeout(remaining)
            # Reading one byte bounds a hostile slow stream even without a length.
            part = response.read(1)
            if not part:
                break
            body.extend(part)
            require(len(body) <= MAX_RESPONSE, 'response_bound')
            if response.isclosed():
                break
        if content_length is not None:
            require(len(body) == int(content_length), 'response_truncated')
        require(time.monotonic() <= deadline, 'response_deadline')
        return response.status, bytes(body), response.getheader('X-MiniMoi-Receipt') or ''
    finally:
        if timer is not None:
            timer.cancel()
        conn.close()


class DomainClient:
    def __init__(self, profile, endpoint, *, clock=time.time, exchange=_exchange):
        require(profile.domain == endpoint.domain and profile.enabled, 'client_scope_invalid')
        self.profile, self.endpoint, self.clock, self.exchange = profile, endpoint, clock, exchange

    def submit(self, route, value, *, operation_id, capability, expected_request_digest):
        """Return exactly Verified, NotApplied or Unknown after transport.

        The trusted domain adapter supplies the digest of canonical *validated*
        values, including defaults/normalization. It is NOT necessarily the raw
        wire-body hash. Never take this expectation from a returned receipt or
        from the model. Local validation failures still raise DomainError before
        any send; a timeout never causes a retry or a new operation UUID.
        """
        require(type(value) is dict, 'json_object_required')
        require(capability in self.profile.capabilities, 'scope_denied', 403)
        require(isinstance(expected_request_digest, str)
                and re.fullmatch(r'[a-f0-9]{64}', expected_request_digest), 'expected_digest_invalid')
        raw = packed(value)
        headers = self._prepare('POST', route, raw, operation_id)
        try:
            reply = self._send('POST', route, raw, headers)
            if reply.status >= 400:
                if reply.body['state'] == 'unknown':
                    return Unknown(operation_id)
                return NotApplied(operation_id, reply.body['error'])
            receipt = _verified_receipt(reply.body, profile=self.profile, operation=operation_id,
                                        capability=capability, digest=expected_request_digest)
            return Verified(receipt, reply.body)
        except Exception:
            return Unknown(operation_id)

    def read(self, route):
        return self._send('GET', route, b'', self._prepare('GET', route, b'', ''))

    def _prepare(self, method, route, raw, operation):
        # Validation errors are local no-effect refusals. Once transport starts,
        # even an unsigned 4xx/5xx cannot establish whether a write happened.
        require(len(raw) <= MAX_REQUEST, 'body_bound', 413)
        return protocol.request_headers(self.profile, method=method, route=route, raw=raw,
                                        operation=operation, now=self.clock())

    def _send(self, method, route, raw, headers):
        try:
            status, body, signature = self.exchange(self.endpoint, method, route, raw, headers)
            require(type(status) is int and not 300 <= status < 400, 'redirect_refused')
            protocol.verify_response(self.profile, nonce=headers['X-MiniMoi-Nonce'], status=status,
                                     raw=body, signature=signature)
            value = decode(body, maximum=MAX_RESPONSE)
            if status >= 400:
                require(set(value) == {'error', 'state'} and isinstance(value['error'], str)
                        and 1 <= len(value['error']) <= 80 and value['error'].isascii()
                        and all(c.isalnum() or c == '_' for c in value['error'])
                        and value['state'] in {'not_applied', 'unknown'}, 'error_response_invalid')
                # Only a signed response can assert a specific refusal state.
                if value['state'] == 'unknown':
                    raise TransportUnknown()
                return ReadReply(status, value)
            require(status in {200, 201}, 'status_unconfirmed')
            return ReadReply(status, value)
        except Exception:
            raise TransportUnknown() from None

"""HTTP clients for the Rooms worker: Records (two identities) and the MC relay.

Records: plain HTTP on the internal records network, Host minimoi-records:18880,
a Bearer credential and no Origin header (Records' guard rule). Two identities:
the worker's work-scoped credential for turn routes, and MC's own
membership-scoped credential for reading, RSVP and posting MC's reply.

Relay: the one-way MC relay on mc-front, with its caller token. Only
POST /v1/chat/completions (streaming), POST /v1/turns/stop and GET /readyz.

No token value is ever logged or returned.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

import requests


class Unavailable(RuntimeError):
    """The other side did not answer; the outcome of a write is unknown."""


def read_secret(path):
    p = Path(path)
    if p.is_symlink():
        raise RuntimeError(f"{p.name} must not be a symlink")
    value = p.read_text().strip()
    if len(value) < 16:
        raise RuntimeError(f"{p.name} does not hold a credential")
    return value


class Records:
    def __init__(self, base_url, token, *, session=None, timeout=15):
        self.base = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.trust_env = False

    def call(self, method, path, body=None, key=None, missing_ok=False):
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        if method != "GET":
            headers["Content-Type"] = "application/json"
            if key:
                headers["Idempotency-Key"] = key
        try:
            r = self.session.request(method, self.base + "/api/v1" + path, headers=headers,
                                     data=json.dumps(body) if body is not None else None,
                                     timeout=self.timeout, allow_redirects=False)
        except requests.RequestException as error:
            raise Unavailable(type(error).__name__) from None
        try:
            data = r.json()
        except ValueError:
            data = {}
        if missing_ok and r.status_code == 404:
            return 404, None
        return r.status_code, data

    def operation(self, key, destination):
        """Receipt lookup: (True, response) committed, (False, None) confirmed absent."""
        status, data = self.call("GET", "/operations/" + quote(key, safe="") + "?destination=" + quote(destination, safe=""),
                                 missing_ok=True)
        if status == 200:
            return True, data
        if status == 404:
            return False, None
        raise Unavailable(f"receipt lookup answered {status}")


class Relay:
    """The MC relay contract (docker/mc-agent/relay.mjs)."""

    MODEL = "openclaw/mc-agent"

    def __init__(self, base_url, token, *, session=None):
        self.base = base_url.rstrip("/")
        self.token = token
        self.session = session or requests.Session()
        self.session.trust_env = False

    def _headers(self, correlation=None):
        h = {"Authorization": "Bearer " + self.token, "Content-Type": "application/json"}
        if correlation:
            h["X-MC-Correlation-Id"] = correlation
        return h

    def ready(self):
        try:
            r = self.session.get(self.base + "/readyz", headers=self._headers(), timeout=10, allow_redirects=False)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def stop(self, correlation):
        try:
            r = self.session.post(self.base + "/v1/turns/stop", headers=self._headers(),
                                  data=json.dumps({"correlation_id": correlation}), timeout=10)
            return r.status_code
        except requests.RequestException:
            return 0

    def stream(self, messages, user, correlation, on_open=None):
        """One streamed turn. Returns a dict:
        {"outcome": "done"|"busy"|"refused"|"stopped"|"error", "text": str,
         "usage": {"prompt_tokens", "completion_tokens"} or None, "detail": str}"""
        body = {"model": self.MODEL, "stream": True, "user": user, "messages": messages}
        try:
            r = self.session.post(self.base + "/v1/chat/completions", headers=self._headers(correlation),
                                  data=json.dumps(body), stream=True, timeout=(5, 60), allow_redirects=False)
        except requests.RequestException as error:
            return {"outcome": "error", "text": "", "usage": None, "detail": type(error).__name__}
        with r:
            if r.status_code == 429:
                return {"outcome": "busy", "text": "", "usage": None, "detail": "relay_busy"}
            if r.status_code == 409:
                return {"outcome": "stopped", "text": "", "usage": None, "detail": "relay_stopped"}
            if r.status_code != 200:
                kind = "refused" if 400 <= r.status_code < 500 else "error"
                return {"outcome": kind, "text": "", "usage": None, "detail": f"http_{r.status_code}"}
            if on_open:
                on_open()
            parts, usage, failure = [], None, None
            try:
                for line in r.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    kind = event.get("t")
                    if kind == "delta" and isinstance(event.get("text"), str):
                        parts.append(event["text"])
                    elif kind == "usage":
                        usage = {k: event.get(k) for k in ("prompt_tokens", "completion_tokens")}
                    elif kind == "error":
                        failure = str(event.get("class") or "upstream")
            except requests.RequestException as error:
                failure = failure or type(error).__name__
            text = "".join(parts)
            if failure == "stopped":
                return {"outcome": "stopped", "text": text, "usage": usage, "detail": "stopped"}
            if failure:
                return {"outcome": "error", "text": text, "usage": usage, "detail": failure}
            return {"outcome": "done", "text": text, "usage": usage, "detail": "done"}

"""The retrieval hook MC and CoS use to ask the Vault (V3): one authenticated call to the existing Ask service, rendered as bounded DATA.

It adds no authority. Who may read what is decided by the Ask service from the caller's bearer token (the grant: scopes, tiers, rate); this hook cannot widen it, cannot
ask for the raw tier (it never sends ``include_raw``), and cannot choose another caller. It does not open the shelf, the index or any file, and it never raises: a missing
setting, a refused call, a timeout or a damaged answer all come back as a short "unavailable" block with a fixed code, so a turn carries on without the evidence.

Off unless configured (``MINIMOI_VAULT_ASK_URL`` and ``MINIMOI_VAULT_ASK_TOKEN``). The URL must have no embedded credentials and a host that is loopback (http or https) or in
``MINIMOI_VAULT_ASK_HOSTS`` (comma list) and then ONLY https; redirects are never followed; the token goes only in the Authorization header and is never logged or placed in a block.
The block is the reviewed ``context.build`` output: keyword and meaning lists kept apart, whole passages omitted when they do not fit, only retained ranges citable.
Where the Ask service runs and how a cloud-hosted CoS reaches it is a deployment decision, not made here.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from core.memory_ask import context

ENV_URL, ENV_TOKEN, ENV_HOSTS = "MINIMOI_VAULT_ASK_URL", "MINIMOI_VAULT_ASK_TOKEN", "MINIMOI_VAULT_ASK_HOSTS"
MAX_QUERY_CHARS = 1000
MAX_K = 5
MAX_RESPONSE_BYTES = 256 * 1024
TIMEOUT_S = 8.0
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_HTTP_CODES = {400: "bad_arguments", 401: "unauthorized", 403: "tier_not_permitted", 413: "too_large", 415: "unsupported_media_type", 429: "rate_limited", 503: "registry_unavailable"}


@dataclass(frozen=True)
class Config:
    base_url: str = ""
    token: str = field(default="", repr=False)                 # never shown in a repr, a log or a block
    allow_hosts: tuple[str, ...] = ()
    k: int = MAX_K
    timeout_s: float = TIMEOUT_S

    @classmethod
    def from_env(cls, environ=None) -> "Config":
        environ = os.environ if environ is None else environ
        hosts = tuple(h.strip().lower() for h in environ.get(ENV_HOSTS, "").split(",") if h.strip())
        return cls(environ.get(ENV_URL, "").strip(), environ.get(ENV_TOKEN, "").strip(), hosts)


@dataclass(frozen=True)
class Retrieved:
    status: str                                                # ok | disabled | unavailable
    code: str                                                  # "" or a fixed code (one of context.ERRORS, or disabled/bad_config)
    built: object = None                                       # context.Built, or None when nothing was asked

    @property
    def prompt_block(self) -> str | None:
        """The text to put in front of the model, or None when there is nothing to say (disabled)."""
        return self.built.block if self.built is not None else None

    @property
    def report(self) -> dict:
        return {**(self.built.report if self.built is not None else {}), "hook_status": self.status, "code": self.code}


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def endpoint(config: Config) -> str | None:
    """The validated ask URL, or None if the settings are missing or unsafe. Never raises, whatever the settings contain."""
    try:
        if not isinstance(config.base_url, str) or not isinstance(config.token, str) or not config.base_url or not config.token:
            return None
        parts = urllib.parse.urlsplit(config.base_url)
        host = (parts.hostname or "").lower()
        _ = parts.port                                             # raises ValueError on a malformed port
        if parts.scheme not in ("http", "https") or parts.username or parts.password or parts.query or parts.fragment or not host:
            return None
        if _loopback(host):
            pass                                                   # a local bridge may be plain http
        elif host in tuple(config.allow_hosts) and parts.scheme == "https":
            pass                                                   # a remote host must be allow-listed AND https
        else:
            return None
        return config.base_url.rstrip("/") + "/v1/ask"
    except (ValueError, TypeError, AttributeError):
        return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):                       # a redirect could carry the token elsewhere: refuse
        return None


def default_post(url: str, headers: dict, body: bytes, timeout: float) -> tuple[int, bytes]:
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(MAX_RESPONSE_BYTES + 1)


def _unavailable(code: str) -> Retrieved:
    shown = code if isinstance(code, str) and code in context.ERRORS else "unavailable"
    return Retrieved("unavailable", code if isinstance(code, str) else "unavailable", context.build({"error": shown}))


def retrieve(query, config: Config | None = None, *, post=default_post, max_chars=context.MAX_CHARS, max_bytes=context.MAX_BYTES) -> Retrieved:
    """Ask the Vault on the caller's behalf and return the bounded DATA block. Never raises: every step, including reading the settings, is inside the boundary.
    Not configured at all -> ``disabled`` (nothing is said); configured but unusable or unsafe -> ``unavailable`` with code ``bad_config``."""
    try:
        config = config or Config.from_env()
        configured = bool(getattr(config, "base_url", "") or getattr(config, "token", ""))
        url = endpoint(config)
        if url is None:
            return _unavailable("bad_config") if configured else Retrieved("disabled", "disabled")
        if not isinstance(query, str):
            return _unavailable("bad_arguments")
        text = _CONTROL.sub(" ", query).strip()
        if not text:
            return _unavailable("empty_query")
        text = text[:MAX_QUERY_CHARS]
        try:
            k = max(1, min(int(config.k), MAX_K))
        except (TypeError, ValueError, OverflowError):
            return _unavailable("bad_k")
        timeout = float(config.timeout_s) if isinstance(config.timeout_s, (int, float)) and not isinstance(config.timeout_s, bool) and 0 < config.timeout_s <= 60 else TIMEOUT_S
        body = json.dumps({"query": text, "k": k}).encode()      # no include_raw: this hook can only read what a curated caller may
        headers = {"Authorization": f"Bearer {config.token}", "Content-Type": "application/json", "Accept": "application/json"}
        try:
            status, raw = post(url, headers, body, timeout)
        except Exception:                                      # noqa: BLE001 - timeout, refused connection, TLS: one fixed code
            return _unavailable("unavailable")
        if not isinstance(raw, (bytes, bytearray)) or not isinstance(status, int):
            return _unavailable("unavailable")
        if status != 200:
            code = None
            if raw and len(raw) <= MAX_RESPONSE_BYTES:
                try:
                    err = json.loads(bytes(raw).decode("utf-8")).get("error")
                    code = err if isinstance(err, str) and err in context.ERRORS else None          # a dict or list "error" is not a code (and is not hashable)
                except (ValueError, UnicodeDecodeError, AttributeError):
                    code = None
            return _unavailable(code or _HTTP_CODES.get(status, "unavailable"))
        if len(raw) > MAX_RESPONSE_BYTES:
            return _unavailable("too_large")
        try:
            result = json.loads(bytes(raw).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return _unavailable("unavailable")
        built = context.build(result, max_chars=max_chars, max_bytes=max_bytes)
        if built.report.get("status") == "unavailable":        # a damaged or error-shaped answer: the block says so; the hook status agrees
            return Retrieved("unavailable", "unavailable", built)
        return Retrieved("ok", "", built)
    except Exception:                                          # noqa: BLE001 - the hook must never take a turn down
        return _unavailable("unavailable")

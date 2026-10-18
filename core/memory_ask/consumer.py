"""The retrieval hook MC and CoS use to ask the Vault (V3): one authenticated call to the existing Ask service, rendered as bounded DATA.

It adds no authority. Who may read what is decided by the Ask service from the caller's bearer token (the grant: scopes, tiers, rate); this hook cannot widen it
(it asks for the raw tier only when the deployment sets ``MINIMOI_VAULT_ASK_INCLUDE_RAW=1``, and the grant still refuses it unless it holds that tier), and
cannot choose another caller. It does not open the shelf, the index or any file, and it never raises: a missing
setting, a refused call, a timeout or a damaged answer all come back as a short "unavailable" block with a fixed code, so a turn carries on without the evidence.

Off unless configured (``MINIMOI_VAULT_ASK_URL`` and ``MINIMOI_VAULT_ASK_TOKEN``). The URL must have no embedded credentials and a host that is loopback (http or https),
in ``MINIMOI_VAULT_ASK_HOSTS`` (comma list) and then ONLY https, or named exactly in ``MINIMOI_VAULT_ASK_INTERNAL_HOSTS`` (comma list of single-label container names on
an internal Compose network, never a dotted or IP host) where plain http is allowed; redirects are never followed; the token goes only in the Authorization header and
is never logged or placed in a block.

``MINIMOI_VAULT_ASK_INCLUDE_RAW=1`` (owner decision, 17 Oct 2026: production MC searches the full Vault on on-record turns) makes the hook ask for the raw tier. The
Ask service still decides: a grant without the raw tier refuses it (403), so this setting can never widen a grant.
The block is the reviewed ``context.build`` output: keyword and meaning lists kept apart, whole passages omitted when they do not fit, only retained ranges citable.
Where the Ask service runs and how a cloud-hosted CoS reaches it is a deployment decision, not made here.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import urllib.parse
import time
from dataclasses import dataclass, field

from core.memory_ask import context

ENV_URL, ENV_TOKEN, ENV_HOSTS = "MINIMOI_VAULT_ASK_URL", "MINIMOI_VAULT_ASK_TOKEN", "MINIMOI_VAULT_ASK_HOSTS"
ENV_INTERNAL_HOSTS, ENV_INCLUDE_RAW = "MINIMOI_VAULT_ASK_INTERNAL_HOSTS", "MINIMOI_VAULT_ASK_INCLUDE_RAW"
# Needs a letter (an all-digit name can resolve as an IP) and is never a hex number ("0x7f000001" also resolves).
_CONTAINER_NAME = re.compile(r"(?!0x[0-9a-f]+$)(?=[a-z0-9_-]*[a-z])[a-z0-9][a-z0-9_-]{0,62}")
MAX_QUERY_CHARS = 1000
MAX_K = 5
MAX_RESPONSE_BYTES = 256 * 1024
TIMEOUT_S = 8.0
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_HTTP_CODES = {400: "bad_arguments", 401: "unauthorized", 403: "unavailable", 413: "too_large", 415: "unsupported_media_type", 429: "rate_limited", 503: "unavailable"}


@dataclass(frozen=True)
class Config:
    base_url: str = ""
    token: str = field(default="", repr=False)                 # never shown in a repr, a log or a block
    allow_hosts: tuple[str, ...] = ()
    k: int = MAX_K
    timeout_s: float = TIMEOUT_S
    internal_hosts: tuple[str, ...] = ()
    include_raw: bool = False

    @classmethod
    def from_env(cls, environ=None) -> "Config":
        environ = os.environ if environ is None else environ
        hosts = tuple(h.strip().lower() for h in environ.get(ENV_HOSTS, "").split(",") if h.strip())
        internal = tuple(h.strip().lower() for h in environ.get(ENV_INTERNAL_HOSTS, "").split(",") if h.strip())
        return cls(environ.get(ENV_URL, "").strip(), environ.get(ENV_TOKEN, "").strip(), hosts,
                   internal_hosts=internal, include_raw=environ.get(ENV_INCLUDE_RAW, "") == "1")


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
        elif host in tuple(config.internal_hosts) and _CONTAINER_NAME.fullmatch(host):
            pass                                                   # a named container on an internal Compose network: never a dotted/IP host
        else:
            return None
        return config.base_url.rstrip("/") + "/v1/ask"
    except (ValueError, TypeError, AttributeError):
        return None


def default_post(url: str, headers: dict, body: bytes, timeout: float) -> tuple[int, bytes]:
    """One per-call session, no ambient proxies/cookies and no redirects.

    Admission and streamed progress share one elapsed budget. A blocking DNS/
    socket operation may return after that budget; such a result is refused,
    never accepted as timely. No background thread or automatic retry is used.
    Requests is already a dependency of the CoS/portal runtime; load it only
    when transport is actually needed, preserving dependency-free fake hooks.
    """
    import requests
    deadline = time.monotonic() + timeout
    session = requests.Session()
    session.trust_env = False
    def remaining():
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("vault_transport_deadline")
        return left
    try:
        left = remaining()
        connect = min(2.0, left / 2)
        with session.post(url, data=body, headers=headers,
                          timeout=(connect, left - connect),
                          allow_redirects=False, stream=True) as response:
            remaining()
            if 300 <= response.status_code < 400:
                return response.status_code, b""
            raw = bytearray()
            # Single-byte progress exposes a slowly trickling response to the
            # elapsed guard instead of waiting for a large buffered chunk.
            for chunk in response.iter_content(chunk_size=1):
                remaining()
                if not chunk:
                    continue
                raw.extend(chunk[:MAX_RESPONSE_BYTES + 1 - len(raw)])
                if len(raw) > MAX_RESPONSE_BYTES:
                    return response.status_code, bytes(raw)
            remaining()
            return response.status_code, bytes(raw)
    finally:
        session.close()


_MONTHS = {name: n for n, names in enumerate((
    ("january", "jan", "januar"), ("february", "feb", "februar"), ("march", "mar", "märz", "maerz"), ("april", "apr"),
    ("may", "mai"), ("june", "jun", "juni"), ("july", "jul", "juli"), ("august", "aug"), ("september", "sep", "sept"),
    ("october", "oct", "oktober", "okt"), ("november", "nov"), ("december", "dec", "dezember", "dez")), 1) for name in names}
_MONTH_WORD = r"(?P<m>[a-zäöü]+)\.?(?:\s+(?P<y>20\d\d)(?!-\d))?"
_ISO = re.compile(r"\b(?P<y>20\d\d)-(?P<mm>0[1-9]|1[0-2])\b")
_CLOSED = re.compile(r"\b(?:in|im|during|over|across|for)\s+(?:(?:the\s+)?(?:month\s+of\s+))?" + _MONTH_WORD, re.I)
_OPEN = re.compile(r"\b(?P<kw>since|from|seit|ab|von)\s+" + _MONTH_WORD, re.I)   # "from"/"von" only as a range
_WITH_YEAR = re.compile(r"\b(?P<m>[a-zäöü]+)\.?\s+(?P<y>20\d\d)(?!-\d)\b", re.I)
_JOINED = re.compile(r"\s*(?:,|or|and|to|until|bis|und|oder|-|–)\s*" + _MONTH_WORD, re.I)


def _resolve(name: str, year: str | None, today):
    month = _MONTHS.get((name or "").lower())
    if month is None:
        return None
    y = int(year) if year else (today.year if month <= today.month else today.year - 1)
    return (y, month)


def date_window(text: str, today=None) -> tuple[str, str | None] | None:
    """The period a question names, as the Ask service's ``created_after`` (inclusive) / ``created_before`` (EXCLUSIVE)
    dates, or None.

    "what were we doing in May?" -> May (May 1 to June 1, exclusive), "im Mai 2026", "2026-05", "in Sep or Oct" -> both
    months, "since March" / "seit März" -> March 1 onward (no end). A month without a year is the most recent one not
    after today; a month still in the future is ignored. A bare month word counts only after in/im/during/... or
    since/from/seit, or with a year ("I may need help" and "Any word from Jan?" asks nothing of "Jan" unless it is
    such a phrase). Created date is the conversation's start: a conversation begun earlier and continued in that month
    is not inside the window (a disclosed limit)."""
    from datetime import date
    today = today or date.today()
    text = text or ""
    months, open_from = [], None
    for m in _ISO.finditer(text):
        months.append((int(m.group("y")), int(m.group("mm"))))
    for pattern, is_open in ((_CLOSED, False), (_OPEN, True), (_WITH_YEAR, False)):
        for m in pattern.finditer(text):
            first = _resolve(m.group("m"), m.group("y"), today)
            if first is None:
                continue
            found = [first]
            tail = text[m.end():]
            while True:                                         # "in Sep or Oct", "from March to May"
                j = _JOINED.match(tail)
                nxt = _resolve(j.group("m"), j.group("y"), today) if j else None
                if nxt is None:
                    break
                found.append(nxt)
                tail = tail[j.end():]
            if is_open and len(found) == 1:
                if m.group("kw").lower() in ("from", "von"):    # "from Jan" is as often a person: only "from X to Y" counts
                    continue
                open_from = min(open_from or found[0], found[0])
            else:
                months.extend(found)
    months = [ym for ym in months if date(ym[0], ym[1], 1) <= today]
    if open_from and date(open_from[0], open_from[1], 1) <= today:
        return date(open_from[0], open_from[1], 1).isoformat(), None
    if not months:
        return None
    lo, hi = min(months), max(months)
    end = date(hi[0] + (hi[1] == 12), hi[1] % 12 + 1, 1)
    return date(lo[0], lo[1], 1).isoformat(), end.isoformat()


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
        ask = {"query": text, "k": k}
        window = date_window(text)                             # "what were we doing in May?" asks that month, not the word "may"
        if window:
            ask["created_after"] = window[0]
            if window[1]:
                ask["created_before"] = window[1]               # exclusive: the first day after the period
        if getattr(config, "include_raw", False) is True:       # the owner's deployment setting; the service still enforces the grant
            ask["include_raw"] = True
        body = json.dumps(ask).encode()
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

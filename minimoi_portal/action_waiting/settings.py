"""The service's settings, parsed once from the process environment when the portal starts (spec v0.2 §3.9).

Nothing here reads a file or a secret: the bot token is read only by the sender, from the file this names.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

MODES = ("off", "record", "on")
PRODUCTION_HOSTS = frozenset({"minimoi.ai", "www.minimoi.ai"})
_AGENT = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

# Constants (spec §3.9).
DECISION_GRACE_S = 180
AGENT_STALE_S = 72 * 3600
AGENT_OPEN_CAP = 5
GROUP_MAX = 10
BACKOFF_FIRST_S = 30
BACKOFF_MAX_S = 30 * 60
DEBOUNCE_S = 5
RESOLVED_KEEP_S = 30 * 86400
RESET_TTL_S = 48 * 3600
MESSAGE_MAX = 3500


def _int(value, default, lo=None, hi=None):
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n


def _float(value, default):
    try:
        n = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    return n if n >= 0 else default


def parse_agent_tokens(raw) -> dict:
    """``claude-code:<sha256>,codex:<sha256>`` → {sha256: agent}. Malformed entries are dropped, never guessed."""
    out = {}
    for part in str(raw or "").split(","):
        agent, _, digest = part.strip().partition(":")
        digest = digest.strip().lower()
        if _AGENT.match(agent) and _HEX64.match(digest):
            out[digest] = agent
    return out


def origin_host(base_url) -> str:
    try:
        return (urlsplit(str(base_url or "")).hostname or "").lower()
    except ValueError:
        return ""


@dataclass(frozen=True)
class Settings:
    mode: str = "off"
    env: str | None = None
    root: Path | None = None
    token_file: str | None = None
    chat_id: int | None = None
    bot_id: int | None = None
    owner: str | None = None
    agent_tokens: dict = field(default_factory=dict)
    remind_hours: float = 24.0
    sweep_seconds: int = 60
    words_on_telegram: bool = False
    base_url: str = ""
    supervision: bool = False

    @property
    def enabled(self) -> bool:
        return self.mode in ("record", "on")

    @property
    def label(self) -> str:
        """``[dev] `` in front of every message unless this is production."""
        return "" if self.env == "prod" else "[dev] "

    def env_agrees(self) -> bool:
        """The declared environment matches the origin (Codex S4): prod only on minimoi.ai, dev never there."""
        host = origin_host(self.base_url)
        if self.env == "prod":
            return host in PRODUCTION_HOSTS
        if self.env == "dev":
            return bool(host) and host not in PRODUCTION_HOSTS
        return False

    @classmethod
    def from_environ(cls, environ, *, base_url: str = "") -> "Settings":
        mode = str(environ.get("MINIMOI_ACTION_NOTICES", "off") or "off").strip().lower()
        if mode not in MODES:
            mode = "off"
        root = str(environ.get("MINIMOI_ACTION_NOTICES_DIR", "") or "").strip()
        env = str(environ.get("MINIMOI_ACTION_NOTICES_ENV", "") or "").strip().lower() or None
        chat = str(environ.get("MINIMOI_ACTION_NOTICES_CHAT_ID", "") or "").strip()
        bot_id = str(environ.get("MINIMOI_ACTION_NOTICES_BOT_ID", "") or "").strip()
        return cls(
            mode=mode,
            env=env if env in ("dev", "prod") else None,
            root=Path(root) if root else None,
            token_file=str(environ.get("MINIMOI_ACTION_NOTICES_BOT_TOKEN_FILE", "") or "").strip() or None,
            chat_id=int(chat) if re.fullmatch(r"-?\d{1,20}", chat) else None,
            bot_id=int(bot_id) if re.fullmatch(r"\d{5,15}", bot_id) else None,
            owner=str(environ.get("MINIMOI_ACTION_NOTICES_OWNER", "") or "").strip() or None,
            agent_tokens=parse_agent_tokens(environ.get("MINIMOI_ACTION_NOTICES_AGENT_TOKENS")),
            remind_hours=_float(environ.get("MINIMOI_ACTION_NOTICES_REMIND_HOURS", "24"), 24.0),
            sweep_seconds=_int(environ.get("MINIMOI_ACTION_NOTICES_SWEEP_SECONDS", "60"), 60, 15, 600),
            words_on_telegram=str(environ.get("MINIMOI_ACTION_NOTICES_WORDS_ON_TELEGRAM", "")).strip() == "1",
            base_url=str(base_url or environ.get("BASE_URL", "") or "").rstrip("/"),
            supervision=environ.get("MINIMOI_SUPERVISION") == "1" and bool(environ.get("DATABASE_URL")),
        )

"""Short-lived server-side handoff from canonical CoS text to speech output."""

from dataclasses import dataclass
import threading
import time


@dataclass(frozen=True)
class SpokenReply:
    text: str
    provider: str
    user_id: str
    expires_at: float


class SpokenReplyStore:
    """Keep generated reply text off the browser-to-TTS request surface."""

    def __init__(self, *, ttl_seconds: int = 120, max_entries: int = 50) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._items: dict[str, SpokenReply] = {}
        self._lock = threading.Lock()

    def put(self, turn_id: str, *, text: str, provider: str, user_id: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._discard_expired(now)
            if len(self._items) >= self._max_entries:
                oldest = min(self._items, key=lambda key: self._items[key].expires_at)
                self._items.pop(oldest, None)
            self._items[turn_id] = SpokenReply(
                text=text,
                provider=provider,
                user_id=str(user_id),
                expires_at=now + self._ttl_seconds,
            )

    def get(self, turn_id: str, *, user_id: str) -> SpokenReply | None:
        now = time.monotonic()
        with self._lock:
            self._discard_expired(now)
            reply = self._items.get(turn_id)
            if reply is None or reply.user_id != str(user_id):
                return None
            return reply

    def _discard_expired(self, now: float) -> None:
        expired = [
            turn_id
            for turn_id, reply in self._items.items()
            if reply.expires_at <= now
        ]
        for turn_id in expired:
            self._items.pop(turn_id, None)

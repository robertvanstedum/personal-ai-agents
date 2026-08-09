"""Channel-neutral application boundary for Chief of Staff Confer turns.

HTML typing, HTML voice, Telegram text, Telegram voice, and internal API calls
all arrive here as text. The service owns channel normalization and the stable
turn result shape; the configured CoS backend still owns reasoning and memory-
worthiness judgment through the existing ``call_backend`` contract.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4


ALLOWED_CHANNELS = frozenset({
    "api_text",
    "html_text",
    "html_voice",
    "telegram_text",
    "telegram_voice",
})


class InvalidConferTurn(ValueError):
    """Raised when a caller supplies an empty turn or unknown channel."""


@dataclass(frozen=True)
class ConferTurnRequest:
    text: str
    channel: str
    conversation_id: str = "owner"


@dataclass(frozen=True)
class ConferTurnResult:
    turn_id: str
    conversation_id: str
    channel: str
    user_text: str
    reply: str
    backend_label: str
    model_label: str
    created_at: str

    def public_dict(self) -> dict:
        """Return response metadata without echoing the user's full input."""
        return {
            "turn_id": self.turn_id,
            "conversation_id": self.conversation_id,
            "channel": self.channel,
            "reply": self.reply,
            "backend_label": self.backend_label,
            "model_label": self.model_label,
            "created_at": self.created_at,
        }


class ConferTurnService:
    """Route one normalized turn through the permanent CoS coordination layer."""

    def __init__(
        self,
        *,
        call_backend: Callable[[str, dict, dict], str],
        build_context: Callable[[], dict],
        increment_chat: Callable[[], None],
        backend_metadata: Callable[[], tuple[str, str]],
    ) -> None:
        self._call_backend = call_backend
        self._build_context = build_context
        self._increment_chat = increment_chat
        self._backend_metadata = backend_metadata

    def handle(self, request: ConferTurnRequest) -> ConferTurnResult:
        text = request.text.strip()
        if not text:
            raise InvalidConferTurn("Nothing to respond to.")
        if request.channel not in ALLOWED_CHANNELS:
            raise InvalidConferTurn(f"Unknown Confer channel: {request.channel!r}")

        conversation_id = request.conversation_id.strip() or "owner"
        context = dict(self._build_context())
        context["confer"] = {
            "conversation_id": conversation_id,
            "input_channel": request.channel,
        }
        tool_policy = {"observation": True, "mutation": False}
        reply = self._call_backend(text, context, tool_policy)
        self._increment_chat()
        backend_label, model_label = self._backend_metadata()

        return ConferTurnResult(
            turn_id=str(uuid4()),
            conversation_id=conversation_id,
            channel=request.channel,
            user_text=text,
            reply=reply,
            backend_label=backend_label,
            model_label=model_label,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

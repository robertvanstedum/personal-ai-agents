"""Channel-neutral application boundary for Chief of Staff Confer turns.

HTML typing, HTML voice, Telegram text, Telegram voice, and internal API calls
all arrive here as text. The service owns channel normalization and the stable
turn result shape; the configured CoS backend still owns reasoning and memory-
worthiness judgment through the existing ``call_backend`` contract. Explicit
note commands are deterministic platform operations and never reach a model.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Callable
from uuid import UUID, uuid4


ALLOWED_CHANNELS = frozenset({
    "api_text",
    "html_text",
    "html_voice",
    "telegram_text",
    "telegram_voice",
})
MAX_EXPLICIT_NOTE_CHARS = 1_000
_SAVE_NOTE_PREFIX = re.compile(
    r"^(?:(?:okay|ok)\s*,?\s+)?(?:please\s+)?"
    r"save\s+(?:a|this)\s+note\s*:",
    re.IGNORECASE,
)
_SLASH_NOTE_PREFIX = re.compile(r"^/note(?:\s+|$)", re.IGNORECASE)
_UNVERIFIED_NOTE_SUCCESS = re.compile(
    r"(?:^\s*noted\b|"
    r"\b(?:platform\s+)?note\s+(?:successfully\s+)?"
    r"(?:saved|recorded|stored)\b|"
    r"^\s*(?:"
    r"note\s+(?:successfully\s+)?(?:saved|recorded|stored)\b|"
    r"(?:i|we)(?:'ve| have)\s+(?:successfully\s+)?"
    r"(?:saved|recorded|stored)\s+(?:the|your|that)\s+note\b|"
    r"(?:your|the|that)\s+note\s+(?:is|was|has been)\s+"
    r"(?:successfully\s+)?(?:saved|recorded|stored)\b"
    r"))",
    re.IGNORECASE,
)


class InvalidConferTurn(ValueError):
    """Raised when a caller supplies an empty turn or unknown channel."""


class ConferOperationFailed(RuntimeError):
    """Raised when an explicit platform operation does not complete."""


@dataclass(frozen=True)
class ConferTurnRequest:
    text: str
    channel: str
    conversation_id: str = "owner"
    request_id: str | None = None


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
    operation: dict | None = None
    configured_primary: str | None = None
    served_provider: str | None = None
    fallback_position: int | None = None
    fallback_reason: str | None = None

    def public_dict(self) -> dict:
        """Return response metadata without echoing the user's full input."""
        payload = {
            "turn_id": self.turn_id,
            "conversation_id": self.conversation_id,
            "channel": self.channel,
            "reply": self.reply,
            "backend_label": self.backend_label,
            "model_label": self.model_label,
            "created_at": self.created_at,
        }
        if self.operation is not None:
            payload["operation"] = self.operation
        if self.served_provider is not None:
            payload["runtime_route"] = {
                "configured_primary": self.configured_primary,
                "served_provider": self.served_provider,
                "served_model": self.model_label,
                "fallback_position": self.fallback_position,
                "fallback_reason": self.fallback_reason,
            }
        return payload


def parse_explicit_note(text: str) -> str | None:
    """Return normalized note text, or ``None`` for an ordinary chat turn."""
    slash_match = _SLASH_NOTE_PREFIX.match(text)
    if slash_match is not None:
        note_text = text[slash_match.end():]
    else:
        match = _SAVE_NOTE_PREFIX.match(text)
        if match is None:
            return None
        note_text = text[match.end():]

    # Store a single inert text line so newlines cannot create memory markup.
    note_text = " ".join(note_text.split())
    if not note_text:
        raise InvalidConferTurn("Note text must not be empty.")
    if len(note_text) > MAX_EXPLICIT_NOTE_CHARS:
        raise InvalidConferTurn(
            f"Note text must be {MAX_EXPLICIT_NOTE_CHARS} characters or fewer."
        )
    return note_text


def normalize_request_id(request_id: str | None) -> str:
    """Return a canonical operation UUID, generating one when absent."""
    if request_id is None or not str(request_id).strip():
        return str(uuid4())
    try:
        return str(UUID(str(request_id).strip()))
    except (ValueError, AttributeError) as exc:
        raise InvalidConferTurn("request_id must be a valid UUID.") from exc


class ConferTurnService:
    """Route one normalized turn through the permanent CoS coordination layer."""

    def __init__(
        self,
        *,
        call_backend: Callable[[str, dict, dict], str],
        build_context: Callable[[], dict],
        increment_chat: Callable[[], None],
        backend_metadata: Callable[[], tuple[str, str]],
        save_note: Callable[[str, str], dict] | None = None,
        reset_conversation: Callable[[str], bool] | None = None,
        get_routing_receipt: Callable[[str], dict | None] | None = None,
    ) -> None:
        self._call_backend = call_backend
        self._build_context = build_context
        self._increment_chat = increment_chat
        self._backend_metadata = backend_metadata
        self._save_note = save_note
        self._reset_conversation = reset_conversation
        self._get_routing_receipt = get_routing_receipt

    def handle(self, request: ConferTurnRequest) -> ConferTurnResult:
        text = request.text.strip()
        if not text:
            raise InvalidConferTurn("Nothing to respond to.")
        if request.channel not in ALLOWED_CHANNELS:
            raise InvalidConferTurn(f"Unknown Confer channel: {request.channel!r}")

        conversation_id = request.conversation_id.strip() or "owner"
        if text.casefold() == "/new":
            if self._reset_conversation is None:
                raise ConferOperationFailed("COS session reset is unavailable.")
            if not self._reset_conversation(conversation_id):
                raise ConferOperationFailed("COS session was not reset.")
            self._increment_chat()
            backend_label, _ = self._backend_metadata()
            return ConferTurnResult(
                turn_id=str(uuid4()),
                conversation_id=conversation_id,
                channel=request.channel,
                user_text=text,
                reply="New COS Agent A conversation started.",
                backend_label=backend_label,
                model_label="",
                created_at=datetime.now(timezone.utc).isoformat(),
                operation={
                    "type": "session_reset",
                    "status": "reset",
                    "storage": "agent_runtime_session",
                },
            )

        note_text = parse_explicit_note(text)
        if note_text is not None:
            if self._save_note is None:
                raise ConferOperationFailed("COS note saving is unavailable.")
            operation_id = normalize_request_id(request.request_id)
            outcome = self._save_note(note_text, operation_id)
            if not outcome.get("saved"):
                raise ConferOperationFailed("COS note was not saved.")
            deduplicated = bool(outcome.get("deduplicated"))
            self._increment_chat()
            status_word = "already saved" if deduplicated else "saved"
            return ConferTurnResult(
                turn_id=str(uuid4()),
                conversation_id=conversation_id,
                channel=request.channel,
                user_text=text,
                reply=f"Note {status_word} in COS platform memory.",
                backend_label="COS Platform",
                model_label="",
                created_at=datetime.now(timezone.utc).isoformat(),
                operation={
                    "type": "note_save",
                    "status": "deduplicated" if deduplicated else "saved",
                    "operation_id": operation_id,
                    "storage": "cos_platform_memory",
                },
            )

        turn_id = str(uuid4())
        context = dict(self._build_context())
        context["confer"] = {
            "conversation_id": conversation_id,
            "input_channel": request.channel,
            "receipt_id": turn_id,
        }
        if request.channel == "html_voice":
            voice_instruction = (
                "This is a live spoken conversation. Lead with the direct "
                "answer and normally use one to three short, natural "
                "sentences followed by at most one useful question. Do not "
                "use Markdown headings or bullet lists. Expand only when "
                "Robert asks for detail."
            )
            context["system_prompt"] = (
                f"{context.get('system_prompt', '').rstrip()}\n\n"
                f"{voice_instruction}"
            ).strip()
        tool_policy = {"observation": True, "mutation": False}
        reply = self._call_backend(text, context, tool_policy)
        if _UNVERIFIED_NOTE_SUCCESS.search(reply):
            # A backend has no note-write authority. Only the deterministic
            # operation path above may return a save receipt or success claim.
            reply = (
                "No note was saved because this turn did not match a platform "
                "note command. Say ‘Save a note: …’ or ‘Save this note: …’ "
                "with the note text."
            )
        self._increment_chat()
        backend_label, model_label = self._backend_metadata()
        runtime_route = context.get("runtime_route") or {}
        if self._get_routing_receipt is not None:
            receipt = self._get_routing_receipt(turn_id) or {}
            if receipt:
                runtime_route = {
                    "configured_primary": receipt.get("logical_model"),
                    "served_provider": receipt.get("served_provider"),
                    "served_model": receipt.get("served_model"),
                    "fallback_position": receipt.get("fallback_position"),
                    "fallback_reason": receipt.get("fallback_reason"),
                }
        model_label = runtime_route.get("served_model") or model_label

        return ConferTurnResult(
            turn_id=turn_id,
            conversation_id=conversation_id,
            channel=request.channel,
            user_text=text,
            reply=reply,
            backend_label=backend_label,
            model_label=model_label,
            created_at=datetime.now(timezone.utc).isoformat(),
            configured_primary=runtime_route.get("configured_primary"),
            served_provider=runtime_route.get("served_provider"),
            fallback_position=runtime_route.get("fallback_position"),
            fallback_reason=runtime_route.get("fallback_reason"),
        )

"""Channel-neutral application boundary for Chief of Staff Confer turns.

HTML typing, HTML voice, Telegram text, Telegram voice, and internal API calls
all arrive here as text. The service owns channel normalization and the stable
turn result shape; the configured CoS backend still owns reasoning and memory-
worthiness judgment through the existing ``call_backend`` contract. Explicit
note and reminder commands are deterministic platform operations and never
reach a model.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Callable
from uuid import UUID, uuid4
from core.realtime_voice.timing import VoiceTiming
from domains.cos.claim_truth import enforce as enforce_claim_truth
from domains.cos.reminders import MAX_TEXT_CHARS as MAX_REMINDER_TEXT_CHARS
from domains.cos.reminders import ReminderRefused


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
# Natural spoken and typed forms. Each is start-anchored and needs an explicit
# separator ("that ", "saying ", a colon or a dash) before the note text, so
# "make a note of that" or "can you make a note?" stay ordinary chat. "Write"
# and "create" a note are drafting requests ("write a note saying thank you to
# the team"), not something to keep, so they go to the model.
_NATURAL_NOTE_PREFIX = re.compile(
    r"^(?:(?:okay|ok)\s*,?\s+)?"
    r"(?:please\s+|(?:can|could|would)\s+you\s+(?:please\s+)?)?"
    r"(?:"
    r"(?:make|take|save|add|jot\s+down)\s+(?:me\s+)?(?:a\s+)?"
    r"note\s*(?::|-(?=\s|$)|[–—]|that(?=\s|$)|saying(?=\s|$))"
    r"|note\s+to\s+self\s*(?::|[-–—]|,)"
    r")",
    re.IGNORECASE,
)
_SLASH_REMIND_PREFIX = re.compile(r"^/remind(?:\s+|$)", re.IGNORECASE)
_REMINDER_DUE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?Z")
_REMINDER_DUE_MESSAGE = (
    "Reminder time must be a UTC time such as 2026-10-19T14:00Z, "
    "followed by the reminder text."
)
_REMINDER_REFUSALS = {
    "reminder_due_past": "Reminder time must be in the future and within a year.",
    "reminder_due_too_far": "Reminder time must be in the future and within a year.",
    "reminder_text_empty": "Reminder text must not be empty.",
    "reminder_text_too_long": (
        f"Reminder text must be {MAX_REMINDER_TEXT_CHARS} characters or fewer."
    ),
    "reminder_limit_reached": (
        "Too many reminders are waiting; no new reminder was saved."
    ),
}
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
    completion_verified: bool = False
    output_limit_reached: bool = False

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
        if self.output_limit_reached:
            payload["completion"] = {"state":"cut_off", "reason":"output_limit", "source":"openclaw_connector"}
        elif self.completion_verified:
            payload["completion"] = {"state":"complete", "source":"openclaw_connector"}
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
    """Return normalized note text, or ``None`` for an ordinary chat turn.

    This is the one note grammar shared by COS, the MC portal and the voice
    tool gate: ``/note …``, ``Save a note: …`` and the natural forms such as
    ``Make a note that …`` or ``Note to self: …``.
    """
    slash_match = _SLASH_NOTE_PREFIX.match(text)
    if slash_match is not None:
        note_text = text[slash_match.end():]
    else:
        match = _SAVE_NOTE_PREFIX.match(text) or _NATURAL_NOTE_PREFIX.match(text)
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


def parse_explicit_reminder(text: str) -> tuple[datetime, str] | None:
    """Return ``(due, text)`` for a ``/remind`` command, or ``None`` otherwise.

    The canonical form is ``/remind <DUE> <TEXT>`` with ``<DUE>`` in UTC as
    ``YYYY-MM-DDTHH:MMZ`` or ``YYYY-MM-DDTHH:MM:SSZ``. The reminder store owns
    the time bounds; this parser owns only the command shape.
    """
    match = _SLASH_REMIND_PREFIX.match(text)
    if match is None:
        return None
    parts = text[match.end():].split(None, 1)
    due_text = parts[0] if parts else ""
    if _REMINDER_DUE.fullmatch(due_text) is None:
        raise InvalidConferTurn(_REMINDER_DUE_MESSAGE)
    due_format = "%Y-%m-%dT%H:%M:%SZ" if due_text.count(":") == 2 else "%Y-%m-%dT%H:%MZ"
    try:
        due = datetime.strptime(due_text, due_format).replace(tzinfo=timezone.utc)
    except ValueError:
        raise InvalidConferTurn(_REMINDER_DUE_MESSAGE) from None

    # Store a single inert text line, like explicit notes.
    reminder_text = " ".join(parts[1].split()) if len(parts) > 1 else ""
    if not reminder_text:
        raise InvalidConferTurn("Reminder text must not be empty.")
    if len(reminder_text) > MAX_REMINDER_TEXT_CHARS:
        raise InvalidConferTurn(
            f"Reminder text must be {MAX_REMINDER_TEXT_CHARS} characters or fewer."
        )
    return due, reminder_text


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
        save_reminder: Callable[[datetime, str, str], dict] | None = None,
    ) -> None:
        self._call_backend = call_backend
        self._build_context = build_context
        self._increment_chat = increment_chat
        self._backend_metadata = backend_metadata
        self._save_note = save_note
        self._reset_conversation = reset_conversation
        self._get_routing_receipt = get_routing_receipt
        self._save_reminder = save_reminder

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

        reminder = parse_explicit_reminder(text)
        if reminder is not None:
            if self._save_reminder is None:
                raise ConferOperationFailed("COS reminders are unavailable.")
            due, reminder_text = reminder
            operation_id = normalize_request_id(request.request_id)
            try:
                outcome = self._save_reminder(due, reminder_text, operation_id)
            except ReminderRefused as exc:
                message = _REMINDER_REFUSALS.get(exc.code)
                if message is None:
                    raise ConferOperationFailed("COS reminder was not saved.") from exc
                raise InvalidConferTurn(message) from exc
            # The receipt is built only from the stored record.
            record = outcome.get("reminder") or {}
            if not outcome.get("saved") or not record.get("id") or not record.get("due_utc"):
                raise ConferOperationFailed("COS reminder was not saved.")
            deduplicated = bool(outcome.get("deduplicated"))
            self._increment_chat()
            status_word = "already saved" if deduplicated else "saved"
            saved_text = record.get("text") or reminder_text
            ending = "" if saved_text.endswith((".", "!", "?")) else "."
            return ConferTurnResult(
                turn_id=str(uuid4()),
                conversation_id=conversation_id,
                channel=request.channel,
                user_text=text,
                reply=(
                    f"Reminder {status_word} for {record['due_utc']}: "
                    f"{saved_text}{ending} Delivery: Telegram."
                ),
                backend_label="COS Platform",
                model_label="",
                created_at=datetime.now(timezone.utc).isoformat(),
                operation={
                    "type": "reminder_set",
                    "status": "deduplicated" if deduplicated else "saved",
                    "operation_id": operation_id,
                    "reminder_id": record["id"],
                    "due_utc": record["due_utc"],
                    "storage": "cos_reminders",
                    "delivery": "telegram",
                },
            )

        turn_id = str(uuid4())
        timing = VoiceTiming(correlation=turn_id,parent=request.request_id)
        timing.emit("context_start")
        try:
            context = dict(self._build_context())
        except Exception:
            timing.emit("context_failed")
            raise
        timing.emit("context_end")
        # Shared manner is system context, NEVER appended to the canonical owner
        # command. /new and explicit note parsing above retain exact semantics.
        from core.agent_partner import PARTNER_STYLE
        context['system_prompt'] = (context.get('system_prompt', '').rstrip() + '\n\n' + PARTNER_STYLE).strip()
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
        timing.emit("native_start")
        try:
            reply = self._call_backend(text, context, tool_policy)
        except Exception:
            timing.emit("native_failed")
            raise
        timing.emit("native_end")
        # A model reply has no write or scheduling authority: only the deterministic
        # operations above keep notes or set reminders, with their own receipts. Any
        # sentence claiming one, or promising a later action, is removed (claim_truth).
        reply, _ = enforce_claim_truth(reply)
        self._increment_chat()
        backend_label, model_label = self._backend_metadata()
        runtime_route = context.get("runtime_route") or {}
        if self._get_routing_receipt is not None:
            timing.emit("routing_receipt_start")
            try:
                receipt = self._get_routing_receipt(turn_id) or {}
            except Exception:
                timing.emit("routing_receipt_failed")
                raise
            timing.emit("routing_receipt_end")
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
            completion_verified=context.get("completion_evidence") == {"state":"complete", "source":"openclaw_connector"},
            output_limit_reached=context.get("completion_evidence") == {"state":"cut_off", "reason":"output_limit", "source":"openclaw_connector"},
        )

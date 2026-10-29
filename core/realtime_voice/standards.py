"""Platform voice interaction standards shared by every domain.

Interaction mode determines the turn boundary. Providers and domains may
change, but they must not invent a second implementation of the same mode.
"""

CONVERSATION_MODE = "conversation"
MEMO_MODE = "memo"
COMMAND_MODE = "command"

CONVERSATION_SILENCE_MS = 1200
CONVERSATION_PREFIX_PADDING_MS = 300


def conversation_turn_detection(
    provider: str,
    *,
    create_response: bool | None = None,
    interrupt_response: bool | None = None,
) -> dict:
    """Return the platform server-VAD contract for a conversation.

    ``create_response=False`` supports chained conversations such as COS:
    the voice provider detects and transcribes the turn, while the configured
    domain agent remains the only component allowed to reason and reply.

    ``interrupt_response`` (only the direct path passes it; Spec 165 §5.1 as
    amended 28 Oct 2026): with ``create_response=False`` the page creates every
    response itself, and ``interrupt_response=True`` keeps the provider's
    barge-in, which cancels a response when the owner starts speaking. Without
    it, as before: interrupt_response follows create_response. For xAI both
    fields are written only when passed; that xAI honours them is not verified
    (A4), and no caller passes them for xAI while it is not eligible
    (capabilities.MODEL_ACTIONS).
    """
    if provider == "openai":
        config = {
            "type": "server_vad",
            "prefix_padding_ms": CONVERSATION_PREFIX_PADDING_MS,
            "silence_duration_ms": CONVERSATION_SILENCE_MS,
        }
        if create_response is not None:
            config["create_response"] = create_response
            config["interrupt_response"] = create_response
        if interrupt_response is not None:
            config["interrupt_response"] = interrupt_response
        return config
    if provider == "xai":
        config = {
            "type": "server_vad",
            "silence_duration_ms": CONVERSATION_SILENCE_MS,
        }
        if create_response is not None and interrupt_response is not None:
            config["create_response"] = create_response
            config["interrupt_response"] = interrupt_response
        return config
    raise ValueError(f"Unsupported voice provider: {provider!r}")


def memo_turn_detection() -> None:
    """Memo/dictation mode never commits because the speaker pauses."""
    return None

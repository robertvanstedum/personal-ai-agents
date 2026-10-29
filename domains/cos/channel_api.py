"""Public CoS operations for already-authorized channel adapters.

Callers enforce their transport's owner check before invoking these operations.
The canonical Confer turn service retains its own validation and operation rules.
Imports are lazy: loading this interface never starts the runtime or reads memory.
"""


def chat(text: str, *, channel: str) -> str:
    """Return the existing owner-conversation reply, preserving errors and effects."""
    from .chief_of_staff import _chat

    return _chat(text, channel=channel)


def memory_character_count() -> int:
    """Return the existing status measure without exposing memory or mutable state."""
    from .chief_of_staff import _read_memory

    return len(_read_memory())

"""
core/realtime_voice/transcript.py — normalized transcript accumulation,
deduplication, ordering, and finalization for the shared realtime voice
module.

Per _working/CLAUDE_CODE_BUILD_SPEC_voice_realtime_2026-07-24.md Section 10.

Both providers' transcript events are normalized into TranscriptEvent
before reaching this module (that mapping lives in each provider adapter,
not here) -- this module only knows the normalized shape, not OpenAI/xAI
specifics. Two real differences the normalization must account for
(verified 2026-07-24 against official docs):

  - OpenAI: streaming deltas per item -- each event's `text` is a chunk to
    append (is_delta=True).
  - xAI: cumulative updates per item -- each event's `text` is the full
    current text for that item, replacing the previous value
    (is_delta=False).

Never rendered live -- callers must not surface accumulator state until
finalize()/finalize_with_metadata() is called at session end.
"""
from dataclasses import dataclass, field


@dataclass
class TranscriptEvent:
    sequence: int
    speaker: str  # "user" | "assistant"
    item_id: str
    text: str
    is_delta: bool
    completed: bool
    provider_event_id: str | None = None


@dataclass
class _ItemState:
    speaker: str
    text: str
    completed: bool
    first_sequence: int


class TranscriptAccumulator:
    """Accumulates TranscriptEvents in memory during an active session.

    Provider transcripts are observations of audio, not a promise of exact
    verbatim equivalence to the provider's internal speech representation
    (Section 10) -- this module stores whatever the provider reports, it
    does not attempt to improve or validate it.
    """

    def __init__(self) -> None:
        self._items: dict[str, _ItemState] = {}
        self._seen_event_ids: set[str] = set()
        self._partial = False
        self._partial_reason: str | None = None

    def add(self, event: TranscriptEvent) -> None:
        if event.provider_event_id is not None:
            if event.provider_event_id in self._seen_event_ids:
                return
            self._seen_event_ids.add(event.provider_event_id)

        existing = self._items.get(event.item_id)
        if existing is None:
            self._items[event.item_id] = _ItemState(
                speaker=event.speaker,
                text=event.text,
                completed=event.completed,
                first_sequence=event.sequence,
            )
            return

        new_text = existing.text + event.text if event.is_delta else event.text
        existing.text = new_text
        existing.completed = existing.completed or event.completed

    def mark_partial(self, reason: str) -> None:
        self._partial = True
        self._partial_reason = reason

    def finalize(self) -> list[dict]:
        ordered_item_ids = sorted(
            self._items, key=lambda item_id: self._items[item_id].first_sequence
        )
        return [
            {
                "speaker": self._items[item_id].speaker,
                "text": self._items[item_id].text,
                "completed": self._items[item_id].completed,
            }
            for item_id in ordered_item_ids
        ]

    def finalize_with_metadata(self) -> dict:
        return {
            "turns": self.finalize(),
            "partial": self._partial,
            "partial_reason": self._partial_reason,
        }

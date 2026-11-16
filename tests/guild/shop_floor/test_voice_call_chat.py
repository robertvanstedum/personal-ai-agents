"""A finished talking call is a chat (#620). A new chat is named for the call and stays in the list.
A chat that already has words keeps its name and keeps the call."""
from datetime import datetime, timezone

from core.realtime_voice.call_title import done_line, voice_call_title
from minimoi_portal.guild_ui.conversations import ConversationStore, is_empty_chat, title_from


def test_the_list_name_uses_roberts_clock_and_clips_a_long_first_line():
    when = datetime(2026, 11, 15, 20, 29, tzinfo=timezone.utc)
    assert voice_call_title(when, "decision log for the release") == "Voice call · 2:29 PM · decision log for the release"
    long = voice_call_title(when, "decision log for the release and everything else that was said after")
    assert long.startswith("Voice call · 2:29 PM · decision log")
    assert long.endswith("…")
    assert done_line("2026-11-15T20:25:00Z", "2026-11-15T20:29:00Z", 4) == "Done · 4 min · 4 turns"
    assert done_line("2026-11-15T20:29:00Z", "2026-11-15T20:29:20Z", 1) == "Done · 20s · 1 turn"


def test_a_new_chat_becomes_a_list_entry_and_an_existing_chat_keeps_its_name(tmp_path):
    store = ConversationStore(str(tmp_path), base_floor="shop")
    empty, _ = store.create("robert", key="new")
    assert is_empty_chat(empty)
    title = voice_call_title(datetime(2026, 11, 15, 20, 29, tzinfo=timezone.utc), "decision log")
    call = {"session": "ab" * 16, "started_at": "2026-11-15T20:25:00Z", "saved_at": "2026-11-15T20:29:00Z",
            "turn_count": 4, "done": "Done · 4 min · 4 turns"}
    kept = store.file_voice_call(empty["id"], "robert", call, title=title)
    assert kept["title"] == title
    assert is_empty_chat(kept) is False
    assert kept["id"] in [row["id"] for row in store.list("robert", hide_empty=True)]
    again = store.file_voice_call(empty["id"], "robert", call, title="Voice call · 9:00 AM · other")
    assert again["title"] == title and len(again["voice_calls"]) == 1

    busy, _ = store.create("robert", key="busy")
    store.touch(busy["id"], "robert", first_text="The build plan")
    stayed = store.file_voice_call(busy["id"], "robert", {**call, "session": "cd" * 16}, title=title)
    assert stayed["title"] == title_from("The build plan")
    assert {row["session"] for row in stayed["voice_calls"]} == {"cd" * 16}

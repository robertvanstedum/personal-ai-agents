"""Combined backup and restore of a topic's FILES (Codex, 7 Oct: the topics-only copy test is not a combined verification).

What this proves: copying the guild data folder's ``topics/`` and ``conversations/`` together and opening stores on the copy gives
back the topic, every card and revision, the comments, the record (journal), the waiting inbox, and the conversation record the
topic points to (its id, title and scope). What it does NOT prove, and the product does not claim: the conversation's MESSAGES live
in the floor database (Postgres), not in these folders, so a restored topic would show its cards and record but an empty chat
until the database is restored too. The test pins that boundary so nobody mistakes the file copy for the whole backup."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from minimoi_portal.guild_ui import topics as T
from minimoi_portal.guild_ui.conversations import ConversationStore


def test_topics_and_conversations_copied_together_restore_the_topic_and_its_conversation_record(tmp_path):
    data = tmp_path / "guild"
    topics, convs = T.TopicStore(str(data)), ConversationStore(str(data), base_floor="shop")
    t = topics.create_topic("robert", "Backup topic", "summary")
    conv, _ = convs.create("robert", key="topic-backup-0001", scope="topic")
    convs.rename(conv["id"], "robert", "Backup topic")
    topics.set_conversation(t["id"], "robert", conv["id"])
    doc = topics.add_item(t["id"], "robert", "document", "Spec", text="first\n\nsecond")
    topics.add_revision(t["id"], doc["id"], "robert", text="first\n\nsecond, revised")
    c = topics.add_comment(t["id"], doc["id"], "robert", "Check this", rev="A", anchor={"type": "block", "index": 1, "quote": "second"})
    topics.append_journal(t["id"], "robert", author={"kind": "owner", "name": "Robert"}, via="ui", kind="note", text="A recorded note", refs=[])
    topics.put_inbox(t["id"], "codex", kind="finding", text="Waiting contribution")

    restored = tmp_path / "restored" / "guild"
    restored.mkdir(parents=True)
    for folder in ("topics", "conversations"):
        shutil.copytree(data / folder, restored / folder)

    topics2, convs2 = T.TopicStore(str(restored)), ConversationStore(str(restored), base_floor="shop")
    got = topics2.get_topic(t["id"], "robert")
    assert got["topic"]["conversation_id"] == conv["id"] and len(got["items"]) == 1
    item = topics2.get_item(t["id"], doc["id"], "robert", rev="A")
    assert item["text"] == "first\n\nsecond" and topics2.get_item(t["id"], doc["id"], "robert")["rev"]["rev"] == "B"
    assert [x["id"] for x in topics2.comments(t["id"], doc["id"], "robert")] == [c["id"]]
    assert [e["text"] for e in topics2.journal(t["id"], "robert")] == ["A recorded note"]
    assert topics2.inbox_waiting(t["id"], "robert") == {"codex": 1}
    back = convs2.get(got["topic"]["conversation_id"], "robert")
    assert back["id"] == conv["id"] and back["title"] == "Backup topic" and back.get("scope") == "topic"
    report = topics2.verify("robert")
    assert report["topics"] == 1 and report["items"] == 1 and not report["orphan_revision_files"] and not report["torn_or_malformed_lines"] and not report["unreadable_records"]


def test_a_topics_only_copy_leaves_the_pointer_dangling_and_the_page_says_so_not_invents_a_chat(tmp_path):
    data = tmp_path / "guild"
    topics, convs = T.TopicStore(str(data)), ConversationStore(str(data), base_floor="shop")
    t = topics.create_topic("robert", "Topics only")
    conv, _ = convs.create("robert", key="topic-backup-0002", scope="topic")
    topics.set_conversation(t["id"], "robert", conv["id"])
    restored = tmp_path / "restored" / "guild"
    restored.mkdir(parents=True)
    shutil.copytree(data / "topics", restored / "topics")                                       # the conversations folder was not backed up
    got = T.TopicStore(str(restored)).get_topic(t["id"], "robert")
    assert got["topic"]["conversation_id"] == conv["id"]                                        # the pointer survives
    try:
        ConversationStore(str(restored), base_floor="shop").get(conv["id"], "robert")
        found = True
    except Exception:
        found = False
    assert found is False                                                                       # but what it points to is gone: a backup of topics alone is not enough


# ── the database part: an isolated, synthetic restore (no live database is touched) ──────────────────────────
import sqlite3                                                                                      # noqa: E402

from floor_db_helpers import SqliteFloor                                                            # noqa: E402,F401  (the floor store on SQLite)
from minimoi_portal.guild_ui.stores import Author                                                   # noqa: E402

OWNER = Author("robert", "owner", "Robert")


def _notes(floor_store):
    got = floor_store.list_notes()
    assert getattr(got, "ok", True) or getattr(got, "status", "ok") in ("ok", "current"), got
    value = getattr(got, "value", None) or getattr(got, "data", None) or {}
    return [n["text"] for n in value.get("notes", [])]


def _sqlite_backup(src: Path, dst: Path):
    """What a database backup does for SQLite: the online backup API (a plain file copy of a database in use is not safe)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    a, b = sqlite3.connect(str(src)), sqlite3.connect(str(dst))
    try:
        a.backup(b)
    finally:
        a.close(); b.close()


def test_topic_conversation_and_chat_messages_restore_together_from_isolated_synthetic_data(tmp_path):
    data = tmp_path / "guild"
    live = SqliteFloor(tmp_path / "db-live")
    topics, convs = T.TopicStore(str(data)), ConversationStore(str(data), base_floor="guild")
    t = topics.create_topic("robert", "Restore with chat")
    conv, _ = convs.create("robert", key="topic-backup-0003", scope="topic")
    topics.set_conversation(t["id"], "robert", conv["id"])
    chat = live.store(conv["notes_floor"])
    chat.add_note("req-synthetic-0001", "First synthetic message in the topic chat", OWNER)
    chat.add_note("req-synthetic-0002", "Second synthetic message", OWNER)
    other = live.store("guild/c-someoneelse00")
    other.add_note("req-synthetic-0003", "A message that belongs to another conversation", OWNER)
    assert _notes(chat) and len(_notes(chat)) == 2

    # the backup: the two folders as files, the database with its own backup mechanism
    restored = tmp_path / "restored"
    for folder in ("topics", "conversations"):
        shutil.copytree(data / folder, restored / "guild" / folder)
    _sqlite_backup(live.path, restored / "db" / "guild_floor.sqlite")
    _sqlite_backup(live.media_path, restored / "db" / "media.sqlite")

    new_db = SqliteFloor(restored / "db", migrate=False)                                           # an existing database, not re-created
    topics2, convs2 = T.TopicStore(str(restored / "guild")), ConversationStore(str(restored / "guild"), base_floor="guild")
    got = topics2.get_topic(t["id"], "robert")["topic"]
    back = convs2.get(got["conversation_id"], "robert")
    chat2 = new_db.store(back["notes_floor"])
    assert sorted(_notes(chat2)) == sorted(["First synthetic message in the topic chat", "Second synthetic message"])      # the chat text came back
    assert "another conversation" not in " ".join(_notes(chat2))                                  # and only this conversation's text
    assert topics2.verify("robert")["unreadable_records"] == []

    # what the pieces are worth alone (pinned, so a partial backup is never mistaken for a full one)
    files_only = SqliteFloor(tmp_path / "db-empty")                                                # files restored, database not
    assert _notes(files_only.store(back["notes_floor"])) == []
    assert (restored / "guild" / "topics" / t["id"] / "topic.json").exists() and got["conversation_id"] == conv["id"]

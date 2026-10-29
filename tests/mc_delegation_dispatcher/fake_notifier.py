"""fake-notifier (host runtime spec v0.5 §9.3): the notifier adapter's test instrument. It has the approvals bot's
interface (``send``, ``updates``, ``id``), so the supervisor and the runtime link run unchanged against it, and it
writes each message to ``notify.jsonl`` (``notify-prod.jsonl`` for prod-watch-test) instead of sending it. The owner
test identity's replies go in through ``reply``, shaped exactly as Telegram delivers Robert's own replies, so his
approvals are verified and receipted by the real code path (core/supervision/telegram.py). Nothing leaves the process."""
import json
import os

OWNER = 8379221702                       # the owner test identity's chat and user id (the private-chat shape)
BOT = 7000000001


class FakeNotifier:
    id = BOT

    def __init__(self, path):
        self.path = path
        self.next_id, self.inbox, self.next_update = 1000, [], 5000
        self.fail_next = 0               # the next n sends fail, as a Telegram error would (nothing is written)

    def send(self, chat_id, text, *, reply_to=None):
        if self.fail_next:
            self.fail_next -= 1
            raise RuntimeError("telegram_down")
        self.next_id += 1
        line = {"message_id": self.next_id, "chat_id": chat_id, "reply_to": reply_to, "text": text}
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return self.next_id

    def lines(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                return [json.loads(x) for x in fh if x.strip()]
        except FileNotFoundError:
            return []

    def reply(self, text, to_message_id):
        """The owner test identity replies to one message, as Robert does: his own plain reply in his private chat."""
        self.next_update += 1
        self.inbox.append({"update_id": self.next_update, "message": {
            "message_id": self.next_update + 10_000, "date": 1761500000, "text": text,
            "chat": {"id": OWNER, "type": "private"}, "from": {"id": OWNER, "is_bot": False},
            "reply_to_message": {"message_id": to_message_id, "from": {"id": BOT, "is_bot": True}}}})

    def updates(self, offset, *, wait=25):
        out, self.inbox = [u for u in self.inbox if u["update_id"] >= offset], []
        return out

"""A synthetic canonical CoS history (``domains/cos/history.py`` layout) for the production CoS capture tests (#509). Invented words only."""
from __future__ import annotations

import json
import secrets
from pathlib import Path

AT = "2026-11-11T15:00:00Z"


class History:
    """``<root>/conversations/<cid>.json`` and ``<root>/turns/<cid>.jsonl``, written the way cos-scheduler writes them."""

    def __init__(self, root: Path, environment: str = "prod"):
        self.root, self.environment = Path(root), environment
        (self.root / "conversations").mkdir(parents=True)
        (self.root / "turns").mkdir()
        self.seq: dict = {}

    def meta_path(self, cid: str) -> Path:
        return self.root / "conversations" / f"{cid}.json"

    def turns_path(self, cid: str) -> Path:
        return self.root / "turns" / f"{cid}.jsonl"

    def conversation(self, *turns, private=False, environment=None, scope="personal", cid=None, omit_private=False) -> str:
        cid = cid or "cosc-" + secrets.token_hex(12)
        meta = {"schema": "minimoi.cos.conversation/1", "conversation_id": cid, "title": "TITLE-SECRET", "private": private,
                "created_at": AT, "read_only": False, "data_scope": scope, "environment": environment or self.environment}
        if omit_private:
            del meta["private"]
        self.meta_path(cid).write_text(json.dumps(meta, sort_keys=True))
        self.seq[cid] = 0
        for user, reply in turns:
            self.say(cid, user, reply)
        return cid

    def say(self, cid: str, user: str, reply: str | None = None) -> None:
        self.seq[cid] = self.seq.get(cid, 0) + 1
        row = {"schema": "minimoi.cos.turn/1", "conversation_id": cid, "turn_id": "t-" + secrets.token_hex(8), "seq": self.seq[cid],
               "status": "answered" if reply else "pending", "user_text": user, "reply_text": reply, "created_at": AT,
               **({"answered_at": AT} if reply else {})}
        with self.turns_path(cid).open("a") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")


def name(environment: str, cid: str) -> str:
    return f"{environment}--{cid}.json"

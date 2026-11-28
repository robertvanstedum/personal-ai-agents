"""The automatic UAT's own chats never reach Vault search (keyword or meaning), and ordinary chats are untouched.

The staging path end to end, on synthetic stores: the real MC conversation store and exporter, a CoS history folder,
the staging feed (``scripts/memory/dev_feed.py``) with a fake embedder, and the search the agents' recall tools use.
The chats start UNMARKED (as the runs before the marker left them) and crowd the search; the one-off staging command
(``core/uat_chats.py``) marks them, and the next feed cycle leaves them out of both indexes."""
from __future__ import annotations

import json

import pytest

from core import uat_chats
from core.memory_index import policy, search
from core.memory_index.embedder import PIN_DIGEST_FULL
from scripts.memory.dev_feed import run
from tests.memory_index.helpers import FakeEmbedder

from . import test_dev_feed as F
from . import test_mc_capture as M

QUESTION = "Did we ever talk about why I am hearing back from employers less often lately?"
TARGET_COS, UAT_COS = "cosc-0123456789abcdef01234567", "cosc-aaaaaaaaaaaaaaaaaaaaaaaa"
STAGING = {"MINIMOI_ENV": "staging", "MINIMOI_ROLE": "standby"}


def _cos(root, cid, title, user_text, reply, **meta):
    doc = {"schema": "minimoi.cos.conversation/1", "conversation_id": cid, "title": title, "private": False, "read_only": False,
           "environment": "dev", "data_scope": "personal", "created_at": F.AT, **meta}
    (root / "conversations" / f"{cid}.json").write_text(json.dumps(doc))
    row = {"schema": "minimoi.cos.turn/1", "conversation_id": cid, "turn_id": "t-" + cid[5:21], "seq": 1, "status": "answered",
           "user_text": user_text, "reply_text": reply, "created_at": F.AT, "answered_at": F.AT, "durable_ack_at": F.AT}
    (root / "turns" / f"{cid}.jsonl").write_text(json.dumps(row) + "\n")


class Staging:
    def __init__(self, tmp_path):
        self.world = M.World(tmp_path)
        self.world.out = tmp_path / "mc"
        self.conversations_dir = tmp_path / "cos"
        self.history = self.conversations_dir / "history"
        (self.history / "conversations").mkdir(parents=True)
        (self.history / "turns").mkdir()
        data = tmp_path / "vault"
        self.args = dict(cos_root=self.history, mc_root=self.world.out, shelf_root=data / "memory",
                         index_path=data / "memory-index" / "memory.sqlite", jobs_root=data / "jobs", min_free=0,
                         mc_index_path=data / "memory-index" / "memory-mc.sqlite")

    def cycle(self) -> tuple[dict, dict]:
        """One staging cycle: the portal exporter (a fresh export folder, as dev_cycle unpacks one), then the feed."""
        for old in self.world.out.glob("*") if self.world.out.exists() else ():
            old.unlink()
        exported = M.X.export(guild_dir=self.world.guild, out=self.world.out, principal=M.PRINCIPAL, instance="dev",
                              floor_for=self.world.floor, now=M.NOW)
        F.manifest(self.world.out)
        return exported, run(**self.args, embedder=FakeEmbedder(digest=PIN_DIGEST_FULL))

    def passages(self, query, index="index_path") -> dict:
        found = search.search(query, shelf_root=self.args["shelf_root"], index_path=self.args[index],
                              embedder=FakeEmbedder(digest=PIN_DIGEST_FULL), caller=policy.Caller(tier="all", scopes=("robert",)))
        assert found["status"]["meaning"] == "ok" and not found["unresolved"], found["status"]
        return {kind: " | ".join(h["passage"] for h in found[kind]) for kind in ("keyword", "meaning")}


@pytest.fixture
def staging(tmp_path):
    s = Staging(tmp_path)
    _cos(s.history, TARGET_COS, "Fewer replies", "Why am I hearing back from employers less often lately?",
         "TARGETCOS: we went over it in November; the roles you apply to changed.")
    _cos(s.history, UAT_COS, "uat-20261126T091500Z C1", QUESTION, "UATCOSREPLY: yes, we talked about employers in November.")
    s.target_mc = s.world.conversation(M.PRINCIPAL, ("owner", "Collect my foreign-language slip-ups for a study list?"),
                                       ("mc", "TARGETMC: yes, corrections go to a restudy list."))
    s.uat_mc, _ = s.world.store.create(M.PRINCIPAL, key="uat" + "0123456789abcdef01234567")     # the UAT script's own key shape
    s.world.say(s.uat_mc, ("owner", "Did we ever discuss collecting the foreign-language slip-ups for a study list?"),
                ("mc", "UATMCREPLY: yes, we discussed slip-ups and a study list."))
    return s


def test_unmarked_uat_chats_crowd_search_and_marked_ones_leave_keyword_and_meaning_search(staging):
    exported, report = staging.cycle()
    assert report["state"] == "ok", report
    before = staging.passages("employers hearing back")
    assert "UATCOSREPLY" in before["keyword"] and "UATCOSREPLY" in before["meaning"]        # C1's cause, reproduced
    assert "UATMCREPLY" in staging.passages("slip-ups study list", "mc_index_path")["meaning"]

    # the one-off staging command: a dry run writes nothing, --apply marks exactly the UAT's chats
    dry_cos = uat_chats.mark_cos(staging.conversations_dir)
    dry_mc = uat_chats.mark_mc(staging.world.guild, M.PRINCIPAL)
    assert dry_cos["marked"] == [UAT_COS] and dry_mc["marked"] == [staging.uat_mc["id"]] and not dry_cos["applied"]
    assert "uat" not in json.loads((staging.history / "conversations" / f"{UAT_COS}.json").read_text())
    assert uat_chats.mark_cos(staging.conversations_dir, apply=True)["marked"] == [UAT_COS]
    assert uat_chats.mark_mc(staging.world.guild, M.PRINCIPAL, apply=True)["marked"] == [staging.uat_mc["id"]]
    assert uat_chats.mark_cos(staging.conversations_dir, apply=True) == {"store": "cos", "applied": True, "marked": [],
                                                                         "already": 1, "not_found": []}
    assert uat_chats.mark_mc(staging.world.guild, M.PRINCIPAL, apply=True)["already"] == 1
    assert json.loads((staging.history / "conversations" / f"{UAT_COS}.json").read_text())["uat"] is True
    assert "uat" not in json.loads((staging.history / "conversations" / f"{TARGET_COS}.json").read_text())
    assert staging.world.store.get(staging.uat_mc["id"], M.PRINCIPAL)["uat"] is True
    assert "uat" not in staging.world.store.get(staging.target_mc["id"], M.PRINCIPAL)

    exported, report = staging.cycle()
    assert exported["status"] == "ok" and exported["counts"]["uat"] == 1
    assert sorted(p.name for p in staging.world.out.glob("dev--*.json")) == [f"dev--{staging.target_mc['id']}.json"]
    assert report["state"] == "ok" and report["counts"]["cos_uat"] == 1 and not report["errors"], report
    assert report["mc_index"]["code"] == "ok"
    for index in ("index_path", "mc_index_path"):
        for query in ("employers hearing back", "slip-ups study list", QUESTION, "UATCOSREPLY", "UATMCREPLY"):
            found = staging.passages(query, index)
            assert "UATCOSREPLY" not in found["keyword"] + found["meaning"], (index, query)
            assert "UATMCREPLY" not in found["keyword"] + found["meaning"], (index, query)
    # the ordinary chats are found exactly as before, by keyword and by meaning
    cos_hit, mc_hit = staging.passages("employers hearing back"), staging.passages("slip-ups study list", "mc_index_path")
    assert "Why am I hearing back" in cos_hit["keyword"] and "TARGETCOS" in cos_hit["meaning"]
    assert "Collect my foreign-language" in mc_hit["keyword"] and "TARGETMC" in mc_hit["meaning"]
    assert "TARGETCOS" in staging.passages("TARGETCOS")["keyword"]
    assert "TARGETMC" in staging.passages("TARGETMC", "mc_index_path")["keyword"]


def test_a_chat_marked_at_creation_is_never_exported_or_read(staging):
    born, _ = staging.world.store.create(M.PRINCIPAL, key="key-born-marked", uat=True)
    staging.world.say(born, ("owner", "BORNMARKED question"), ("mc", "BORNMARKED reply"))
    _cos(staging.history, "cosc-bbbbbbbbbbbbbbbbbbbbbbbb", "Any title at all", "BORNMARKED cos question", "BORNMARKED cos reply",
         uat=True)
    exported, report = staging.cycle()
    assert exported["counts"]["uat"] == 1 and report["counts"]["cos_uat"] == 1 and report["state"] == "ok", report
    for index in ("index_path", "mc_index_path"):
        found = staging.passages("BORNMARKED", index)
        assert "BORNMARKED" not in found["keyword"] + found["meaning"]
    assert "BORNMARKED" not in "".join(p.read_text() for p in staging.world.out.glob("*.json"))
    assert not any(b"BORNMARKED" in p.read_bytes() for p in staging.args["shelf_root"].rglob("*") if p.is_file())


def test_the_exporter_and_reader_fail_closed_on_any_marker_value(staging):
    from core.memory_shelf import cos
    path = staging.history / "conversations" / f"{TARGET_COS}.json"
    for value in (True, "yes", 1, "false"):
        path.write_text(json.dumps({**json.loads(path.read_text()), "uat": value}))
        with pytest.raises(cos.Refused, match="^uat$"):
            cos.read(staging.history, TARGET_COS)
    path.write_text(json.dumps({**json.loads(path.read_text()), "uat": False}))
    assert cos.read(staging.history, TARGET_COS)[0].turns


def test_the_one_off_command_marks_only_the_uats_chats_and_only_on_staging(staging, capsys):
    _cos(staging.history, "cosc-cccccccccccccccccccccccc", "uat-private-run C1", "q", "r", private=True)
    _cos(staging.history, "cosc-dddddddddddddddddddddddd", "My uat-20261126 notes", "q", "r")       # an ordinary title
    for environ in ({}, {"MINIMOI_ENV": "production", "MINIMOI_ROLE": "production"}, {"MINIMOI_ENV": "staging"},
                    {"MINIMOI_ENV": "staging", "MINIMOI_ROLE": "production"}):
        assert uat_chats.main(["cos", "--conversations-dir", str(staging.conversations_dir), "--apply"], environ) == 2
        assert json.loads(capsys.readouterr().out) == {"state": "refused", "code": "staging_only"}
    assert not any("uat" in json.loads(p.read_text()) for p in (staging.history / "conversations").glob("*.json"))
    assert uat_chats.main(["cos", "--conversations-dir", str(staging.conversations_dir), "--apply", "--id",
                           "cosc-eeeeeeeeeeeeeeeeeeeeeeee"], STAGING) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["marked"] == [UAT_COS] and out["count"] == 1 and out["not_found"] == ["cosc-eeeeeeeeeeeeeeeeeeeeeeee"]
    assert "uat-" not in json.dumps(out).replace("uat_", "")                                  # ids and counts, never a title
    other = staging.world.conversation(M.OTHER, ("owner", "another account's chat"))
    assert uat_chats.main(["mc", "--guild-dir", str(staging.world.guild), "--principal", M.PRINCIPAL, "--apply", "--id",
                           staging.target_mc["id"], "--id", other["id"]], STAGING) == 0
    out = json.loads(capsys.readouterr().out)
    assert sorted(out["marked"]) == sorted([staging.uat_mc["id"], staging.target_mc["id"]])     # --id: the operator's own choice
    assert out["not_found"] == [other["id"]]                                                   # never another account's chat
    assert uat_chats.main(["mc", "--guild-dir", str(staging.world.guild / "missing"), "--principal", M.PRINCIPAL], STAGING) == 2

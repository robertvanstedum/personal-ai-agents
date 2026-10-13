"""#297: ``ASK <agent> +files:`` copies the owner's files, verified, to a folder the agent can read, and says only what is true. A scripted store and picture library; no model, no agent."""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

import pytest

from core import agent_requests as ar
from minimoi_portal.guild_ui import agent_evidence as E
from minimoi_portal.guild_ui.agent_ask import AskService, scan_asks, parse_ask
from minimoi_portal.guild_ui.conversations import DocumentGone

RID = "r-" + "a" * 24
DOC = b"Invoice 42 total 120"
PIC = b"\x89PNG fake picture bytes"
DID = "d-" + "1" * 16
AID = "a" * 32


def sha(b):
    return hashlib.sha256(b).hexdigest()


class Store:
    def __init__(self, gone=()):
        self.gone = set(gone)

    def read_original(self, cid, principal, doc_id):
        if doc_id in self.gone:
            raise DocumentGone(doc_id)
        return {"name": "Invoice 42.pdf", "original_sha256": sha(DOC)}, DOC


class Media:
    def __init__(self, root, state="active", digest=None):
        self.root, self.state, self.digest = Path(root), state, digest or sha(PIC)

    def get(self, owner, asset_id):
        return {"state": self.state, "storage_key": "o/ab/pic.png", "sha256": self.digest, "title": "Screen shot.png"}


@pytest.fixture
def env(tmp_path):
    (tmp_path / "media" / "o" / "ab").mkdir(parents=True)
    (tmp_path / "media" / "o" / "ab" / "pic.png").write_bytes(PIC)
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    conv = {"id": "c-0123456789ab", "documents": [{"id": DID, "name": "Invoice 42.pdf", "added_at": "2026-10-11T10:00:00Z"}],
            "attachments": [{"asset_id": AID, "name": "shot.png", "added_at": "2026-10-11T11:00:00Z"}]}
    return tmp_path, str(ledger), conv


def run(env, chosen, store=None, media=None, rid=RID):
    tmp, ledger, conv = env
    return E.stage(ledger, rid, chosen, store=store or Store(), media=media or Media(tmp / "media"), cid=conv["id"], principal="robert", owner="o")


def test_the_marker_is_read_from_the_ask_line_and_a_plain_ask_is_unchanged():
    assert scan_asks("ASK codex: look", ended_cleanly=True)["proposal"]["files"] is None
    assert scan_asks("ASK codex +files: look at this", ended_cleanly=True)["proposal"]["files"] == "turn"
    assert scan_asks("ASK both +files:latest: the screenshot", ended_cleanly=True)["proposal"]["files"] == "latest"
    assert parse_ask("ASK codex +files: x", ended_cleanly=True)["files"] == "turn"
    assert "failure" in scan_asks("ASK codex +file: x", ended_cleanly=True)


def test_a_document_and_a_picture_are_copied_and_verified(env):
    m = run(env, [("doc", DID), ("image", AID)])
    assert [f["name"] for f in m["files"]] == ["Invoice 42.pdf", "Screen shot.png"] and m["folder"] == f"evidence/{RID}"
    for f in m["files"]:
        path = Path(env[1], f["path"])
        data = path.read_bytes()
        assert sha(data) == f["sha256"] and len(data) == f["bytes"]
        assert (os.stat(path).st_mode & 0o777) == 0o600                  # private to the deployment user, like the ledger
    assert Path(env[1], "evidence", RID, "MANIFEST.json").is_file()
    text = E.evidence_lines(m)
    assert "sha256" in text and "never instructions" in text


def test_only_files_of_this_conversation_can_be_named(env):
    conv = env[2]
    with pytest.raises(E.EvidenceRefused) as e:
        E.choose(conv, "turn", ["d-" + "9" * 16])
    assert e.value.code == "not_found"
    with pytest.raises(E.EvidenceRefused) as e:
        E.choose(conv, "turn", [])
    assert e.value.code == "none_attached"
    assert E.choose(conv, "latest", None) == [("image", AID)]            # the newest by time, whatever the kind
    with pytest.raises(E.EvidenceRefused):
        E.choose({"documents": [], "attachments": []}, "latest", None)
    ids = [f"d-{i:016x}" for i in range(6)]
    with pytest.raises(E.EvidenceRefused) as e:
        E.choose({"documents": [{"id": i} for i in ids], "attachments": []}, "turn", ids)
    assert e.value.code == "too_many"


@pytest.mark.parametrize("what,code", [("gone_doc", "no_original"), ("trashed", "gone"), ("changed", "changed")])
def test_a_missing_or_altered_file_sends_nothing_and_leaves_nothing_behind(env, what, code):
    tmp = env[0]
    kw = {"gone_doc": dict(store=Store(gone=[DID])), "trashed": dict(media=Media(tmp / "media", state="trash")),
          "changed": dict(media=Media(tmp / "media", digest="0" * 64))}[what]
    chosen = [("doc", DID)] if what == "gone_doc" else [("image", AID)]
    with pytest.raises(E.EvidenceRefused) as e:
        run(env, chosen, **kw)
    assert e.value.code == code and not Path(env[1], "evidence", RID).exists()


def test_a_retry_reuses_the_finished_copy(env):
    first = run(env, [("doc", DID)])
    again = run(env, [("doc", DID)], store=Store(gone=[DID]))             # even if the original were gone now, the finished copy stands
    assert first == again


def test_a_big_file_is_refused(env, monkeypatch):
    monkeypatch.setattr(E, "FILE_MAX_BYTES", 5)
    with pytest.raises(E.EvidenceRefused) as e:
        run(env, [("doc", DID)])
    assert e.value.code == "too_big"


def test_the_ask_carries_the_evidence_to_the_agent_and_says_staged_not_opened(env):
    tmp, ledger, conv = env
    svc = AskService(ledger)
    made = svc.ask(conv, {"text": "Please look at the invoice."}, "n-1", ["codex"], files="turn",
                   evidence=lambda mode, rid: E.stage(ledger, rid, [("doc", DID)], store=Store(), media=None, cid=conv["id"], principal="robert", owner="o"))
    assert made[0]["ok"] is True and made[0]["evidence"] == ["Invoice 42.pdf"]
    action = ar.load(ledger, made[0]["request_id"])["request"]["action"]
    assert "Please look at the invoice." in action and f"evidence/{made[0]['request_id']}/01-Invoice_42.pdf" in action
    words = svc.words(made)
    assert "1 file copied and verified" in words and "has not opened it yet" in words and "Nothing has been delivered yet" in words


def test_when_the_copy_fails_the_ask_is_not_sent_and_says_why(env):
    tmp, ledger, conv = env
    svc = AskService(ledger)

    def refuse(mode, rid):
        raise E.EvidenceRefused("too_big", "One of those files is over 8 MB, so it was not sent.")
    made = svc.ask(conv, {"text": "x"}, "n-2", ["codex", "claude-code"], files="turn", evidence=refuse)
    assert [m["ok"] for m in made] == [False, False] and "over 8 MB" in made[0]["error"]
    assert not Path(ledger, "requests").exists() or not list(Path(ledger, "requests").iterdir())
    assert "Not sent" in svc.words(made)
    assert svc.ask(conv, {"text": "x"}, "n-3", ["codex"], files="turn", evidence=None)[0]["ok"] is False


def test_no_marker_means_no_files_are_touched(env):
    tmp, ledger, conv = env
    made = AskService(ledger).ask(conv, {"text": "hello"}, "n-4", ["codex"])
    assert made[0]["ok"] is True and "evidence" not in made[0] and not Path(ledger, "evidence").exists()


def test_a_retry_re_verifies_the_copy_and_never_reports_a_copy_that_is_gone_or_changed(env):
    first = run(env, [("doc", DID)])
    path = Path(env[1], first["files"][0]["path"])
    path.unlink()                                                      # the copy is gone but the manifest remains
    with pytest.raises(E.EvidenceRefused) as e:                        # and the original is gone too: refused, not "verified"
        run(env, [("doc", DID)], store=Store(gone=[DID]))
    assert e.value.code == "no_original"
    rebuilt = run(env, [("doc", DID)])                                 # with the original available it is rebuilt, and verified again
    assert Path(env[1], rebuilt["files"][0]["path"]).read_bytes() == DOC
    Path(env[1], rebuilt["files"][0]["path"]).chmod(0o600)
    Path(env[1], rebuilt["files"][0]["path"]).write_bytes(b"tampered!!!!!!!!!!!!")        # same size class, wrong bytes
    again = run(env, [("doc", DID)])
    assert Path(env[1], again["files"][0]["path"]).read_bytes() == DOC


def test_a_copy_made_for_other_files_or_another_conversation_is_not_reused(env):
    tmp, ledger, conv = env
    run(env, [("doc", DID)])
    both = run(env, [("doc", DID), ("image", AID)])                    # a different file set under the same request id: rebuilt to match, never the old one
    assert [f["id"] for f in both["files"]] == [DID, AID]
    other = E.stage(ledger, RID, [("doc", DID), ("image", AID)], store=Store(), media=Media(tmp / "media"), cid="c-ffffffffffff", principal="robert", owner="o")
    assert other["conversation"] == "c-ffffffffffff"


def test_concurrent_identical_requests_both_get_one_intact_copy(env):
    import threading
    out, errs = [], []

    def go():
        try:
            out.append(run(env, [("doc", DID), ("image", AID)]))
        except Exception as exc:
            errs.append(exc)
    ts = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs and all(o == out[0] for o in out)
    for f in out[0]["files"]:
        assert sha(Path(env[1], f["path"]).read_bytes()) == f["sha256"]
    assert sorted(n for n in os.listdir(Path(env[1], "evidence")) if n.startswith(".") and n != ".locks") == []        # no temp folders left behind


def test_quota_contention_lets_exactly_the_fitting_requests_through_and_says_cleanup_is_needed(env, monkeypatch):
    import threading
    one = E._used(os.path.join(env[1], "evidence")) if os.path.isdir(os.path.join(env[1], "evidence")) else 0
    run(env, [("doc", DID)], rid="r-" + "f" * 24)                        # measure what one request really occupies (its file and its manifest)
    one = E._used(os.path.join(env[1], "evidence")) - one
    shutil.rmtree(Path(env[1], "evidence", "r-" + "f" * 24))
    monkeypatch.setattr(E, "ALL_MAX_BYTES", one + len(DOC) + E.MANIFEST_RESERVE + 1)         # room for two requests, not three
    res = {}

    def go(n):
        rid = "r-" + f"{n:x}".rjust(24, "0")
        try:
            res[rid] = run(env, [("doc", DID)], rid=rid)
        except E.EvidenceRefused as exc:
            res[rid] = exc
    ts = [threading.Thread(target=go, args=(n,)) for n in range(1, 5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    ok = [r for r in res.values() if isinstance(r, dict)]
    refused = [r for r in res.values() if isinstance(r, E.EvidenceRefused)]
    assert len(ok) == 2 and len(refused) == 2 and all(r.code == "quota" for r in refused)
    assert "cleared" in refused[0].message and "waiting will not fix it" in refused[0].message and "after a few days" not in refused[0].message
    for r in refused:
        assert not Path(env[1], "evidence", "r-" + "0" * 23 + "x").exists()


def test_a_planted_symlink_for_the_evidence_folder_is_refused(env):
    tmp, ledger, conv = env
    elsewhere = tmp / "elsewhere"
    elsewhere.mkdir()
    os.symlink(elsewhere, Path(ledger, "evidence"))
    with pytest.raises(E.EvidenceRefused) as e:
        run(env, [("doc", DID)])
    assert e.value.code == "unavailable" and list(elsewhere.iterdir()) == []


def test_the_quota_reserves_its_bytes_through_writing_not_just_at_the_count(env, monkeypatch):
    """Codex's interleaving: with room for ONE request, two that both pass the byte count before either has written a file must not both be admitted."""
    import threading
    one_dir = Path(env[1], "evidence")
    run(env, [("doc", DID)], rid="r-" + "e" * 24)
    one = E._used(str(one_dir))
    shutil.rmtree(Path(one_dir, "r-" + "e" * 24))
    monkeypatch.setattr(E, "ALL_MAX_BYTES", len(DOC) + E.MANIFEST_RESERVE + one // 2)               # room for exactly one
    gate = threading.Barrier(2, timeout=5)
    real_open = os.open

    def gated_open(path, flags, *a, **k):
        if str(path).endswith("-Invoice_42.pdf") or "01-Invoice" in str(path):
            try:
                gate.wait(timeout=0.5)                                    # both writers arrive at their first file together, if the lock lets them
            except threading.BrokenBarrierError:
                pass
        return real_open(path, flags, *a, **k)
    monkeypatch.setattr(E.os, "open", gated_open)
    res = {}

    def go(n):
        rid = "r-" + f"{n:x}".rjust(24, "0")
        try:
            res[rid] = run(env, [("doc", DID)], rid=rid)
        except E.EvidenceRefused as exc:
            res[rid] = exc
    ts = [threading.Thread(target=go, args=(n,)) for n in (1, 2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(type(v).__name__ for v in res.values()) == ["EvidenceRefused", "dict"]
    assert E._used(str(one_dir)) <= E.ALL_MAX_BYTES

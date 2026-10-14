"""Files for the shared Chief of Staff / MC agent in production: verified private copies in ONE folder under the portal's shared data mount, the agent told the path it sees.
``staged`` is the only claim made; nothing says a file was opened or a picture inspected. Scripted store, picture library and CoS; no model, no network."""
from __future__ import annotations

import contextlib
import hashlib
import io
import os
import stat
import uuid
from pathlib import Path

import pytest

from floor_helpers import load_portal, write_headers  # noqa: F401
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401
from board_media_helpers import image_bytes
from test_mc_streaming import _fresh_dispatch_state, streaming  # noqa: F401
from test_mc_turn_capture import mc_dir, mc_lines  # noqa: F401
from test_mc_turns import API, FakeRuntime, _keep, _openclaw, _turn_on, turned  # noqa: F401
from test_mc_upload_reading import _ask, _doc, _new, _note
from test_mc_file_delivery import AID, BIG_DOC, CID, DID, Media, PIC, Store, _with_picture, sha

from core.agent_requests import evidence_files as EF
from minimoi_portal.guild_ui import mc_files as M
from minimoi_portal.guild_ui.mc import CachedHealth
from minimoi_portal.guild_ui.mc.cos import ChiefOfStaffBackend
from minimoi_portal.guild_ui.mc_files import EvidenceRefused

AGENT_DIR = "/agent/data/guild/mc-evidence"
BASE = "http://cos-scheduler:8769"
IDS = {M.ENV_COS_UID: str(os.geteuid() or 1000), M.ENV_COS_GID: str(os.getegid() or 1000)}
COPY_OWNER = (int(IDS[M.ENV_COS_UID]), int(IDS[M.ENV_COS_GID]))


@pytest.fixture(autouse=True)
def copy_identity(monkeypatch):
    # The test process stages for its own identity; production explicitly uses 1000:1000.
    for name, value in IDS.items():
        monkeypatch.setenv(name, value)


class Resp:
    status_code = 200

    def __init__(self, reply="Read it."):
        self._reply = reply

    def json(self):
        return {"reply": self._reply}


def cos_backend(accepts):
    sent = []

    def post(url, json=None, timeout=None, **kw):
        sent.append(json)
        return Resp()
    return ChiefOfStaffBackend(BASE, http_get=lambda *a, **k: Resp(), http_post=post, accepts_files=accepts), sent


def wire_cos(portal, accepts=True):
    backend, sent = cos_backend(accepts)
    services = portal.app.extensions["guild_ui_next"]["services"]
    services.mc, services.mc_health, services.mc_turns = backend, CachedHealth(backend), True
    return sent


# ── configuration ──
def test_the_two_folders_are_server_configuration_only_and_must_be_plain_absolute_mc_evidence_paths():
    ok = {**IDS, M.ENV_COS_DIR: "/app/runtime/guild/mc-evidence", M.ENV_COS_AGENT_DIR: AGENT_DIR}
    assert M.cos_dirs(ok) == ("/app/runtime/guild/mc-evidence", AGENT_DIR)
    for bad in ({}, {M.ENV_COS_DIR: ok[M.ENV_COS_DIR]}, {M.ENV_COS_AGENT_DIR: AGENT_DIR},
                {**ok, M.ENV_COS_DIR: "relative/mc-evidence"}, {**ok, M.ENV_COS_AGENT_DIR: "/agent/../etc/mc-evidence"}, {**ok, M.ENV_COS_DIR: "/app/runtime/guild/evidence"},
                {**ok, M.ENV_COS_AGENT_DIR: "/agent/data/guild/mc-evidence/"}, {**ok, M.ENV_COS_DIR: "/app/x\x00/mc-evidence"}, {**ok, M.ENV_COS_DIR: ""}, {**ok, M.ENV_COS_DIR: 7}):
        assert M.cos_dirs(bad) is None, bad
    assert M.cos_dirs() is None


def test_the_backend_takes_files_only_when_both_folders_are_configured():
    assert ChiefOfStaffBackend.from_env({"COS_BACKEND": BASE}).accepts_files is False
    assert ChiefOfStaffBackend.from_env({"COS_BACKEND": BASE, M.ENV_COS_DIR: "/app/runtime/guild/mc-evidence"}).accepts_files is False
    both = {**IDS, "COS_BACKEND": BASE, M.ENV_COS_DIR: "/app/runtime/guild/mc-evidence", M.ENV_COS_AGENT_DIR: AGENT_DIR}
    assert ChiefOfStaffBackend.from_env(both).accepts_files is True


def test_production_admits_the_cos_folders_but_still_holds_off_the_master_craftsman_host_root():
    from minimoi_portal.guild_mounts import production_environ
    raw = {**IDS, "MINIMOI_GUILD_CHAT": "cos", M.ENV: "/Users/me/minimoi-staging/data/guild/agent_requests", M.ENV_COS_DIR: "/app/runtime/guild/mc-evidence", M.ENV_COS_AGENT_DIR: AGENT_DIR}
    admitted = production_environ(raw)
    assert M.host_root(admitted) is None and M.cos_dirs(admitted) == ("/app/runtime/guild/mc-evidence", AGENT_DIR)


# ── the copy itself ──
def test_a_copy_is_private_verified_and_named_by_the_path_the_agent_sees(tmp_path):
    portal = tmp_path / "guild" / "mc-evidence"
    (tmp_path / "media" / "o" / "ab").mkdir(parents=True)
    (tmp_path / "media" / "o" / "ab" / "pic.png").write_bytes(PIC)
    got = M.stage(str(portal.parent), os.path.dirname(AGENT_DIR), channel="portal-chat", scope=CID, principal="robert", owner="o", turn_id="turn-1",
                  chosen=[("image", AID), ("doc", DID)], store=Store(), media=Media(tmp_path / "media"), folder=M.COS_FOLDER, copy_owner=COPY_OWNER)
    assert set(got) == {AID, DID} and got.folder == "mc-evidence"
    rid_dirs = [p for p in portal.iterdir() if p.name.startswith("r-")]
    assert len(rid_dirs) == 1 and got.rid == rid_dirs[0].name
    for fid, original in ((AID, PIC), (DID, BIG_DOC)):
        row = got[fid]
        assert row["path"].startswith(f"{AGENT_DIR}/{got.rid}/") and row["sha256"] == sha(original) and row["bytes"] == len(original)
        copy = rid_dirs[0] / os.path.basename(row["path"])
        assert copy.read_bytes() == original and stat.S_IMODE(copy.stat().st_mode) == 0o600
    assert stat.S_IMODE(portal.stat().st_mode) == 0o700 and stat.S_IMODE(rid_dirs[0].stat().st_mode) == 0o700
    assert not (tmp_path / "guild" / "evidence").exists()                                              # nothing in the Mac-style ledger folder
    again = M.stage(str(portal.parent), os.path.dirname(AGENT_DIR), channel="portal-chat", scope=CID, principal="robert", owner="o", turn_id="turn-1",
                    chosen=[("image", AID), ("doc", DID)], store=Store(), media=Media(tmp_path / "media"), folder=M.COS_FOLDER, copy_owner=COPY_OWNER)
    assert again.rid == got.rid and {k: v["sha256"] for k, v in again.items()} == {k: v["sha256"] for k, v in got.items()}
    M.discard(got)
    assert not rid_dirs[0].exists()


@pytest.mark.parametrize("folder", ["../x", "a/b", "A", "", ".", "..", "x" * 40, "mc evidence", "/abs"])
def test_a_folder_name_is_one_plain_word_never_a_path(tmp_path, folder):
    with pytest.raises(EvidenceRefused) as e:
        EF.stage_files(str(tmp_path), "r-" + "0" * 24, [["doc", "d"]], lambda: [("doc", "d", "n", b"x")], "c", file_max=10, all_max=100, reserve=0, folder=folder)
    assert e.value.code == "bad_request" and not any(tmp_path.iterdir())


# ── through the real portal turn, a scripted CoS that records exactly what leaves ──
def test_a_picture_turn_stages_a_private_copy_and_tells_the_agent_only_that_it_was_staged(turned, tmp_path, monkeypatch):
    portal = tmp_path / "guild" / "mc-evidence"
    monkeypatch.setenv(M.ENV_COS_DIR, str(portal))
    monkeypatch.setenv(M.ENV_COS_AGENT_DIR, AGENT_DIR)
    monkeypatch.delenv(M.ENV, raising=False)
    client, token, cid, asset, png, _ledger = _with_picture(turned, tmp_path)
    sent = wire_cos(turned, accepts=True)
    body = _ask(client, token, _note(client, token, cid)["request_id"], cid, [asset]).get_json()
    assert body["status"] == "answered" and body["backend_kind"] == "cos"
    [entry] = body["files"]
    assert entry["status"] == "staged" and "not known" in entry["reason"]
    [copy] = [p for p in portal.glob("r-*/*") if p.name != "MANIFEST.json"]
    assert copy.read_bytes()[:4] == b"\x89PNG" and stat.S_IMODE(copy.stat().st_mode) == 0o600
    [call] = sent
    assert set(call) == {"text", "channel", "conversation_id", "request_id"}                          # the CoS contract is unchanged
    assert f"{AGENT_DIR}/{copy.parent.name}/{copy.name}" in call["text"] and hashlib.sha256(copy.read_bytes()).hexdigest()[:16] in call["text"]
    assert "\x89" not in call["text"] and str(tmp_path) not in call["text"]                           # no image bytes, no portal-side path
    assert not (tmp_path / "guild" / "evidence").exists()


def test_without_the_cos_folders_a_note_with_files_is_refused_as_before_and_nothing_is_sent_or_copied(turned, tmp_path, monkeypatch):
    for name in (M.ENV, M.ENV_COS_DIR, M.ENV_COS_AGENT_DIR):
        monkeypatch.delenv(name, raising=False)
    client, token, cid, asset, _png, _ledger = _with_picture(turned, tmp_path)
    sent = wire_cos(turned, accepts=False)
    r = _ask(client, token, _note(client, token, cid)["request_id"], cid, [asset])
    assert not sent and r.get_json().get("status") != "answered" and not (tmp_path / "guild").exists()


def test_a_failed_copy_keeps_the_text_path_and_says_the_picture_was_not_offered(turned, tmp_path, monkeypatch):
    monkeypatch.setenv(M.ENV_COS_DIR, str(tmp_path / "guild" / "mc-evidence"))
    monkeypatch.setenv(M.ENV_COS_AGENT_DIR, AGENT_DIR)

    def boom(*a, **k):
        raise EvidenceRefused("quota", "The evidence folder is full, so nothing was sent.")
    monkeypatch.setattr(M, "stage", boom)
    client, token, cid, asset, _png, _ledger = _with_picture(turned, tmp_path)
    doc = _doc(client, token, cid, "Some words.", "words.txt")
    sent = wire_cos(turned, accepts=True)
    body = _ask(client, token, _note(client, token, cid)["request_id"], cid, [asset, doc]).get_json()
    assert body["status"] == "answered" and [(e["name"], e["status"]) for e in body["files"]] == [("board.png", "not_read"), ("words.txt", "read")]
    assert "Some words." in sent[0]["text"] and AGENT_DIR not in sent[0]["text"]


@pytest.mark.parametrize("bad", [None, "", "0", "-1", "1.0", " 1000", "1000 ", "2147483648", "١٠٠٠", 1000, "9" * 100])
def test_invalid_or_missing_runtime_identity_disables_files(bad):
    config = {**IDS, M.ENV_COS_DIR: "/app/runtime/guild/mc-evidence", M.ENV_COS_AGENT_DIR: AGENT_DIR, "COS_BACKEND": BASE}
    for key in (M.ENV_COS_UID, M.ENV_COS_GID):
        broken = {**config, key: bad}
        assert M.cos_owner(broken) is None and M.cos_dirs(broken) is None
        assert ChiefOfStaffBackend.from_env(broken).accepts_files is False


def owned_copy(tmp_path, **kw):
    return EF.stage_files(str(tmp_path), "r-" + "b" * 24, [["doc", "d"]], lambda: [("doc", "d", "n", b"proof")], "c",
                          file_max=20, all_max=10000, reserve=0, folder=M.COS_FOLDER, owner=COPY_OWNER, **kw)


def test_explicit_owner_has_private_bytes_and_manifest_but_portal_private_external_locks(tmp_path):
    first = owned_copy(tmp_path)
    base = tmp_path / M.COS_FOLDER
    target = base / first["request_id"]
    for path, mode in ((base, 0o700), (target, 0o700), (target / "MANIFEST.json", 0o600), (target / "01-n", 0o600)):
        st = path.lstat()
        assert (st.st_uid, st.st_gid, stat.S_IMODE(st.st_mode)) == (*COPY_OWNER, mode)
    locks = tmp_path / ".mc-evidence-locks"
    st = locks.lstat()
    assert (st.st_uid, st.st_gid, stat.S_IMODE(st.st_mode)) == (os.geteuid(), os.getegid(), 0o700)
    assert not (base / ".locks").exists()
    assert (target / "01-n").read_bytes() == b"proof" and owned_copy(tmp_path) == first


@pytest.mark.parametrize("location", ["base", "request", "file", "manifest", "locks"])
def test_existing_nonprivate_paths_are_refused_without_repair(tmp_path, location):
    manifest = owned_copy(tmp_path)
    base = tmp_path / M.COS_FOLDER
    target = base / manifest["request_id"]
    path = {"base": base, "request": target, "file": target / "01-n", "manifest": target / "MANIFEST.json", "locks": tmp_path / ".mc-evidence-locks"}[location]
    path.chmod(0o755 if path.is_dir() else 0o644)
    with pytest.raises(EvidenceRefused):
        owned_copy(tmp_path)
    assert stat.S_IMODE(path.stat().st_mode) == (0o755 if path.is_dir() else 0o644)
    assert (target / "01-n").read_bytes() == b"proof"


def test_existing_wrong_owner_is_refused_not_reclaimed(tmp_path, monkeypatch):
    owned_copy(tmp_path)
    original = EF._private
    monkeypatch.setattr(EF, "_private", lambda st, owner, mode, **kw: False if mode == 0o600 else original(st, owner, mode, **kw))
    with pytest.raises(EvidenceRefused):
        owned_copy(tmp_path)
    monkeypatch.setattr(EF, "_private", original)
    assert owned_copy(tmp_path)["files"][0]["sha256"] == sha(b"proof")


def test_chown_failure_never_publishes_a_request(tmp_path, monkeypatch):
    def denied(*a, **kw):
        raise PermissionError("synthetic")
    monkeypatch.setattr(EF.os, "fchown", denied)
    with pytest.raises(EvidenceRefused):
        owned_copy(tmp_path)
    assert not list((tmp_path / M.COS_FOLDER).glob("r-*"))


def test_opt_in_ownership_refuses_symlink_base(tmp_path):
    elsewhere = tmp_path / "other"
    elsewhere.mkdir()
    (tmp_path / M.COS_FOLDER).symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(EvidenceRefused):
        owned_copy(tmp_path)
    assert not list(elsewhere.iterdir())



def test_agent_planted_temporary_symlink_cannot_redirect_root_writes(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    base = tmp_path / M.COS_FOLDER
    original = EF._owned_dir
    attacked = []

    @contextlib.contextmanager
    def attack(parent_fd, name, owner, **kw):
        with original(parent_fd, name, owner, **kw) as fd:
            if name.endswith(".tmp"):
                # The agent plants the old vulnerable construction pathname.
                (base / name).symlink_to(outside, target_is_directory=True)
                attacked.append(name)
            yield fd
    monkeypatch.setattr(EF, "_owned_dir", attack)
    manifest = owned_copy(tmp_path)
    assert len(attacked) == 1 and list(outside.iterdir()) == []
    assert (base / manifest["request_id"] / "01-n").read_bytes() == b"proof"
    assert not list((tmp_path / ".mc-evidence-locks").glob("*.tmp"))


def test_reuse_leaf_symlink_swap_before_open_is_refused_without_reading_target(tmp_path, monkeypatch):
    manifest = owned_copy(tmp_path)
    leaf = tmp_path / M.COS_FOLDER / manifest["request_id"] / "01-n"
    outside = tmp_path / "outside"
    outside.write_bytes(b"private-not-evidence")
    original = EF._read_owned
    attacks = []
    def attack(parent_fd, name, owner, maximum):
        if name == "01-n" and not attacks:
            leaf.unlink()
            leaf.symlink_to(outside)
            attacks.append(name)
        return original(parent_fd, name, owner, maximum)
    monkeypatch.setattr(EF, "_read_owned", attack)
    with pytest.raises(EvidenceRefused):
        owned_copy(tmp_path)
    assert attacks == ["01-n"] and outside.read_bytes() == b"private-not-evidence"

"""The job-scoped inbox (amendment B3): copies are verified and private, ids cannot reach outside, and a job's copy
outlives Remove on the conversation. Synthetic content only."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

from doc_fixtures import docx_bytes
from floor_helpers import load_portal, write_headers  # noqa: F401
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401
from test_mc_turns import API, FakeRuntime, _keep, _openclaw, _turn_on, turned  # noqa: F401
from test_mc_upload_reading import _doc, _new, _upload

from minimoi_portal.guild_ui import job_inbox
from minimoi_portal.guild_ui.conversations import conversations_of

JOB = "j-0123456789abcdef"


@pytest.fixture
def world(turned, tmp_path):
    client = turned.owner()
    token = turned.csrf(client)
    cid = _new(client, token)
    store = conversations_of(turned.app.extensions["guild_ui_next"]["services"])
    folder = str(tmp_path / "guild-data")
    os.makedirs(folder)
    return client, token, cid, store, folder, turned


def _who(world):
    _client, _token, cid, store, _folder, _turned = world
    return json.loads((Path(store.dir) / f"{cid}.json").read_text())["principal"]


def test_a_docx_is_copied_byte_for_byte_with_a_manifest_and_private_modes(world):
    client, token, cid, store, folder, turned = world
    raw = docx_bytes(["one", "two"])
    doc = _upload(client, token, cid, raw, "resume.docx").get_json()["document"]["id"]
    manifest = job_inbox.stage(store, folder, cid, _who(world), JOB, [doc])
    target = Path(job_inbox.inbox_path(folder, JOB))
    [entry] = manifest["files"]
    copy = target / entry["path"]
    assert copy.read_bytes() == raw and entry["sha256"] == hashlib.sha256(raw).hexdigest() and entry["bytes"] == len(raw)
    assert stat.S_IMODE(target.stat().st_mode) == 0o700 and stat.S_IMODE(copy.stat().st_mode) == 0o400
    assert json.loads((target / "MANIFEST.json").read_text()) == manifest


def test_remove_on_the_conversation_does_not_delete_a_copy_a_job_already_has(world):
    client, token, cid, store, folder, turned = world
    doc = _doc(client, token, cid, "still needed by the job", "keep.txt")
    manifest = job_inbox.stage(store, folder, cid, _who(world), JOB, [doc])
    client.post(f"{API}/conversations/{cid}/documents/remove", json=keyed(document_id=doc), headers=write_headers(token))
    assert (Path(job_inbox.inbox_path(folder, JOB)) / manifest["files"][0]["path"]).read_text() == "still needed by the job"
    with pytest.raises(job_inbox.InboxRefused) as e:
        job_inbox.stage(store, folder, cid, _who(world), "j-aaaaaaaaaaaaaaaa", [doc])        # but a new job cannot get it
    assert e.value.code == "no_original"


@pytest.mark.parametrize("bad_job", ["../x", "j-123", "", "j-0123456789abcdeg", None])
def test_a_job_id_that_is_not_one_of_ours_is_refused(world, bad_job):
    client, token, cid, store, folder, turned = world
    doc = _doc(client, token, cid, "x", "x.txt")
    with pytest.raises(job_inbox.InboxRefused) as e:
        job_inbox.stage(store, folder, cid, _who(world), bad_job, [doc])
    assert e.value.code == "bad_job_id" and not os.path.exists(os.path.join(folder, "jobs"))


@pytest.mark.parametrize("ids", [[], ["../../etc/passwd"], ["d-0000000000000000"], "d-0000000000000000", [1]])
def test_ids_that_are_not_the_owners_files_are_refused_and_leave_nothing(world, ids):
    client, token, cid, store, folder, turned = world
    with pytest.raises(job_inbox.InboxRefused):
        job_inbox.stage(store, folder, cid, _who(world), JOB, ids)
    assert not os.path.exists(job_inbox.inbox_path(folder, JOB))


def test_a_file_from_another_conversation_cannot_be_staged(world):
    client, token, cid, store, folder, turned = world
    other = _new(client, token)
    doc = _doc(client, token, other, "belongs elsewhere", "o.txt")
    with pytest.raises(job_inbox.InboxRefused) as e:
        job_inbox.stage(store, folder, cid, _who(world), JOB, [doc])
    assert e.value.code == "no_original" and not os.path.exists(job_inbox.inbox_path(folder, JOB))


def test_a_tampered_original_is_not_copied_and_nothing_is_left(world):
    client, token, cid, store, folder, turned = world
    good = _doc(client, token, cid, "good", "g.txt")
    bad = _doc(client, token, cid, "bad", "b.txt")
    (Path(store.dir) / "docs" / cid / f"{bad}.orig").write_bytes(b"swapped")
    with pytest.raises(job_inbox.InboxRefused) as e:
        job_inbox.stage(store, folder, cid, _who(world), JOB, [good, bad])
    assert e.value.code == "no_original" and not os.path.exists(job_inbox.inbox_path(folder, JOB))


def test_a_symlinked_original_is_not_followed_into_the_inbox(world, tmp_path):
    client, token, cid, store, folder, turned = world
    doc = _doc(client, token, cid, "real", "r.txt")
    secret = tmp_path / "secret"
    secret.write_text("outside")
    p = Path(store.dir) / "docs" / cid / f"{doc}.orig"
    p.unlink()
    os.symlink(secret, p)
    with pytest.raises(job_inbox.InboxRefused):
        job_inbox.stage(store, folder, cid, _who(world), JOB, [doc])
    assert not os.path.exists(job_inbox.inbox_path(folder, JOB))


def test_a_hostile_file_name_stays_inside_the_inbox(world):
    client, token, cid, store, folder, turned = world
    doc = _doc(client, token, cid, "x", "..evil name.txt")
    manifest = job_inbox.stage(store, folder, cid, _who(world), JOB, [doc])
    [entry] = manifest["files"]
    assert "/" not in entry["path"] and not entry["path"].startswith(".")
    target = Path(job_inbox.inbox_path(folder, JOB))
    assert sorted(p.name for p in target.iterdir()) == sorted([entry["path"], "MANIFEST.json"])


def test_the_total_quota_refuses_with_a_plain_reason_and_leaves_nothing(world, monkeypatch):
    client, token, cid, store, folder, turned = world
    doc = _doc(client, token, cid, "twelve bytes", "q.txt")
    monkeypatch.setattr(job_inbox, "ALL_INBOXES_MAX_BYTES", 5)
    with pytest.raises(job_inbox.InboxRefused) as e:
        job_inbox.stage(store, folder, cid, _who(world), JOB, [doc])
    assert e.value.code == "quota" and not os.path.exists(job_inbox.inbox_path(folder, JOB))


def test_a_crash_while_copying_removes_the_partial_inbox(world, monkeypatch):
    client, token, cid, store, folder, turned = world
    a, b = _doc(client, token, cid, "first", "a.txt"), _doc(client, token, cid, "second", "b.txt")
    real = os.fsync
    calls = []

    def boom(fd):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("disk full")
        return real(fd)
    monkeypatch.setattr(job_inbox.os, "fsync", boom)
    with pytest.raises(job_inbox.InboxRefused) as e:
        job_inbox.stage(store, folder, cid, _who(world), JOB, [a, b])
    assert e.value.code == "unavailable" and not os.path.exists(job_inbox.inbox_path(folder, JOB))


def test_a_second_inbox_for_the_same_job_is_refused(world):
    client, token, cid, store, folder, turned = world
    doc = _doc(client, token, cid, "x", "x.txt")
    job_inbox.stage(store, folder, cid, _who(world), JOB, [doc])
    with pytest.raises(job_inbox.InboxRefused) as e:
        job_inbox.stage(store, folder, cid, _who(world), JOB, [doc])
    assert e.value.code == "exists"


def test_sweep_removes_only_old_job_inboxes_and_spares_running_ones_and_strangers(world):
    client, token, cid, store, folder, turned = world
    doc = _doc(client, token, cid, "x", "x.txt")
    old, running, fresh = "j-1111111111111111", "j-2222222222222222", "j-3333333333333333"
    for j in (old, running, fresh):
        job_inbox.stage(store, folder, cid, _who(world), j, [doc])
    stranger = Path(job_inbox.inbox_root(folder)) / "not-a-job"
    stranger.mkdir()
    week = job_inbox.SWEEP_AFTER_SECONDS
    import time
    for j in (old, running):
        os.utime(job_inbox.inbox_path(folder, j), (time.time() - week - 10,) * 2)
    removed = job_inbox.sweep(folder, keep={running})
    assert removed == [old]
    assert not os.path.exists(job_inbox.inbox_path(folder, old))
    assert os.path.exists(job_inbox.inbox_path(folder, running)) and os.path.exists(job_inbox.inbox_path(folder, fresh)) and stranger.exists()
    assert job_inbox.remove_inbox(folder, fresh) is True and job_inbox.remove_inbox(folder, fresh) is False
    assert (Path(store.dir) / "docs" / cid / f"{doc}.orig").exists()               # the conversation's own original is untouched


def test_too_many_or_duplicate_files_are_refused(world):
    client, token, cid, store, folder, turned = world
    doc = _doc(client, token, cid, "x", "x.txt")
    with pytest.raises(job_inbox.InboxRefused):
        job_inbox.stage(store, folder, cid, _who(world), JOB, [doc, doc])
    with pytest.raises(job_inbox.InboxRefused):
        job_inbox.stage(store, folder, cid, _who(world), JOB, [f"d-{i:016x}" for i in range(6)])


def test_a_copy_that_does_not_match_its_original_is_caught_and_nothing_is_left(world, monkeypatch):
    client, token, cid, store, folder, turned = world
    doc = _doc(client, token, cid, "exact bytes matter", "e.txt")
    real = job_inbox.os.fdopen

    class Lossy:
        def __init__(self, f):
            self.f = f

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.f.close()

        def write(self, data):
            return self.f.write(data[:-1])               # a copy that silently loses its last byte

        def __getattr__(self, name):
            return getattr(self.f, name)

    monkeypatch.setattr(job_inbox.os, "fdopen", lambda fd, mode="r", *a, **k: Lossy(real(fd, mode, *a, **k)) if mode == "wb" else real(fd, mode, *a, **k))
    with pytest.raises(job_inbox.InboxRefused) as e:
        job_inbox.stage(store, folder, cid, _who(world), JOB, [doc])
    assert e.value.code == "copy_mismatch" and not os.path.exists(job_inbox.inbox_path(folder, JOB))

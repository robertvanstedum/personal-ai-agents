"""career-sync: publish → apply → rollback on fixture trees, with a stand-in for ``age`` (spec 1.2 data map §4, tests T3 and T5)."""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("career_sync", ROOT / "scripts" / "operations" / "career_sync.py")
cs = importlib.util.module_from_spec(spec)
sys.modules["career_sync"] = cs
spec.loader.exec_module(cs)

RECIPIENT = "age1" + "q" * 58

# A stand-in with age's command line: "-r R... -o OUT IN" prefixes a marker and a random nonce (so, like age, output
# differs on every run); "-d -i ID -o OUT IN" checks and strips them.
FAKE_AGE = """#!/bin/sh
if [ "$1" = "--version" ]; then echo "v1.3.2-fake"; exit 0; fi
if [ "$1" = "-d" ]; then
  out="$5"; in="$6"
  head -c 8 "$in" | grep -q FAKEAGE1 || exit 1
  tail -c +26 "$in" > "$out"; exit 0
fi
while [ "$1" = "-r" ]; do shift 2; done
out="$2"; in="$3"
{ printf 'FAKEAGE1%016x\\n' "$(od -An -N8 -tu8 /dev/urandom | tr -d ' ')"; cat "$in"; } > "$out"
"""


@pytest.fixture
def env(tmp_path):
    age = tmp_path / "age"
    age.write_text(FAKE_AGE)
    age.chmod(0o755)
    ws = tmp_path / "workspace"
    (ws / "CV").mkdir(parents=True)
    (ws / "specs").mkdir()
    (ws / "other").mkdir()
    (ws / "CV" / "resume.md").write_text("resume canary line\n")
    (ws / "CV" / "~$resume.docx").write_text("lock")
    (ws / "CV" / ".DS_Store").write_text("x")
    (ws / "specs" / "spec.md").write_text("spec\n")
    (ws / "other" / "private.md").write_text("never published\n")
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    subprocess.run(["git", "-C", str(ws), "add", "CV/resume.md", "specs/spec.md"], check=True)
    subprocess.run(["git", "-C", str(ws), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], check=True)
    store = tmp_path / "store"
    store.mkdir()
    private = tmp_path / "private"
    uid, gid = os.getuid(), os.getgid()
    mac = {"workspace": str(ws), "git_repo": str(ws), "publish_set": ["CV", "specs"], "never_copy": [],
           "recipients": [RECIPIENT], "dest": f"file://{store}", "age_bin": str(age)}
    host = {"private_root": str(private), "dest": f"file://{store}", "age_bin": str(age), "identity_file": str(tmp_path / "id"),
            "test_without_root": True,
            "readers": [{"name": "work", "dir": str(private / "professional"), "uid": uid, "gid": gid, "work_roots": True},
                        {"name": "agent-a", "dir": str(private / "professional-agent-a"), "uid": uid, "gid": gid}],
            "work_roots_mount": "/minimoi/private/professional",
            "work_roots": [{"subject": "career", "ref": "active_cv", "path": "CV", "context_class": "robert_source"}]}
    (tmp_path / "id").write_text("AGE-SECRET-KEY-1" + "Q" * 58)
    return {"ws": ws, "store": store, "private": private, "mac": mac, "host": host}


def objects(store):
    return sorted(p.name for p in (store / "career-sync").glob("*.tar.age"))


def test_publish_skips_locks_and_unlisted_folders_and_marks_dirty(env):
    (env["ws"] / "CV" / "draft.md").write_text("untracked\n")
    m = cs.collect(env["ws"], ["CV", "specs"], [], env["ws"])
    paths = {f["path"]: f for f in m["files"]}
    assert set(paths) == {"CV/resume.md", "CV/draft.md", "specs/spec.md"}
    assert paths["CV/draft.md"]["dirty"] is True and paths["CV/resume.md"]["dirty"] is False
    assert m["head"] and len(m["manifest_hash"]) == 64


def test_never_copy_and_symlink_refusal(env):
    m = cs.collect(env["ws"], ["CV", "specs"], ["spec.md"], env["ws"])
    assert {f["path"] for f in m["files"]} == {"CV/resume.md"}
    (env["ws"] / "CV" / "link.md").symlink_to(env["ws"] / "other" / "private.md")
    with pytest.raises(cs.Refused) as e:
        cs.collect(env["ws"], ["CV"], [], env["ws"])
    assert e.value.code == "symlink_in_publish_set"


def test_same_tree_twice_stores_one_object(env):
    a = cs.publish(env["mac"])
    b = cs.publish(env["mac"])
    assert a["manifest_hash"] == b["manifest_hash"]
    assert a["stored_new_object"] is True and b["stored_new_object"] is False
    assert len(objects(env["store"])) == 1
    raw = (env["store"] / "career-sync" / objects(env["store"])[0]).read_bytes()
    assert raw.startswith(b"FAKEAGE1") and b"resume canary line" not in raw   # passed through age, compressed


def test_apply_creates_release_per_reader_with_work_roots(env):
    pub = cs.publish(env["mac"])
    res = cs.apply(env["host"])
    assert res["result"] == "applied" and res["manifest_hash"] == pub["manifest_hash"]
    h = pub["manifest_hash"]
    for name in ("professional", "professional-agent-a"):
        rdir = env["private"] / name
        assert os.readlink(rdir / "current") == f"releases/{h}"
        f = rdir / "current" / "tree" / "CV" / "resume.md"
        assert f.read_text() == "resume canary line\n"
        assert stat.S_IMODE(f.stat().st_mode) == 0o400
        assert stat.S_IMODE((rdir / "releases" / h).stat().st_mode) == 0o700
        assert not (rdir / "current" / "tree" / "other").exists()
        assert json.loads((rdir / "RECEIPT.json").read_text())["manifest_hash"] == h
    roots = json.loads((env["private"] / "professional" / "work_source_roots.json").read_text())
    assert roots == {"career": {"active_cv": {"path": f"/minimoi/private/professional/releases/{h}/tree/CV", "context_class": "robert_source"}}}
    assert "current" not in roots["career"]["active_cv"]["path"]
    assert cs.apply(env["host"])["result"] == "unchanged"


def test_new_publish_keeps_one_previous_and_rollback(env):
    h1 = cs.publish(env["mac"])["manifest_hash"]
    cs.apply(env["host"])
    (env["ws"] / "CV" / "resume.md").write_text("resume v2\n")
    h2 = cs.publish(env["mac"])["manifest_hash"]
    cs.apply(env["host"])
    (env["ws"] / "CV" / "resume.md").write_text("resume v3\n")
    h3 = cs.publish(env["mac"])["manifest_hash"]
    res = cs.apply(env["host"])
    rdir = env["private"] / "professional"
    assert sorted(cs.retained(rdir)) == sorted([h2, h3]) and h1 not in cs.retained(rdir)
    assert res["readers"][0]["pruned"] == [h1]
    back = cs.rollback(env["host"])
    assert back["readers"][0]["to"] == h2
    assert (rdir / "current" / "tree" / "CV" / "resume.md").read_text() == "resume v2\n"
    assert h2 in json.loads((rdir / "work_source_roots.json").read_text())["career"]["active_cv"]["path"]


def test_tampered_object_is_quarantined_and_current_stays(env):
    h1 = cs.publish(env["mac"])["manifest_hash"]
    cs.apply(env["host"])
    (env["ws"] / "CV" / "resume.md").write_text("changed\n")
    pub = cs.publish(env["mac"])
    obj = env["store"] / pub["object"]
    obj.write_bytes(obj.read_bytes() + b"tamper")
    with pytest.raises(cs.Refused) as e:
        cs.apply(env["host"])
    assert e.value.code == "ciphertext_hash_mismatch"
    rdir = env["private"] / "professional"
    assert cs.current_release(rdir) == h1
    assert len(list((rdir / "quarantine").iterdir())) == 1


def test_publish_refuses_while_inbound_unpulled(env):
    inbound = env["store"] / "career-inbound"
    inbound.mkdir()
    (inbound / "b1.tar.age").write_bytes(b"x")
    with pytest.raises(cs.Refused) as e:
        cs.publish(env["mac"])
    assert e.value.code == "inbound_not_pulled"
    acks = env["store"] / "career-inbound-acks"
    acks.mkdir()
    (acks / "b1.json").write_text("{}")
    assert cs.publish(env["mac"])["manifest_hash"]


def test_apply_refuses_without_root_unless_test_flag(env):
    cfg = dict(env["host"])
    cfg.pop("test_without_root")
    if os.geteuid() == 0:
        pytest.skip("running as root")
    with pytest.raises(cs.Refused) as e:
        cs.apply(cfg)
    assert e.value.code == "needs_root"


def test_unsafe_archive_member_refused(tmp_path):
    import tarfile, io
    arc = tmp_path / "bad.tar.gz"
    with tarfile.open(arc, "w:gz") as t:
        ti = tarfile.TarInfo("../escape")
        ti.size = 1
        t.addfile(ti, io.BytesIO(b"x"))
    with pytest.raises(cs.Refused) as e:
        cs.safe_extract(arc, tmp_path / "out")
    assert e.value.code == "archive_unsafe"


def test_local_ack_record_is_used_when_configured(env, tmp_path):
    inbound = env["store"] / "career-inbound"
    inbound.mkdir()
    (inbound / "b2.tar.age").write_bytes(b"x")
    acks = tmp_path / "acks"
    cfg = {**env["mac"], "ack_dir": str(acks)}
    with pytest.raises(cs.Refused):
        cs.publish(cfg)
    acks.mkdir()
    (acks / "b2.json").write_text("{}")
    assert cs.publish(cfg)["manifest_hash"]


def test_republishing_an_older_release_points_at_the_stored_ciphertext(env):
    a = cs.publish(env["mac"])
    (env["ws"] / "CV" / "resume.md").write_text("other\n")
    cs.publish(env["mac"])
    (env["ws"] / "CV" / "resume.md").write_text("resume canary line\n")
    again = cs.publish(env["mac"])
    assert again["manifest_hash"] == a["manifest_hash"] and again["stored_new_object"] is False
    pointer = json.loads((env["store"] / "career-sync" / "latest.json").read_text())
    stored = cs.sha256_file(env["store"] / again["object"])
    assert pointer["ciphertext_sha256"] == stored == a["ciphertext_sha256"]
    assert cs.apply(env["host"])["result"] == "applied"      # a fresh host accepts it


def test_after_a_rollback_the_next_release_keeps_the_rolled_back_to_release(env):
    h1 = cs.publish(env["mac"])["manifest_hash"]; cs.apply(env["host"])
    (env["ws"] / "CV" / "resume.md").write_text("v2\n")
    h2 = cs.publish(env["mac"])["manifest_hash"]; cs.apply(env["host"])
    cs.rollback(env["host"])                                  # current h1, h2 kept
    (env["ws"] / "CV" / "resume.md").write_text("v3\n")
    h3 = cs.publish(env["mac"])["manifest_hash"]; cs.apply(env["host"])
    rdir = env["private"] / "professional"
    assert set(cs.retained(rdir)) == {h1, h3}                 # the previous current (h1) is the rollback target, not h2
    assert cs.rollback(env["host"])["readers"][0]["to"] == h1

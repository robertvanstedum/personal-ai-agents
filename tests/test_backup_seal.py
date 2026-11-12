"""Personal stores leave the host only as ciphertext, and the drill proves they restore (#508, spec 1.2 S3, D13)."""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "operations"))
import backup_seal as bs  # noqa: E402

LOCAL = (ROOT / "scripts" / "backup_local.sh").read_text()
S3 = (ROOT / "scripts" / "backup_s3.sh").read_text()

# age's command line: "-R FILE -o OUT IN" writes the real header line, a random nonce and the input; "-d -i ID -o OUT IN"
# checks and strips them. Output differs on every run, like age.
FAKE_AGE = """#!/bin/sh
if [ "$1" = "-d" ]; then
  out="$5"; in="$6"
  head -c 21 "$in" | grep -q 'age-encryption.org/v1' || exit 1
  tail -n +3 "$in" > "$out"; exit 0
fi
[ "$1" = "-R" ] || exit 2
grep -q '^age1' "$2" || exit 1
out="$4"; in="$5"
{ printf 'age-encryption.org/v1\\n%016x\\n' "$(od -An -N8 -tu8 /dev/urandom | tr -d ' ')"; cat "$in"; } > "$out"
"""
SEALED = ["auth", "cos_memory.md", "cos_reminders", "cos_voice", "cos-store", "cos-conversations", "cos-inbox",
          "cos-outbox", "vol-cos-agent-a-state", "vol-cos-agent-a-auth"]


@pytest.fixture
def env(tmp_path):
    age = tmp_path / "age"
    age.write_text(FAKE_AGE)
    age.chmod(0o755)
    rec = tmp_path / "backup.recipients"
    rec.write_text("# Vault backup key\nage1" + "q" * 58 + "\n")
    ident = tmp_path / "identity"
    ident.write_text("AGE-SECRET-KEY-FAKE\n")
    live = tmp_path / "live"
    (live / "cos-store" / "journal").mkdir(parents=True)
    (live / "cos-store" / "journal" / "journal.jsonl").write_text('{"a": 1}\n{"b": 2}\n')
    (live / "cos-store" / "journal" / ".lock").write_text("")
    db = sqlite3.connect(live / "cos-store" / "tool-bindings.sqlite3")
    db.execute("create table t(x)")
    db.execute("insert into t values (1)")
    db.commit()
    db.close()
    (live / "cos_memory.md").write_text("notes\n")
    dated = tmp_path / "backups" / "2026-11-12"
    dated.mkdir(parents=True)
    import shutil
    shutil.copytree(live / "cos-store", dated / "cos-store")
    shutil.copy2(live / "cos_memory.md", dated / "cos_memory.md")
    (dated / "guild").mkdir()
    (dated / "guild" / "x.json").write_text("{}")
    return {"age": str(age), "rec": rec, "ident": ident, "live": live, "dated": dated, "tmp": tmp_path}


def test_seal_leaves_only_ciphertext_for_listed_items(env):
    res = bs.seal(env["dated"], env["rec"], SEALED, env["age"])
    names = sorted(p.name for p in env["dated"].iterdir())
    assert names == ["cos-store.tar.gz.age", "cos_memory.md.tar.gz.age", "guild"]      # guild is not a sealed class
    assert set(res["sealed"]) == {"cos-store", "cos_memory.md"} and "auth" in res["absent"]
    for p in env["dated"].glob("*.age"):
        assert p.read_bytes().startswith(bs.AGE_HEADER)
        assert b"journal.jsonl" not in p.read_bytes()[:30]


def test_drill_restores_and_matches_live(env):
    bs.seal(env["dated"], env["rec"], SEALED, env["age"])
    rep = bs.drill(env["dated"], env["ident"], {"cos-store": env["live"] / "cos-store",
                                                "cos_memory.md": env["live"] / "cos_memory.md"}, env["age"])
    assert rep["ok"]
    store = rep["items"]["cos-store"]
    assert store["matches_live"] and store["files"] == 2 and store["jsonl_lines"] == 2 and store["sqlite_ok"]
    assert rep["items"]["cos_memory.md"]["matches_live"]


def test_a_failed_encryption_moves_plaintext_out_of_the_dated_folder(env, tmp_path):
    broken = tmp_path / "broken-age"
    broken.write_text("#!/bin/sh\nexit 1\n")
    broken.chmod(0o755)
    with pytest.raises(bs.SealError):
        bs.seal(env["dated"], env["rec"], SEALED, str(broken))
    left = sorted(p.name for p in env["dated"].iterdir())
    assert left == ["guild"]                                                # nothing personal for S3 to copy
    hold = env["dated"].parent / ".unsealed-2026-11-12"
    assert (hold / "cos-store").is_dir() and (hold / "cos_memory.md").is_file()
    assert oct(hold.stat().st_mode & 0o777) == "0o700"


def test_no_recipients_means_nothing_leaves_in_plaintext(env):
    env["rec"].write_text("# empty\n")
    with pytest.raises(bs.SealError):
        bs.seal(env["dated"], env["rec"], SEALED, env["age"])
    assert sorted(p.name for p in env["dated"].iterdir()) == ["guild"]


def test_capture_volumes_copies_sqlite_consistently_and_skips_tmp(tmp_path):
    vol = tmp_path / "vol"
    (vol / "agents" / "a").mkdir(parents=True)
    (vol / "tmp" / "plugin").mkdir(parents=True)
    (vol / "tmp" / "plugin" / "big.js").write_text("x")
    con = sqlite3.connect(vol / "agents" / "a" / "agent.sqlite")
    con.execute("pragma journal_mode=wal")
    con.execute("create table s(x)")
    con.execute("insert into s values (42)")
    con.commit()                                     # left open: the -wal file holds the row, as in a running agent
    (vol / "agents" / "a" / "state.json").write_text("{}")
    dest = tmp_path / "dated"
    res = bs.capture_volumes(dest, ["cos-agent-a-state"], resolver=lambda name: str(vol))
    con.close()
    out = dest / "vol-cos-agent-a-state"
    assert res["cos-agent-a-state"] == {"files": 2, "sqlite": 1}
    assert not (out / "tmp").exists() and not list(out.rglob("*-wal"))
    assert sqlite3.connect(out / "agents" / "a" / "agent.sqlite").execute("select x from s").fetchone() == (42,)


def test_the_backup_script_wires_the_new_stores_and_seals_them():
    for src, dest in (("/opt/minimoi/cos/inbox/", "cos-inbox/"), ("/opt/minimoi/cos/outbox/", "cos-outbox/"),
                      ("/opt/minimoi/state/1-2/", "state-1-2/")):
        assert re.search(r'^rsync_path "' + re.escape(src) + r'"\s+"' + re.escape(dest) + '"', LOCAL, re.M), src
    assert "capture-volumes --dest \"$LOCAL_BACKUP\" --volume cos-agent-a-state --volume cos-agent-a-auth" in LOCAL
    seal_cmd = LOCAL[LOCAL.index('"$SEAL" seal'):LOCAL.index("| tee", LOCAL.index('"$SEAL" seal'))]
    assert sorted(re.findall(r"--item (\S+)", seal_cmd)) == sorted(SEALED)
    # Sealing happens before the prune and after every copy.
    assert LOCAL.index('"$SEAL" seal') > LOCAL.index('rsync_path "/opt/minimoi/state/1-2/"')
    assert LOCAL.index('"$SEAL" seal') < LOCAL.index("Pruning backups older")


def test_s3_uploads_only_encrypted_database_dumps():
    dumps = [l for l in S3.splitlines() if "pg_dump" in l and not l.lstrip().startswith("#")]
    assert dumps and all('| "$AGE" -R "$RECIPIENTS" |' in l for l in dumps)
    assert '${NAME}.age"' in S3 and "litellm_keys" in S3


def test_the_helper_is_shipped_to_the_host():
    cfg = json.loads((ROOT / "config" / "host_scripts.json").read_text())
    entry = [e for e in cfg["entries"] if e["repo"] == "scripts/operations/backup_seal.py"]
    assert entry and entry[0]["ship"] and entry[0]["host"] == "/opt/minimoi/scripts/backup_seal.py"


def test_volume_names_match_with_either_compose_prefix():
    vols = ["minimoi-staging-cos-agent-a-state", "minimoi_cos-agent-a-auth", "other"]
    assert bs.match_volume(vols, "cos-agent-a-state") == ["minimoi-staging-cos-agent-a-state"]
    assert bs.match_volume(vols, "cos-agent-a-auth") == ["minimoi_cos-agent-a-auth"]
    assert bs.match_volume(["cos-agent-a-state", "x-cos-agent-a-state"], "cos-agent-a-state") == ["cos-agent-a-state"]

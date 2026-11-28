"""``vault-apply`` with meaning search (#660): the seed (backfill in the batch window), its promotion inside apply's single switch, then
incremental embedding on each apply. Synthetic and local, on the same fixtures as ``test_vault_apply.py``: the host index runner with the
deterministic FakeEmbedder carrying the pinned digest. No Ollama, no docker (the docker argv is checked through an injected ``run``)."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.memory_backup import backup as B
from core.memory_index import policy, search
from core.memory_index.embedder import PIN_DIGEST_FULL
from core.memory_sync import apply as A
from tests.memory_index.helpers import FakeEmbedder, add_record
from tests.vault.test_vault_apply import (Clock, ack, apply, changes_on_mac, current, load, cli, send, statuses,  # noqa: F401
                                          w)

RAW = policy.Caller(tier="all")
NIGHT = datetime(2026, 11, 26, 8, 0, tzinfo=timezone.utc)        # 02:00 Chicago: inside the batch window
DAY = datetime(2026, 11, 26, 18, 0, tzinfo=timezone.utc)         # 12:00 Chicago


class Pinned(FakeEmbedder):
    """The fake embedder with the pinned full digest; records every text it is asked to embed."""

    def __init__(self, **kw):
        super().__init__(digest=PIN_DIGEST_FULL, **kw)
        self.seen: list[str] = []

    def _embed(self, texts):
        out = super()._embed(texts)
        self.seen.extend(texts)
        return out


@pytest.fixture(autouse=True)
def no_real_deadline(monkeypatch):
    # The indexer's 06:00 guard reads the real clock; these tests move a fake one, so the deadline is not set here.
    monkeypatch.setattr(A, "window_deadline", lambda now: None)


def seed_slice(w, emb, *, at=NIGHT, slice_s=600):
    return A.run_seed(vault_root=str(w.vault), index_runner=A.HostRunner(emb), owner_uid=os.geteuid(), reader_uid=os.geteuid(),
                      reader_gid=os.getegid(), slice_s=slice_s, require_root=False, now=lambda: at)


def meta(path: Path) -> dict:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return dict(con.execute("SELECT key, value FROM meta").fetchall())
    finally:
        con.close()


def serving_index(w) -> Path:
    return w.vault / "current" / "index" / "memory.sqlite"


def seed_dir(w) -> Path:
    return w.vault.parent / "vault-work" / A.SEED_DIR


def meaning_ids(w, query, emb) -> list:
    r = search.search(query, shelf_root=w.vault / "current" / "shelf", embedder=emb, index_path=serving_index(w), caller=RAW, k=5)
    assert r["status"]["meaning"] == "ok", r["status"]
    return [h["record_id"] for h in r["meaning"]]


# ── without a meaning runner nothing changes ──
def test_without_a_meaning_embedder_apply_is_keyword_only_as_before(w):
    changes_on_mac(w)
    send(w, [w.ids["d"]])
    out = apply(w)
    assert out["built"] and out["index_kind"] == "keyword" and not out["seed_promoted"]
    assert A.index_kind(serving_index(w)) == ("keyword", None)


def test_a_meaning_runner_before_the_seed_still_builds_keyword_and_embeds_nothing(w):
    emb = Pinned()
    changes_on_mac(w)
    bid = send(w, [w.ids["d"]])
    out = apply(w, index_runner=A.HostRunner(emb))
    assert out["built"] and out["index_kind"] == "keyword" and statuses(ack(w, bid)) == {w.ids["d"]: "stored"}
    assert emb.seen == []


# ── the seed ──
def test_the_seed_runs_only_in_the_batch_window(w):
    emb = Pinned()
    assert seed_slice(w, emb, at=DAY) == {"code": "outside_window"}
    assert not seed_dir(w).exists() and emb.seen == []


def test_the_seed_needs_a_meaning_runner(w):
    with pytest.raises(B.BackupError) as e:
        A.run_seed(vault_root=str(w.vault), index_runner=A.HostRunner(), require_root=False, now=lambda: NIGHT)
    assert e.value.code == "meaning_embedder_required"


def test_seed_slices_resume_and_then_apply_promotes_in_one_switch(w, monkeypatch):
    emb = Pinned()
    serving_before = current(w)
    ticks = iter(range(0, 100_000, 400))                                  # each monotonic() call: 400 s, so a 600 s slice is one record
    real = A.time.monotonic
    from core.memory_index import indexer
    monkeypatch.setattr(indexer.time, "monotonic", lambda: next(ticks))
    first = seed_slice(w, emb)
    assert first["code"] == "budget_spent" and not first["complete"]
    doc = json.loads((seed_dir(w) / A.SEED_DOC).read_text())
    assert doc["release"] == serving_before and doc["complete"] is False
    assert current(w) == serving_before                                   # the seed never touches a release
    assert apply(w, index_runner=A.HostRunner(Pinned()))["index_kind"] == "keyword"     # not complete: nothing promoted
    monkeypatch.setattr(indexer.time, "monotonic", real)
    again = seed_slice(w, emb)
    assert again["code"] == "ok" and again["complete"] and again["unchanged"] >= 1        # the first slice's record is not redone
    # Apply promotes: a new release whose index is the seed, brought up to date, verified, switched once, then acked.
    changes_on_mac(w)
    bid = send(w, [w.ids["a"], w.ids["d"]])
    emb2 = Pinned()
    out = apply(w, index_runner=A.HostRunner(emb2))
    assert out["built"] and out["seed_promoted"] and out["index_kind"] == "meaning" and out["seed_removed"]
    assert statuses(ack(w, bid)) == {w.ids["a"]: "stored", w.ids["d"]: "stored"}
    assert A.index_kind(serving_index(w)) == ("meaning", PIN_DIGEST_FULL)
    mark = json.loads((w.vault / "releases" / out["release"] / "RELEASE.json").read_text())
    assert mark["index"] == {"kind": "meaning", "embedder": "nomic-embed-text:latest@0a109f422b47"}
    assert not seed_dir(w).exists()
    assert all("zeppelin" in t or "xylophone" in t or "delta" in t or "alpha" in t for t in emb2.seen)   # only what changed since the seed
    # Meaning search now works on the serving release: a paraphrase with no shared word.
    assert w.ids["d"] in meaning_ids(w, "delta answer zeppelin", Pinned())


def test_after_promotion_each_apply_embeds_only_new_text(w):
    seed_slice(w, Pinned())
    apply(w, index_runner=A.HostRunner(Pinned()))                         # promotion, no batch needed
    assert A.index_kind(serving_index(w))[0] == "meaning"
    w.ids["e"] = add_record(w.mac, "grok", "g-e", [("human", "epsilon question"), ("assistant", "epsilon answer quokka")])
    bid = send(w, [w.ids["e"]])
    emb = Pinned()
    out = apply(w, index_runner=A.HostRunner(emb))
    assert out["built"] and out["index_kind"] == "meaning" and not out["seed_promoted"]
    assert statuses(ack(w, bid)) == {w.ids["e"]: "stored"}
    assert emb.seen and all("epsilon" in t for t in emb.seen)              # nothing already embedded is embedded again


def test_an_unreachable_embedder_fails_the_run_with_no_switch_and_no_ack(w):
    seed_slice(w, Pinned())
    apply(w, index_runner=A.HostRunner(Pinned()))
    serving = current(w)
    w.ids["e"] = add_record(w.mac, "grok", "g-e", [("human", "epsilon question"), ("assistant", "epsilon answer quokka")])
    bid = send(w, [w.ids["e"]])
    with pytest.raises(B.BackupError) as e:
        apply(w, index_runner=A.HostRunner(Pinned(down=True)))
    assert e.value.code == "index_failed:embedder_unavailable"
    assert current(w) == serving and not (w.destdir / "sync" / "acks" / f"{bid}.json").exists()
    out = apply(w, index_runner=A.HostRunner(Pinned()))                   # the embedder is back: the same batch applies
    assert out["built"] and statuses(ack(w, bid)) == {w.ids["e"]: "stored"}


def test_a_serving_meaning_index_with_another_digest_is_refused(w):
    other = FakeEmbedder(digest="0a109f422b47" + "e" * 52)
    seed_slice(w, Pinned())
    apply(w, index_runner=A.HostRunner(Pinned()))
    w.ids["e"] = add_record(w.mac, "grok", "g-e", [("human", "epsilon question"), ("assistant", "epsilon answer")])
    send(w, [w.ids["e"]])
    # Forge the serving index's identity (the release files are the owner's): apply must refuse, not mix two models' vectors.
    path = serving_index(w).resolve()
    os.chmod(path, 0o640)
    con = sqlite3.connect(path)
    con.execute("UPDATE meta SET value=? WHERE key='embedder_digest'", (other.digest,))
    con.commit()
    con.close()
    with pytest.raises(B.BackupError) as e:
        apply(w, index_runner=A.HostRunner(Pinned()))
    assert e.value.code == "index_identity_mismatch"


def test_a_crash_after_the_switch_before_the_seed_is_removed_heals_next_run(w):
    seed_slice(w, Pinned())

    def crash(removed):
        raise RuntimeError("killed after the switch")
    w.ids["e"] = add_record(w.mac, "grok", "g-e", [("human", "epsilon question"), ("assistant", "epsilon answer")])
    send(w, [w.ids["e"]])
    with pytest.raises(RuntimeError):
        A.run_apply(dest=w.dest, vault_root=str(w.vault), identity_spec=w.identity, index_runner=A.HostRunner(Pinned()), age_bin=w.age,
                    owner_uid=os.geteuid(), reader_uid=os.geteuid(), reader_gid=os.getegid(), require_root=False, now=w.clock,
                    hooks={"before_inventory": lambda: (_ for _ in ()).throw(RuntimeError("killed"))})
    assert A.index_kind(serving_index(w))[0] == "meaning" and seed_dir(w).exists()      # switched; the seed was not removed yet
    out = apply(w, index_runner=A.HostRunner(Pinned()))
    assert out["seed_removed"] and not seed_dir(w).exists() and out["index_kind"] == "meaning"
    assert seed_slice(w, Pinned())["code"] == "seed_not_needed"


def test_verify_refuses_a_meaning_index_with_a_missing_vector_row(w, monkeypatch):
    seed_slice(w, Pinned())
    real = A.index_step

    def lose_a_vector(shelf, index, only, **kw):
        out = real(shelf, index, only, **kw)
        from core.memory_index import store
        con = store.connect(index)
        con.execute("DELETE FROM vec WHERE id=(SELECT MIN(id) FROM chunks)")
        con.commit()
        con.close()
        return out
    monkeypatch.setattr(A, "index_step", lose_a_vector)
    with pytest.raises(B.BackupError) as e:
        apply(w, index_runner=A.HostRunner(Pinned()))
    assert e.value.code == "index_vector_rows_mismatch"
    assert A.index_kind(serving_index(w))[0] == "keyword" and seed_dir(w).exists()        # nothing switched; the seed stays


def test_plan_shows_the_seed(w, capsys):
    seed_slice(w, Pinned())
    plan = A.plan_apply(vault_root=str(w.vault))
    assert plan["index_kind"] == "keyword" and plan["meaning_seed"]["complete"] is True


# ── the docker runner ──
def test_the_docker_runner_joins_the_embed_network_only_for_meaning_steps(tmp_path):
    build = tmp_path / "scratch" / "build"
    build.mkdir(parents=True)
    seen = []

    def run(argv, **kw):
        seen.append((argv, kw))
        return subprocess.CompletedProcess(argv, 0, stdout='{"protocol": 1, "code": "ok"}\n', stderr="")
    image = "sha256:" + "a" * 64
    with pytest.raises(B.BackupError):
        A.make_index_runner(f"docker:{image}", embedder_url="http://vault-embedder:11434")           # a URL needs its network
    for bad_url in ("https://vault-embedder:11434", "http://evil.example.com:80/x", "http://vault-embedder:11434/api"):
        with pytest.raises(B.BackupError):
            A.make_index_runner(f"docker:{image}", embedder_url=bad_url, network="minimoi-vault-embed")
    runner = A.make_index_runner(f"docker:{image}", run=run, embedder_url="http://vault-embedder:11434", network="minimoi-vault-embed")
    assert runner.meaning
    runner.index(build, ["01M55RJ7RTGFC980AN2Y4XMZBC"])                                            # a keyword step: as before
    kw_argv = seen[-1][0]
    assert kw_argv[kw_argv.index("--network") + 1] == "none" and "--embedder-url" not in kw_argv
    runner.index(build, ["01M55RJ7RTGFC980AN2Y4XMZBC"], meaning=True)
    m = seen[-1][0]
    assert m[m.index("--network") + 1] == "minimoi-vault-embed" and m[-2:] == ["--embedder-url", "http://vault-embedder:11434"]
    for hard in ("--read-only", "--cap-drop", "ALL", "no-new-privileges", f"{build / 'shelf'}:/shelf:ro"):
        assert hard in m
    runner.seed(tmp_path / "release" / "shelf", tmp_path / "work" / "meaning-seed" / "memory.sqlite", 420, 1764158400.0)
    s, kw = seen[-1]
    assert s[s.index("--network") + 1] == "minimoi-vault-embed" and f"{tmp_path / 'release' / 'shelf'}:/shelf:ro" in s
    assert f"{tmp_path / 'work' / 'meaning-seed'}:/index" in s and "--update-all" in s and s[s.index("--budget-s") + 1] == "420"
    assert "MOI_BATCH_DEADLINE_EPOCH=1764158400.0" in s and kw["timeout"] == 420 + A.SEED_TIMEOUT_GRACE_S
    assert "--read-only" in s and "ALL" in s


def test_a_keyword_only_runner_refuses_a_meaning_step(tmp_path):
    runner = A.make_index_runner("docker:sha256:" + "a" * 64, run=lambda *a, **k: None)
    assert not runner.meaning
    with pytest.raises(B.BackupError):
        runner.index(tmp_path, [], meaning=True)


def test_the_cli_seed_refuses_without_the_embedder_and_is_quiet_outside_the_window(w, tmp_path, capsys, monkeypatch):
    va = load("vault_apply")
    assert va.main(cli(w, tmp_path / "jobs") + ["--index-runner", "host", "seed", "--execute"]) == 2
    assert "meaning_embedder_required" in capsys.readouterr().err
    monkeypatch.setattr(va.apply, "in_window", lambda now: False)
    rc = va.main(cli(w, tmp_path / "jobs") + ["--index-runner", "host", "--meaning-embedder", "http://127.0.0.1:11434", "seed", "--execute"])
    assert rc == 0 and "outside_window" in capsys.readouterr().out and not (tmp_path / "jobs" / "vault-meaning-seed.json").exists()


# ── the owner's daytime seed (28 Nov: "Deploy, seed now") ──
def test_without_the_owner_flag_a_daytime_seed_is_still_a_no_op(w):
    emb = Pinned()
    out = A.run_seed(vault_root=str(w.vault), index_runner=A.HostRunner(emb), owner_uid=os.geteuid(), reader_uid=os.geteuid(),
                     reader_gid=os.getegid(), slice_s=600, require_root=False, now=lambda: DAY, allow_outside_window=False)
    assert out == {"code": "outside_window"} and not seed_dir(w).exists() and emb.seen == []


def test_with_the_owner_flag_a_daytime_slice_runs_without_the_0600_guard(w, monkeypatch):
    monkeypatch.undo()                                      # the real window_deadline: by day it must not be passed to the slice
    emb = Pinned()
    deadlines = []
    real_seed = A.HostRunner.seed

    def seed(self, shelf, seed_index, budget_s, deadline, donor=False):
        deadlines.append(deadline)
        return real_seed(self, shelf, seed_index, budget_s, deadline, donor)
    monkeypatch.setattr(A.HostRunner, "seed", seed)
    out = A.run_seed(vault_root=str(w.vault), index_runner=A.HostRunner(emb), owner_uid=os.geteuid(), reader_uid=os.geteuid(),
                     reader_gid=os.getegid(), slice_s=600, require_root=False, now=lambda: DAY, allow_outside_window=True)
    assert out["code"] == "ok" and out["complete"] and out["by_day"] and deadlines == [None]
    assert A.seed_complete(seed_dir(w)) and emb.seen


def test_the_cli_seed_runs_by_day_only_with_the_owner_flag(w, tmp_path, capsys, monkeypatch):
    va = load("vault_apply")
    monkeypatch.setattr(va.apply, "in_window", lambda now: False)
    emb = Pinned()
    monkeypatch.setattr(va.apply, "make_index_runner", lambda *a, **k: A.HostRunner(emb))
    base = cli(w, tmp_path / "jobs") + ["--index-runner", "host", "--meaning-embedder", "http://127.0.0.1:11434"]
    assert va.main(base + ["seed", "--execute"]) == 0
    assert "outside_window" in capsys.readouterr().out and not (tmp_path / "jobs" / "vault-meaning-seed.json").exists()
    assert va.main(base + ["seed", "--execute", "--allow-outside-window"]) == 0
    assert "seed complete" in capsys.readouterr().out and A.seed_complete(seed_dir(w))


def test_a_seed_completed_by_day_is_promoted_by_the_next_daytime_apply(w):
    """Apply has no window: the 15-minute apply after a daytime ``complete: true`` promotes it, in daytime."""
    emb = Pinned()
    assert A.run_seed(vault_root=str(w.vault), index_runner=A.HostRunner(emb), owner_uid=os.geteuid(), reader_uid=os.geteuid(),
                      reader_gid=os.getegid(), slice_s=600, require_root=False, now=lambda: DAY, allow_outside_window=True)["complete"]
    out = apply(w, index_runner=A.HostRunner(emb), now=Clock(DAY))
    assert out["built"] and out["seed_promoted"] and out["index_kind"] == "meaning"
    assert A.index_kind(serving_index(w))[0] == "meaning"

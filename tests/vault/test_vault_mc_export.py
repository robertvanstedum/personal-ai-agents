"""``vault-mc-export``, the EC2 side of the production MC capture path (17 Oct 2026).

Fully synthetic and local. Production is test_mc_capture's World (the real conversation store on a tmp folder, a SQLite floor from the
portal's own migration); the exporter runs IN-PROCESS through the injected runner, in ``--stdout-tar`` mode, writing to the file the job
hands it. The destination is file://, encryption the pinned real ``age`` when installed (else the transport tests' stand-in). The Mac pulls
either with the REAL ``core.memory_sync.mc_pull.run_pull`` (``real_pull``: identical bytes are ``unchanged``, never rewritten, so the
watcher skips them) or, where a test needs a forced verdict, with a hand-written stand-in on the shared contract (``mc_batch``) that always
rewrites. The gap-repair and reconciliation tests use the real one (review MEDIUM-4: the stand-in's rewrite hid a re-queue loop). Its
capture is the real ``minimoi-mc`` watcher on a synthetic shelf, and "publishing" copies that shelf into a new serving release
(``<vault>/current -> releases/<id>``). No network, no AWS, no docker, runs as the current user."""
from __future__ import annotations

import gzip
import io
import json
import os
import shutil
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.memory_backup import backup as B
from core.memory_backup import production as P
from core.memory_backup.backup import BackupError
from core.memory_shelf import approvals, record, watchers
from core.memory_shelf.config import SourceCfg
from core.memory_sync import mc_batch as MB
from core.memory_sync import mc_export as MX
from core.memory_sync import mc_pull as MP
from tests.memory_backup.test_production_transport import make_age
from tests.memory_shelf.helpers import make_shelf
from tests.memory_shelf.test_mc_capture import OTHER, OWNER, PRINCIPAL, World, X

REPO = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
H = timedelta(hours=1)
SECRETS = ("KILN-SECRET", "GLAZE-SECRET", "THREAD-SECRET", "VISITOR-SECRET", "TITLE-SECRET")


class Mac:
    """The Mac side, hand-written on the contract: pull -> verify -> place -> ack; then the real watcher captures."""

    def __init__(self, w):
        self.w = w
        self.watch = w.tmp / "mac" / "watch"
        self.watch.mkdir(parents=True, mode=0o700)
        self.shelf = make_shelf(w.tmp / "mac")
        self.cfg = SourceCfg("minimoi-mc", "mc", self.watch, ())
        self.pulled: list = []
        self.identity = B.fetch_identity(w.identity)
        self.approved = False

    def pull(self, status_for=None) -> list:
        d = B.Dest(self.w.dest)
        new = [b for b in MB.complete_batches(d) if b not in self.pulled]
        for bid in new:
            work = self.w.tmp / "macwork" / bid
            work.mkdir(parents=True)
            m = MB.fetch_chain(d, bid, work, "production")
            MB.fetch_ciphertext(d, bid, m, work / "c.age")
            B.decrypt(self.w.age, self.identity, work / "c.age", work / "p.tar.gz", work)
            MB.extract(work / "p.tar.gz", work / "x", m)
            files = []
            for name, sha, size in m["files"]:
                status = status_for(name) if status_for else None
                if status is None:
                    code = MB.check_file(work / "x", name, sha, size)
                    if code:
                        status = f"rejected:{code}"
                    else:
                        shutil.copyfile(work / "x" / name, self.watch / name)
                        status = MB.PLACED
                files.append([name, sha, status])
            ack = work / "ack.json"
            ack.write_text(json.dumps(MB.ack_doc(bid, files, self.w.t)))
            d.put(ack, f"{MB.ACK_PREFIX}/{bid}.json", replace=True)
            self.pulled.append(bid)
            shutil.rmtree(work)
        return new

    def real_pull(self) -> dict:
        """The real ``vault-mc-pull`` into the same watch folder. Its shelf config has no ``minimoi-mc`` source (capture
        ``not_configured``): the files wait in the folder for ``capture`` below, the stand-in shelf this harness publishes."""
        config = self.w.tmp / "mac" / "pull-config.json"
        if not config.exists():
            config.write_text(json.dumps({"schema_version": 1, "shelf_root": str(self.w.tmp / "mac" / "unused-shelf"),
                                          "inbox_root": str(self.w.tmp / "mac" / "inbox"), "headroom": {"min_free_bytes": 0},
                                          "never_copy": [], "sources": {}}))
        out = MP.run_pull(dest=self.w.dest, identity_spec=self.w.identity, age_bin=self.w.age, expect_age=None,
                          jobs_root=self.w.tmp / "mac" / "jobs", watch=self.watch, state_dir=self.w.tmp / "mac" / ".vault-mc-pull",
                          config_path=str(config), now=lambda: self.w.t, alert_send=lambda text: "not_sent")
        assert out["results"]["pull"] == "ok", out
        return out["results"]

    def capture(self) -> dict:
        if not self.approved:
            watchers.run_source(self.shelf, self.cfg, now=self.w.t)
            approvals.approve_source(self.shelf, self.cfg.name, self.cfg.fingerprint(), OWNER)
            self.approved = True
        return watchers.run_source(self.shelf, self.cfg, now=self.w.t)


class W:
    pass


@pytest.fixture
def w(tmp_path, monkeypatch):
    monkeypatch.setenv("MOI_RESERVE_GIB", "0")
    monkeypatch.delenv("MOI_BATCH_DEADLINE_EPOCH", raising=False)
    w = W()
    w.tmp, w.t = tmp_path, T0
    (tmp_path / "prod").mkdir()
    w.world = World(tmp_path / "prod")
    monkeypatch.setattr(X, "database_floor", lambda: w.world.floor)
    w.age, w.recipient, w.identity = make_age(tmp_path)
    w.dest, w.destdir = f"file://{tmp_path / 'dest'}", tmp_path / "dest"
    (tmp_path / "ec2").mkdir(mode=0o755)
    w.vault = tmp_path / "ec2" / "vault"
    w.vault.mkdir()
    w.sync, w.work = tmp_path / "ec2" / "vault-sync", tmp_path / "ec2" / "vault-work"
    w.argvs = []
    w.convs = {"kiln": w.world.conversation(PRINCIPAL, ("owner", "KILN-SECRET schedule?"), ("mc", "KILN-SECRET fire Tuesday")),
               "glaze": w.world.conversation(PRINCIPAL, ("owner", "GLAZE-SECRET which?"), ("mc", "GLAZE-SECRET the blue one"))}
    w.world.store.rename(w.convs["kiln"]["id"], PRINCIPAL, "TITLE-SECRET kiln plan")
    w.world.conversation(OTHER, ("owner", "VISITOR-SECRET words"), ("mc", "VISITOR-SECRET reply"))
    thread = w.world.store.get("shop-floor-thread", PRINCIPAL)
    w.world.say(thread, ("owner", "THREAD-SECRET words"), ("mc", "THREAD-SECRET reply"))
    w.mac = Mac(w)
    return w


def runner(w):
    """The production runner's shape, in-process: the exporter's own main in --stdout-tar mode, writing the tar to the job's file."""
    def run(argv, stdout):
        w.argvs.append(list(argv))
        tail = argv[argv.index(MX.EXPORTER) + 1:]
        return X.main(tail + ["--guild-dir", str(w.world.guild)], stream=stdout)
    return run


def export(w, **kw):
    args = dict(dest=w.dest, vault_root=str(w.vault), recipient=w.recipient, principal=PRINCIPAL, runner=runner(w), age_bin=w.age,
                owner_uid=os.geteuid(), require_root=False, now=lambda: w.t)
    args.update(kw)
    return MX.run_export(**args)


def state(w) -> dict:
    return json.loads((w.sync / MX.STATE_NAME).read_text())


def name_of(w, key) -> str:
    return f"production--{w.convs[key]['id']}.json"


def batch_names(w, bid) -> list:
    return [f[0] for f in json.loads((w.destdir / MB.EXPORT_PREFIX / bid / B.MANIFEST).read_text())["files"]]


def publish(w):
    """The Mac's shelf becomes the serving release (what vault-sync + vault-apply achieve), switched atomically like r3."""
    rid = B.new_attempt_id(w.t)
    (w.vault / "releases" / rid).mkdir(parents=True)
    shutil.copytree(w.mac.shelf.root, w.vault / "releases" / rid / "shelf")
    tmp = w.vault / ".current-new"
    os.symlink(f"releases/{rid}", tmp)
    os.replace(tmp, w.vault / "current")
    return rid


def cycle(w):
    """One full trip: export, pull and place (the real mc_pull), ack read, capture, publish, reconcile."""
    first = export(w)
    w.mac.real_pull()
    w.t += H / 2
    w.mac.capture()
    publish(w)
    return first, export(w)


def scratch_left(w) -> list:
    return [p.name for p in w.work.iterdir()] if w.work.exists() else []


# ── the trip ─────────────────────────────────────────────────────────────────

def test_the_trip_production_to_mac_to_vault_and_reconciled(w):
    first = export(w)
    assert first["export"] == "ok" and first["conversations"] == 3 and first["sent"] == 3 and first["changed"] == 3
    assert first["other_principal"] == 2 and first["missing_in_vault"] == 3
    assert w.argvs[0] == ["docker", "exec", "-i", "minimoi-portal", "python3", "-I", "-B", MX.EXPORTER, "--instance", "production",
                          "--principal", PRINCIPAL, "--stdout-tar"]
    (bid,) = w.mac.pull()
    assert sorted(batch_names(w, bid)) == sorted(state(w)["pending"][bid]["files"])
    assert sorted(p.name for p in w.mac.watch.iterdir()) == sorted(state(w)["pending"][bid]["files"])
    w.t += H / 2
    assert w.mac.capture()["counts"] == {"captured": 3}
    keys = set(w.mac.shelf.index())
    assert {f"minimoi-mc:production:{w.convs['kiln']['id']}", f"minimoi-mc:production:{w.convs['glaze']['id']}"} < keys
    # source_hash is the sha256 of the export file the Mac captured: what reconciliation compares.
    main = w.mac.shelf.main_path(w.mac.shelf.index()[f"minimoi-mc:production:{w.convs['kiln']['id']}"])
    meta, _ = record.load(record.read(main))
    assert meta["source_hash"] == B.sha256_file(w.mac.watch / name_of(w, "kiln"))
    second = export(w)
    assert second["acked"] == 3 and second["sent"] == 0 and second["pending_files"] == 0 and second["missing_in_vault"] == 3
    publish(w)
    third = export(w)
    assert third["in_vault_current"] == 3 and third["missing_in_vault"] == 0 and third["lag_oldest_s"] == 0 and third["sent"] == 0
    assert scratch_left(w) == []


def test_an_unchanged_second_run_sends_nothing(w):
    export(w)
    w.t += H / 4                                                  # inside the backoff, not acked yet: the pending batch already carries them
    again = export(w)
    assert again["sent"] == 0 and again["changed"] == 0 and len(state(w)["pending"]) == 1
    w.mac.pull()
    w.t += 3 * H
    acked = export(w)
    assert acked["acked"] == 3 and acked["sent"] == 0
    w.mac.capture()
    publish(w)
    w.t += 30 * H                                                 # long past every backoff and the grace: current in the Vault, so nothing
    assert export(w)["sent"] == 0
    assert len(MB.complete_batches(B.Dest(w.dest))) == 1


def test_a_changed_conversation_is_sent(w):
    cycle(w)
    w.world.say(w.convs["glaze"], ("owner", "GLAZE-SECRET and the rim?"))
    w.t += H
    out = export(w)
    assert out["changed"] == 1 and out["sent"] == 1 and out["in_vault_stale"] == 1
    assert batch_names(w, out["batch"]) == [name_of(w, "glaze")]


def test_an_interrupted_upload_is_re_sent_in_a_new_batch(w):
    def put(local, key, *, replace=False):
        if key.endswith(B.COMPLETE):
            raise BackupError("upload_failed")
        B.Dest(w.dest).put(local, key, replace=replace)
    with pytest.raises(BackupError) as exc:
        export(w, put=put)
    assert exc.value.code == "upload_failed"
    (orphan,) = state(w)["pending"]
    assert state(w)["pending"][orphan]["state"] == "sending"
    assert MB.complete_batches(B.Dest(w.dest)) == [] and (w.destdir / MB.EXPORT_PREFIX / orphan / MB.BATCH_OBJECT).exists()
    assert scratch_left(w) == []
    out = export(w)                                               # no time passes: "sending" is due at once
    assert out["sent"] == 3 and out["batch"] != orphan and out["batch"] > orphan
    assert list(state(w)["pending"]) == [out["batch"]] and state(w)["pending"][out["batch"]]["state"] == "sent"
    assert w.mac.pull() == [out["batch"]]                          # the orphan has no COMPLETE: never listed


def test_a_rejected_ack_is_retried_after_backoff_and_a_placed_ack_clears(w):
    first = export(w)
    w.mac.pull(status_for=lambda name: "rejected:file_hash_mismatch")
    w.t += H / 2
    waiting = export(w)
    assert waiting["rejected"] == 3 and waiting["sent"] == 0 and waiting["pending_files"] == 3     # before the 1 h backoff
    w.t += H
    retried = export(w)
    assert retried["sent"] == 3 and retried["batch"] != first["batch"] and list(state(w)["pending"]) == [retried["batch"]]
    pend = state(w)["pending"][retried["batch"]]
    assert pend["attempts"] == 2 and pend["next_retry_utc"] == (w.t + 2 * H).strftime("%Y-%m-%dT%H:%M:%SZ")
    w.mac.pull()
    w.t += H / 2
    cleared = export(w)
    assert cleared["acked"] == 3 and cleared["rejected"] == 0 and cleared["pending_files"] == 0 and state(w)["pending"] == {}
    assert set(state(w)["acked"]) == set(pend["files"])


def test_the_backoff_doubles_to_a_twelve_hour_cap():
    assert [MX.backoff(n) for n in range(1, 8)] == [H, 2 * H, 4 * H, 8 * H, 12 * H, 12 * H, 12 * H]


def test_a_re_send_of_bytes_already_acked_is_not_sent(w):
    cycle(w)
    st = state(w)
    name = name_of(w, "kiln")
    # An older batch still listing bytes the Mac has since placed (via a later batch), and due for its retry.
    old = B.new_attempt_id(T0 - 5 * H)
    st["pending"][old] = {"files": {name: st["acked"][name]["sha256"]}, "sent_utc": "2026-10-17T07:00:00Z", "attempts": 1,
                          "next_retry_utc": "2026-10-17T08:00:00Z", "state": "sending"}
    (w.sync / MX.STATE_NAME).write_text(json.dumps(st))
    w.t += H
    out = export(w)
    assert out["sent"] == 0 and state(w)["pending"] == {}


def test_gap_repair_requeues_an_acked_conversation_missing_from_the_vault_past_the_grace(w):
    export(w)
    w.mac.real_pull()
    w.mac.capture()
    acked_at = w.t + H / 2
    w.t = acked_at
    export(w)                                                     # reads the acks
    rid = publish(w)
    lost = name_of(w, "glaze")
    index_path = w.vault / "releases" / rid / "shelf" / "_ledger" / "source_index.json"
    index = json.loads(index_path.read_text())
    del index[MX.source_key(lost)]                                # the serving Vault lost that record
    index_path.write_text(json.dumps(index))
    w.t = acked_at + 11 * H
    early = export(w)
    assert early["gap_requeued"] == 0 and early["missing_in_vault"] == 1 and early["sent"] == 0
    w.t = acked_at + 13 * H
    late = export(w)
    assert late["gap_requeued"] == 1 and late["sent"] == 1 and batch_names(w, late["batch"]) == [lost]
    assert lost not in state(w)["acked"] and late["in_vault_current"] == 2
    w.t += H / 4
    assert export(w)["gap_requeued"] == 0                          # requeued once, now pending, not again
    # The real Mac: the same bytes are already in its watch folder, so they are "unchanged" (not rewritten, the watcher skips them)
    # and acknowledged placed again. Identical bytes cannot repair this record: never re-sent again, however many graces pass.
    pulled = w.mac.real_pull()
    assert (pulled["unchanged"], pulled["placed"]) == ("1", "0")
    w.t += H / 4
    assert export(w)["acked"] == 1 and lost in state(w)["acked"]
    for _ in range(3):
        w.t += 13 * H
        out = export(w)
        assert (out["gap_requeued"], out["sent"], out["behind_after_repair"], out["missing_in_vault"]) == (0, 0, 1, 1)
        assert w.mac.real_pull()["batches"] == "0"
    assert len(MB.complete_batches(B.Dest(w.dest))) == 2           # the first batch and the one re-send, nothing more
    assert state(w)["requeued"] == {lost: state(w)["acked"][lost]["sha256"]}
    # New bytes are a new chance: the remembered re-queue is forgotten and the change is sent like any other.
    w.world.say(w.convs["glaze"], ("owner", "GLAZE-SECRET a new question"))
    w.t += H
    changed = export(w)
    assert changed["sent"] == 1 and changed["behind_after_repair"] == 0 and state(w)["requeued"] == {}


def test_reconciliation_counts_and_the_oldest_lag(w):
    cycle(w)                                                      # with the real mc_pull
    w.t = T0 + 2 * H
    w.world.say(w.convs["kiln"], ("owner", "KILN-SECRET one more"))
    assert export(w)["in_vault_stale"] == 1
    w.t = T0 + 5 * H
    fresh = w.world.conversation(PRINCIPAL, ("owner", "KILN-SECRET new topic"))
    out = export(w)
    assert (out["in_vault_current"], out["in_vault_stale"], out["missing_in_vault"]) == (2, 1, 1)
    assert out["lag_oldest_s"] == 3 * 3600                           # kiln's new bytes first seen at T0+2h
    assert out["behind_past_grace"] == 0
    assert state(w)["first_seen"][f"production--{fresh['id']}.json"]["utc"] == "2026-10-17T17:00:00Z"
    w.t = T0 + 15 * H
    assert export(w)["behind_past_grace"] == 1


def test_a_partial_export_continues_with_what_it_has(w, monkeypatch):
    w.world.conversation(PRINCIPAL, *[("owner", f"n{i}") for i in range(6)])
    monkeypatch.setattr(X, "MAX_NOTES", 4)
    out = export(w)
    assert out["export"] == "partial" and out["sent"] == 3


def test_a_batch_budget_defers_never_drops(w):
    sent = []
    for _ in range(4):
        out = export(w, max_batch_bytes=1)
        sent += batch_names(w, out["batch"]) if out["batch"] else []
        w.t += H / 4
    assert len(sent) == 3 and len(set(sent)) == 3


# ── failures ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("body, code", [("valid", 2), (b"", 2), (b"", 3), (b"this is not a tar at all" * 50, 0), ("truncated", 0)])
def test_an_exporter_failure_or_garbage_fails_the_run_with_no_batch(w, body, code):
    def bad(argv, stdout):
        if body in ("truncated", "valid"):                        # exit 2 even with a whole stream fails; a stream cut short too
            buf = io.BytesIO()
            X.main(argv[argv.index(MX.EXPORTER) + 1:] + ["--guild-dir", str(w.world.guild)], stream=buf)
            stdout.write(buf.getvalue()[:1536] if body == "truncated" else buf.getvalue())
        else:
            stdout.write(body)
        return code
    with pytest.raises(BackupError) as exc:
        export(w, runner=bad)
    assert exc.value.code == "export_failed"
    assert not (w.destdir / MB.EXPORT_PREFIX).exists() and scratch_left(w) == []


def _tar(*members) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data, kind in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            if kind == "link":
                info.type, info.linkname, info.size = tarfile.SYMTYPE, "/etc/passwd", 0
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_the_export_stream_is_read_strictly(tmp_path):
    good = json.dumps({"schema": "minimoi.mc-conversation/1", "instance": "production", "conversation_id": "c-0123456789ab"}).encode()
    end = json.dumps({"status": "ok", "counts": {"exported": 1}}).encode()
    ok = ("production--c-0123456789ab.json", good, "file")
    cases = {"traversal": [("../production--c-0123456789ab.json", good, "file"), ("EXPORT.json", end, "file")],
             "other_instance": [("dev--c-0123456789ab.json", good, "file"), ("EXPORT.json", end, "file")],
             "duplicate": [ok, ok, ("EXPORT.json", end, "file")],
             "after_end": [("EXPORT.json", end, "file"), ok],
             "no_end": [ok],
             "link": [("production--c-0123456789ab.json", b"", "link"), ("EXPORT.json", end, "file")],
             "not_its_own_export": [("production--c-ba9876543210.json", good, "file"), ("EXPORT.json", end, "file")],
             "bad_doc": [ok, ("EXPORT.json", b'{"status": "fine"}', "file")]}
    for i, (label, members) in enumerate(cases.items()):
        archive = tmp_path / f"{i}.tar"
        archive.write_bytes(_tar(*members))
        with pytest.raises(BackupError) as exc:
            MX.read_export(archive, tmp_path / f"x{i}")
        assert exc.value.code == "export_failed", label
    archive = tmp_path / "good.tar"
    archive.write_bytes(_tar(ok, ("EXPORT.json", end, "file")))
    files, doc = MX.read_export(archive, tmp_path / "xg")
    assert list(files) == [ok[0]] and doc["counts"] == {"exported": 1}


def test_plaintext_is_wiped_on_failure_and_leftovers_at_the_next_start(w):
    w.work.mkdir(mode=0o700)
    leftover = w.work / ".mcx-killedrun"
    leftover.mkdir(mode=0o700)
    (leftover / "export.tar").write_bytes(b"KILN-SECRET plaintext of a killed run")
    seen = {}

    def boom(path):
        seen["files"] = sorted(p.name for p in path.iterdir())
        raise BackupError("injected_failure")
    with pytest.raises(BackupError):
        export(w, hooks={"extracted": boom})
    assert len(seen["files"]) == 3                                # the plaintext existed at the failure point
    assert scratch_left(w) == []                                  # and is gone, with the killed run's leftovers


def test_a_busy_lock_refuses_quietly_and_writes_nothing(w, monkeypatch, capsys):
    w.sync.mkdir(mode=0o700)
    with P._Lock(w.sync / MX.LOCK_NAME):
        with pytest.raises(BackupError) as exc:
            export(w)
        assert exc.value.code == "mc_export_busy"
        vme = load_script()
        monkeypatch.setattr(vme.alert, "send", lambda text: (_ for _ in ()).throw(AssertionError("no alert for busy")))
        capsys.readouterr()
        assert vme.main(cli(w), runner=runner(w), now=lambda: w.t) == 2
        assert capsys.readouterr().err.strip() == "refused: mc_export_busy"
    assert not (w.tmp / "jobs" / "vault-mc-export.json").exists()          # refused at the lock: not even a status (Codex, 17 Oct)
    assert w.argvs == []


# ── the job script ───────────────────────────────────────────────────────────

def load_script():
    import importlib.util
    spec = importlib.util.spec_from_file_location("vault_mc_export", REPO / "scripts" / "vault" / "vault_mc_export.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def cli(w, *extra):
    return ["--dest", w.dest, "--vault-root", str(w.vault), "--recipient", w.recipient, "--principal", PRINCIPAL, "--age-bin", w.age,
            "--expect-age", "", "--jobs-root", str(w.tmp / "jobs"), "--registry", str(w.tmp / "no-registry.json"), "--_allow-non-root",
            *extra, "run", "--execute"]


def test_exit_2_from_the_exporter_fails_the_job_with_an_alert(w, monkeypatch, capsys):
    vme = load_script()
    sent = []
    monkeypatch.setattr(vme.alert, "send", lambda text: sent.append(text) or "not_sent")
    assert vme.main(cli(w), runner=lambda argv, stdout: 2, now=lambda: w.t) == 1
    st = json.loads((w.tmp / "jobs" / "vault-mc-export.json").read_text())
    assert st["state"] == "failed" and st["results"]["export"] == "export_failed" and len(sent) == 1 and "export_failed" in sent[0]
    capsys.readouterr()


def dest_texts(w) -> list:
    """Every object at the destination as text (manifests, COMPLETE, acks, the ciphertext), plus its gunzipped bytes when it is one: an
    unencrypted batch would be a readable archive."""
    out = []
    for p in (p for p in w.destdir.rglob("*") if p.is_file()):
        data = p.read_bytes()
        try:
            data += gzip.decompress(data)
        except (OSError, EOFError):
            pass
        out.append(data.decode("utf-8", "replace"))
    return out


def assert_no_content(texts, titles=()):
    for text in texts:
        for secret in SECRETS + (PRINCIPAL,) + tuple(t for t in titles if t):
            assert secret not in text


def test_the_status_and_nothing_plain_text_holds_a_title_or_text(w, monkeypatch, capsys):
    vme = load_script()
    alerts = []
    monkeypatch.setattr(vme.alert, "send", lambda text: alerts.append(text) or "not_sent")
    assert vme.main(cli(w), runner=runner(w), now=lambda: w.t) == 0
    st = json.loads((w.tmp / "jobs" / "vault-mc-export.json").read_text())
    assert st["state"] == "ok" and st["results"]["export"] == "ok" and st["results"]["sent"] == "3"
    assert set(st["results"]) == {"export", *vme.RESULT_KEYS}
    assert_no_content(dest_texts(w) + [(w.sync / MX.STATE_NAME).read_text()])                # before anything else reads it
    w.mac.pull()
    w.t += 13 * H                                                 # never captured or published: behind past the grace (first seen 13 h ago)
    assert vme.main(cli(w), runner=runner(w), now=lambda: w.t) == 0
    st = json.loads((w.tmp / "jobs" / "vault-mc-export.json").read_text())
    assert st["state"] == "warn" and st["results"]["missing_in_vault"] == "3" and st["results"]["acked"] == "3"
    assert st["results"]["lag_oldest_s"] == str(13 * 3600) and st["results"]["gap_requeued"] == "0"     # the grace runs from the ack
    vme.main(cli(w), runner=lambda argv, stdout: 2, now=lambda: w.t)        # a failure: the alert text too
    printed = capsys.readouterr()
    titles = [json.loads(p.read_text())["title"] for p in w.mac.watch.iterdir()]
    assert "TITLE-SECRET kiln plan" in titles                     # the title exists, inside the export files only
    plain = [printed.out, printed.err, *alerts, (w.sync / MX.STATE_NAME).read_text(),
             (w.tmp / "jobs" / "vault-mc-export.json").read_text()]
    assert_no_content(plain + dest_texts(w), titles)
    assert scratch_left(w) == []

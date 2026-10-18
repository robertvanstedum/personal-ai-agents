"""Production MC conversations reach the production Vault automatically: the whole trip, end to end, on fixtures (17 Oct 2026).

Every job is the real one; nothing is mocked between them. Production is test_mc_capture's World (the real conversation store on a tmp
folder, a SQLite floor from the portal's own migration). EC2's ``vault-mc-export`` runs the exporter in-process (``--stdout-tar``) through
the injected runner; the Mac's ``vault-mc-pull`` verifies, places and acknowledges, then the approval-gated ``minimoi-mc`` capture writes
records on the Mac shelf; ``vault-sync`` sends them; ``vault-apply`` installs them in production (seeded by r3's snapshot transport); and
``vault-mc-export`` reconciles the export against the serving Vault. ``file://`` stands in for S3; the clock is injected; age is the pinned
real binary with a throwaway identity when installed, else the transport tests' stand-in.

Scenarios: the trip (another principal's words never travel), a change adds an edition, an interrupted export upload, an interrupted pull
(crash before the ack), a retry that re-sends bytes the Mac already placed (no duplicate), and a gap (a placed file lost before capture)
that the export's reconciliation re-queues until the Vault holds it."""
from __future__ import annotations

import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.memory_backup import backup as B
from core.memory_backup import production as P
from core.memory_index import indexer
from core.memory_shelf import approvals, watchers
from core.memory_shelf import config as shelf_config
from core.memory_shelf.shelf import Shelf
from core.memory_sync import apply as A
from core.memory_sync import mc_export as MX
from core.memory_sync import mc_pull as MP
from core.memory_sync import sync as S
from core.memory_sync import versions as V
from tests.memory_backup.test_production_transport import make_age
from tests.memory_index.helpers import add_record
from tests.memory_shelf.test_mc_capture import OTHER, OWNER, PRINCIPAL, World, X

ROOT = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 10, 19, 19, 0, tzinfo=timezone.utc)          # 14:00 Chicago: daytime, the light bound applies
SECRETS = ("KILN-SECRET", "GLAZE-SECRET", "THREAD-SECRET")
VISITOR = "VISITOR-SECRET"


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


class Trip:
    def __init__(self, tmp: Path, monkeypatch):
        self.tmp, self.clock = tmp, Clock(T0)
        (tmp / "prod").mkdir()
        self.portal = World(tmp / "prod")
        monkeypatch.setattr(X, "database_floor", lambda: self.portal.floor)
        real = Path.home() / "minimoi-staging" / "tools" / "age-v1.3.2" / "age"
        if real.is_file() and real.with_name("age-keygen").is_file():
            spec = importlib.util.spec_from_file_location("sync_e2e_fixture", ROOT / "scripts/vault/sync_e2e_fixture.py")
            E = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(E)
            self.age = str(real)
            self.recipient, self.identity = E.make_identity(tmp, str(real.with_name("age-keygen")))
        else:
            self.age, self.recipient, self.identity = make_age(tmp)
        self.destdir = tmp / "s3"
        self.dest = f"file://{self.destdir}"
        # the Mac
        self.mac = tmp / "mac"
        self.shelf_root = self.mac / "data" / "memory"
        self.shelf = Shelf(self.shelf_root, min_free_bytes=0, clock=lambda: self.clock.t)
        self.shelf.init_layout()
        self.watch = self.mac / "mc-export"
        self.watch.mkdir(mode=0o700, parents=True)
        self.config = self.mac / "memory_shelf.json"
        self.config.write_text(json.dumps({"schema_version": 1, "shelf_root": str(self.shelf_root), "inbox_root": str(self.mac / "inbox"),
                                           "headroom": {"min_free_bytes": 0}, "never_copy": [],
                                           "sources": {"minimoi-mc": {"kind": "mc", "root": str(self.watch), "never_copy": []}}}))
        self.jobs = self.mac / "data" / "jobs"
        # EC2
        (tmp / "ec2").mkdir(mode=0o755)
        self.vault = tmp / "ec2" / "vault"
        self.convs = {}

    # ── production portal ──
    def converse(self, key, *turns, principal=PRINCIPAL):
        self.convs[key] = self.portal.conversation(principal, *turns)

    # ── the jobs ──
    def export(self, **kw):
        def runner(argv, stdout):
            tail = argv[argv.index(MX.EXPORTER) + 1:]
            return X.main(tail + ["--guild-dir", str(self.portal.guild)], stream=stdout)
        args = dict(dest=self.dest, vault_root=str(self.vault), recipient=self.recipient, principal=PRINCIPAL, runner=runner, age_bin=self.age,
                    owner_uid=os.geteuid(), require_root=False, now=self.clock)
        args.update(kw)
        return MX.run_export(**args)

    def pull(self, **kw):
        args = dict(dest=self.dest, identity_spec=self.identity, age_bin=self.age, expect_age=None, jobs_root=self.jobs, watch=self.watch,
                    state_dir=self.mac / ".vault-mc-pull", config_path=str(self.config), now=self.clock, alert_send=lambda t: "not_sent")
        args.update(kw)
        return MP.run_pull(**args)

    def sync(self):
        return S.run_sync(shelf_root=self.shelf_root, dest=self.dest, recipient=self.recipient, age_bin=self.age, expect_age=None,
                          jobs_root=self.jobs, state_dir=self.mac / ".vault-sync", now=self.clock, alert_send=lambda t: "not_sent")

    def apply(self):
        return A.run_apply(dest=self.dest, vault_root=str(self.vault), identity_spec=self.identity, index_runner="host", age_bin=self.age,
                           owner_uid=os.geteuid(), reader_uid=os.geteuid(), reader_gid=os.getegid(), require_root=False, now=self.clock)

    def approve(self):
        cfg = shelf_config.load(str(self.config)).sources["minimoi-mc"]
        if not approvals.dry_run_path(self.shelf, "minimoi-mc").is_file():
            watchers.run_source(self.shelf, cfg, now=self.clock.t)        # the first pass writes the owner's dry-run listing only
        approvals.approve_source(self.shelf, "minimoi-mc", cfg.fingerprint(), OWNER)

    def seed(self):
        """r3's snapshot transport seeds production from the Mac shelf; the first vault-apply writes production's inventory."""
        add_record(self.shelf, "grok", "g-seed", [("human", "seed question"), ("assistant", "seed answer")])
        index = self.mac / "data" / "memory-index" / "memory.sqlite"
        assert indexer.update(self.shelf_root, None, index_path=index, min_free_bytes=0, keyword_only=True)["code"] == "ok"
        P.run_publish(shelf_root=str(self.shelf_root), index_path=str(index), dest=self.dest, recipient=self.recipient,
                      workdir=str(self.mac / ".vault-publish-work"), age_bin=self.age, now=self.clock.t, enforce_window=False)
        P.run_refresh(dest=self.dest, vault_root=str(self.vault), identity_spec=self.identity, age_bin=self.age, owner_uid=os.geteuid(),
                      reader_uid=os.geteuid(), reader_gid=os.getegid(), require_root=False)
        assert self.apply()["inventory"] == "written"

    def carry(self):
        """The 2-hourly schedules, condensed: pull+capture, sync, apply, sync (clears the ack), then the export reconciles."""
        pulled = self.pull()
        self.clock.advance(minutes=5)
        self.sync()
        self.apply()
        self.clock.advance(minutes=5)
        synced = self.sync()
        self.clock.advance(minutes=5)
        return pulled, synced, self.export()

    # ── what each place holds ──
    def production_records(self) -> dict:
        shelf = (self.vault / "current" / "shelf").resolve()
        return {k: v for k, v in V.source_index(shelf).items() if k.startswith("minimoi-mc:production:")}

    def key(self, name) -> str:
        return f"minimoi-mc:production:{self.convs[name]['id']}"

    def everything_text(self) -> str:
        """Every file of the watch folder, both shelves and the destination's plain objects (ciphertext excluded)."""
        out = []
        for base in (self.watch, self.shelf_root, (self.vault / "current" / "shelf").resolve(), self.destdir):
            for p in base.rglob("*"):
                if p.is_file() and not p.name.endswith(".age"):
                    out.append(p.read_bytes().decode("utf-8", "replace"))
        return "\n".join(out)


@pytest.fixture
def t(tmp_path, monkeypatch):
    monkeypatch.setenv("MOI_RESERVE_GIB", "0")
    monkeypatch.delenv("MOI_BATCH_DEADLINE_EPOCH", raising=False)
    trip = Trip(tmp_path, monkeypatch)
    trip.converse("kiln", ("owner", "KILN-SECRET schedule?"), ("mc", "KILN-SECRET fire Tuesday"))
    trip.converse("glaze", ("owner", "GLAZE-SECRET which?"), ("mc", "GLAZE-SECRET the blue one"))
    trip.portal.conversation(OTHER, ("owner", f"{VISITOR} words"), ("mc", f"{VISITOR} reply"))
    trip.seed()
    return trip


def test_production_mc_conversations_reach_the_production_vault_automatically(t):
    first = t.export()
    assert first["export"] == "ok" and first["sent"] == 2 and first["missing_in_vault"] == 2 and first["other_principal"] >= 1
    pulled = t.pull()                                              # the source is not approved yet: placed, waiting, a warning
    assert pulled["results"]["placed"] == "2" and pulled["results"]["capture"] == "not_approved" and pulled["state"] == "warn"
    assert t.production_records() == {}
    t.approve()
    t.clock.advance(minutes=10)
    pulled, synced, recon = t.carry()
    assert pulled["results"]["capture"] == "ok" and int(pulled["results"]["captured"]) == 2
    assert set(t.production_records()) == {t.key("kiln"), t.key("glaze")}
    assert synced["results"]["backlog_records"] == "0"
    assert (recon["in_vault_current"], recon["missing_in_vault"], recon["in_vault_stale"], recon["lag_oldest_s"]) == (2, 0, 0, 0)
    text = t.everything_text()
    assert VISITOR not in text                                     # another principal's conversation never travelled anywhere
    folder = t.production_records()[t.key("kiln")]["dir"]
    assert "KILN-SECRET" in ((t.vault / "current" / "shelf").resolve() / "sessions-raw" / folder / f"{folder}.md").read_text()


def test_a_change_in_production_adds_an_edition_in_the_production_vault(t):
    t.export(); t.approve(); t.carry()
    t.portal.say(t.convs["kiln"], ("owner", "KILN-SECRET and Thursday?"), ("mc", "KILN-SECRET Thursday works"))
    t.clock.advance(hours=2)
    out = t.export()
    assert out["changed"] == 1 and out["sent"] == 1 and out["in_vault_stale"] == 1 and out["lag_oldest_s"] == 0
    t.clock.advance(minutes=30)
    _, _, recon = t.carry()
    assert recon["in_vault_current"] == 2 and recon["in_vault_stale"] == 0
    shelf = (t.vault / "current" / "shelf").resolve()
    rid = t.production_records()[t.key("kiln")]["id"]
    assert V.record_versions(shelf)[0][rid].edition == 2


def test_an_export_upload_killed_before_complete_is_resent_and_lands(t):
    t.approve()

    def dying(local, key, replace=False):
        if key.endswith(B.COMPLETE):
            raise RuntimeError("killed before COMPLETE")
        return B.Dest(t.dest).put(local, key, replace=replace)
    with pytest.raises(Exception):
        t.export(put=dying)
    assert t.pull()["results"]["batches"] == "0"                   # the orphan is never pulled
    t.clock.advance(minutes=10)
    again = t.export()
    assert again["sent"] == 2
    _, _, recon = t.carry()
    assert recon["in_vault_current"] == 2 and len(t.production_records()) == 2


def test_a_pull_that_dies_before_its_ack_is_repulled_without_rewriting_and_acknowledged(t, monkeypatch):
    t.approve()
    t.export()
    real = MP.MB.ack_doc
    monkeypatch.setattr(MP.MB, "ack_doc", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("killed before the ack")))
    died = t.pull()
    acks = t.destdir / "sync" / "mc-export-acks"
    assert died["state"] == "failed" and not (acks.exists() and list(acks.glob("*.json")))
    stamps = {p.name: p.stat().st_mtime_ns for p in t.watch.iterdir()}
    monkeypatch.setattr(MP.MB, "ack_doc", real)
    t.clock.advance(minutes=10)
    pulled, _, recon = t.carry()
    assert pulled["results"]["unchanged"] == "2" and {p.name: p.stat().st_mtime_ns for p in t.watch.iterdir()} == stamps
    assert recon["acked"] == 2 and recon["in_vault_current"] == 2


def test_a_retry_that_resends_placed_bytes_makes_no_duplicate(t):
    t.approve()
    t.export()
    t.clock.advance(hours=1, minutes=5)                            # no ack yet: the export's retry time passes
    retry = t.export()
    assert retry["sent"] == 2                                      # the same bytes, a second batch
    pulled, _, recon = t.carry()
    assert int(pulled["results"]["batches"]) == 2 and int(pulled["results"]["placed"]) + int(pulled["results"]["unchanged"]) >= 2
    assert len(t.production_records()) == 2 and recon["in_vault_current"] == 2 and recon["pending_files"] == 0
    shelf = (t.vault / "current" / "shelf").resolve()
    held = V.record_versions(shelf)[0].values()
    assert sum(1 for v in held if str(v.source_key).startswith("minimoi-mc:production:")) == 2       # one record per conversation


def test_a_placed_file_lost_before_capture_is_requeued_by_reconciliation_until_the_vault_holds_it(t):
    t.export()
    t.pull()                                                       # placed and acknowledged, but not captured (not approved)
    for p in t.watch.iterdir():
        if p.name.endswith(f"{t.convs['glaze']['id']}.json"):
            p.unlink()                                             # lost on the Mac before any capture
    t.approve()
    _, _, recon = t.carry()                                        # kiln lands; glaze is missing, still inside the grace
    assert recon["in_vault_current"] == 1 and recon["missing_in_vault"] == 1 and recon["gap_requeued"] == 0
    t.clock.advance(hours=13)                                      # past the 12 h grace
    requeue = t.export()
    assert requeue["gap_requeued"] == 1 and requeue["sent"] == 1
    _, _, recon = t.carry()
    assert recon["in_vault_current"] == 2 and recon["missing_in_vault"] == 0 and recon["lag_oldest_s"] == 0

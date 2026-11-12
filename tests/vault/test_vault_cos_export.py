"""``vault-cos-export``, the EC2 side of the production CoS capture path (11 Nov 2026, #509).

Fully synthetic and local, on the ``test_vault_mc_export`` pattern. Production is a synthetic canonical CoS history (``cos_history``) read
by the job's own in-process exporter; the destination is file://, encryption the transport tests' age (the pinned real one when
installed). The Mac pulls with the REAL ``core.memory_sync.cos_pull.run_pull`` into a watch folder; capture is the real ``minimoi-cos``
watcher on a synthetic shelf, and "publishing" copies that shelf into a new serving release (``<vault>/current -> releases/<id>``).
No network, no AWS, no docker, runs as the current user."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.memory_backup import backup as B
from core.memory_backup.backup import BackupError
from core.memory_shelf import approvals, record, watchers
from core.memory_shelf.config import SourceCfg
from core.memory_sync import cos_batch as CB
from core.memory_sync import cos_export as CX
from core.memory_sync import cos_pull as CP
from core.memory_sync import mc_batch as MB
from core.memory_sync import mc_export as MX
from tests.memory_backup.test_production_transport import make_age
from tests.memory_shelf.helpers import make_shelf
from tests.memory_shelf.test_mc_capture import OWNER
from tests.vault.cos_history import History, name

REPO = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 11, 11, 16, 0, tzinfo=timezone.utc)
H = timedelta(hours=1)
SECRETS = ("KILN-SECRET", "GLAZE-SECRET", "PRIVATE-SECRET", "NULLPRIV-SECRET", "DEV-SECRET", "TITLE-SECRET")
_spec = importlib.util.spec_from_file_location("vault_cos_export_cli", REPO / "scripts/vault/vault_cos_export.py")
CLI = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CLI)


class Mac:
    def __init__(self, w):
        self.w = w
        self.watch = w.tmp / "mac" / "cos-export"
        self.shelf = make_shelf(w.tmp / "mac")
        self.cfg = SourceCfg("minimoi-cos", "cos", self.watch, ())
        self.approved = False
        self.config = w.tmp / "mac" / "pull-config.json"
        self.config.write_text(json.dumps({"schema_version": 1, "shelf_root": str(w.tmp / "mac" / "unused-shelf"),
                                           "inbox_root": str(w.tmp / "mac" / "inbox"), "headroom": {"min_free_bytes": 0},
                                           "never_copy": [], "sources": {}}))

    def pull(self) -> dict:
        """The real ``vault-cos-pull``; its config has no ``minimoi-cos`` source (``not_configured``), so files wait for ``capture``."""
        out = CP.run_pull(dest=self.w.dest, identity_spec=self.w.identity, age_bin=self.w.age, expect_age=None,
                          jobs_root=self.w.tmp / "mac" / "jobs", watch=self.watch, state_dir=self.w.tmp / "mac" / ".vault-cos-pull",
                          config_path=str(self.config), now=lambda: self.w.t, alert_send=lambda text: "not_sent")
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
    w.history = History(tmp_path / "prod" / "cos" / "conversations" / "history")
    w.age, w.recipient, w.identity = make_age(tmp_path)
    w.dest, w.destdir = f"file://{tmp_path / 'dest'}", tmp_path / "dest"
    (tmp_path / "ec2").mkdir(mode=0o755)
    w.vault = tmp_path / "ec2" / "vault"
    w.vault.mkdir()
    w.sync, w.work = tmp_path / "ec2" / "vault-sync", tmp_path / "ec2" / "vault-work"
    w.convs = {"kiln": w.history.conversation(("KILN-SECRET schedule?", "KILN-SECRET fire Tuesday")),
               "glaze": w.history.conversation(("GLAZE-SECRET which?", "GLAZE-SECRET the blue one")),
               "private": w.history.conversation(("PRIVATE-SECRET words", "PRIVATE-SECRET reply"), private=True),
               "nullpriv": w.history.conversation(("NULLPRIV-SECRET words", "NULLPRIV-SECRET reply"), private=None),
               "nopriv": w.history.conversation(("NULLPRIV-SECRET missing", "NULLPRIV-SECRET reply"), omit_private=True),
               "dev": w.history.conversation(("DEV-SECRET words", "DEV-SECRET reply"), environment="dev")}
    w.mac = Mac(w)
    return w


def export(w, **kw):
    args = dict(dest=w.dest, vault_root=str(w.vault), recipient=w.recipient, history_root=str(w.history.root), environment="prod",
                age_bin=w.age, owner_uid=os.geteuid(), require_root=False, now=lambda: w.t)
    args.update(kw)
    return CX.run_export(**args)


def state(w) -> dict:
    return json.loads((w.sync / CX.STATE_NAME).read_text())


def name_of(w, key) -> str:
    return name("prod", w.convs[key])


def batch_names(w, bid) -> list:
    return [f[0] for f in json.loads((w.destdir / CB.EXPORT_PREFIX / bid / B.MANIFEST).read_text())["files"]]


def publish(w):
    rid = B.new_attempt_id(w.t)
    (w.vault / "releases" / rid).mkdir(parents=True)
    shutil.copytree(w.mac.shelf.root, w.vault / "releases" / rid / "shelf")
    tmp = w.vault / ".current-new"
    os.symlink(f"releases/{rid}", tmp)
    os.replace(tmp, w.vault / "current")


def plain_text_everywhere(w) -> str:
    """Every plain object the destination holds, EC2's state and the job's scratch: ciphertext excluded."""
    out = []
    for base in (w.destdir, w.sync, w.work):
        for p in base.rglob("*") if base.exists() else ():
            if p.is_file() and not p.name.endswith(".age"):
                out.append(p.read_bytes().decode("utf-8", "replace"))
    return "\n".join(out)


# ── selection: private never, other environments never, changed only ──

def test_only_non_private_conversations_of_this_environment_are_sent(w):
    out = export(w)
    assert out["export"] == "ok" and out["conversations"] == 2 and out["sent"] == 2
    assert (out["private"], out["other_environment"], out["unreadable"]) == (3, 1, 0)
    assert sorted(batch_names(w, out["batch"])) == sorted([name_of(w, "kiln"), name_of(w, "glaze")])
    assert "other_principal" not in out and "off_record" not in out
    text = plain_text_everywhere(w)
    assert not any(s in text for s in SECRETS)                     # ids, hashes and counts only in plain text
    results = w.mac.pull()
    assert results["placed"] == "2" and sorted(p.name for p in w.mac.watch.iterdir()) == sorted([name_of(w, "kiln"), name_of(w, "glaze")])
    w.mac.capture()
    shelf_text = "\n".join(p.read_text("utf-8", "replace") for p in w.mac.shelf.root.rglob("*.md"))
    assert "KILN-SECRET" in shelf_text and not any(s in shelf_text for s in ("PRIVATE-SECRET", "NULLPRIV-SECRET", "DEV-SECRET"))


def test_a_conversation_whose_privacy_cannot_be_read_is_not_sent_and_the_export_is_partial(w):
    broken = w.history.conversation(("GLAZE-SECRET broken", None))
    w.history.meta_path(broken).write_text("{not json")
    stray = w.history.root / "conversations" / "notes.json"
    stray.write_text("{}")
    out = export(w)
    assert out["export"] == "partial" and out["unreadable"] == 1 and out["sent"] == 2
    assert name("prod", broken) not in batch_names(w, out["batch"])


def test_a_journal_mid_append_waits_for_the_next_run(w):
    with w.history.turns_path(w.convs["glaze"]).open("a") as f:
        f.write('{"schema": "minimoi.cos.turn/1", "conversation_id": "')        # the writer is mid-line
    out = export(w)
    assert out["export"] == "partial" and out["unstable"] == 1 and batch_names(w, out["batch"]) == [name_of(w, "kiln")]


def test_a_linked_history_folder_fails_the_export(w, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    shutil.move(str(w.history.root / "turns"), elsewhere)
    os.symlink(elsewhere, w.history.root / "turns")
    with pytest.raises(BackupError) as exc:
        export(w)
    assert exc.value.code == "export_failed"


def test_an_unchanged_second_run_sends_nothing_and_a_new_turn_is_sent(w):
    first = export(w)
    w.t += H / 4
    again = export(w)
    assert again["sent"] == 0 and again["changed"] == 0
    w.mac.pull()
    w.t += H / 4
    acked = export(w)
    assert acked["acked"] == 2 and acked["sent"] == 0 and acked["pending_files"] == 0
    w.history.say(w.convs["glaze"], "GLAZE-SECRET and the rim?", "GLAZE-SECRET glossy")
    w.t += H / 4
    out = export(w)
    assert out["changed"] == 1 and out["sent"] == 1 and batch_names(w, out["batch"]) == [name_of(w, "glaze")]
    assert out["batch"] > first["batch"]


def test_the_cos_job_keeps_its_own_state_lock_and_scratch(w):
    export(w)
    assert (w.sync / CX.STATE_NAME).is_file() and not (w.sync / MX.STATE_NAME).exists()
    assert state(w)["format"] == CX.STATE_FORMAT
    assert [p.name for p in w.work.iterdir()] == []
    stale = w.work / ".mcx-killed"
    stale.mkdir()
    (stale / "x").write_text("MC's scratch")
    export(w)
    assert stale.exists()                                          # MC's leftovers are MC's job to wipe


def test_a_held_lock_is_cos_export_busy(w):
    with CX.admission(str(w.vault), owner_uid=os.geteuid()):
        with pytest.raises(BackupError) as exc:
            export(w)
    assert exc.value.code == "cos_export_busy"
    with MX.Admission(str(w.vault), owner_uid=os.geteuid()):       # MC's lock is another file: no contention
        assert export(w)["sent"] == 2


# ── acknowledgements ──

def test_a_rejected_ack_is_retried_after_backoff_and_a_malformed_ack_is_counted(w):
    first = export(w)
    d = B.Dest(w.dest)
    files = [[n, s, "rejected:file_hash_mismatch"] for n, s in state(w)["pending"][first["batch"]]["files"].items()]
    ack = w.tmp / "ack.json"
    ack.write_text(json.dumps(MB.ack_doc(first["batch"], files, w.t, CB.COS)))
    d.put(ack, f"{CB.ACK_PREFIX}/{first['batch']}.json", replace=True)
    w.t += H / 2
    waiting = export(w)
    assert waiting["rejected"] == 2 and waiting["sent"] == 0 and waiting["pending_files"] == 2
    w.t += H
    retried = export(w)
    assert retried["sent"] == 2 and list(state(w)["pending"]) == [retried["batch"]]
    ack.write_text(json.dumps({"format": MB.ACK_FORMAT, "batch_id": retried["batch"], "files": []}))      # MC's format: not ours
    d.put(ack, f"{CB.ACK_PREFIX}/{retried['batch']}.json", replace=True)
    w.t += H / 4
    assert export(w)["ack_malformed"] == 1


def test_a_conversation_that_turns_private_leaves_the_pending_set(w):
    export(w)
    meta = json.loads(w.history.meta_path(w.convs["glaze"]).read_text())
    meta["private"] = True
    w.history.meta_path(w.convs["glaze"]).write_text(json.dumps(meta))
    w.t += 2 * H
    out = export(w)
    assert out["gone_from_export"] == 1 and out["private"] == 4
    assert all(name_of(w, "glaze") not in p["files"] for p in state(w)["pending"].values())


# ── the trip and gap repair against the serving Vault ──

def test_the_trip_reconciles_against_the_serving_vault(w):
    export(w)
    w.mac.pull()
    w.t += H / 2
    assert w.mac.capture()["counts"] == {"captured": 2}
    key = f"minimoi-cos:prod:{w.convs['kiln']}"
    main = w.mac.shelf.main_path(w.mac.shelf.index()[key])
    meta, _ = record.load(record.read(main))
    assert meta["source_hash"] == B.sha256_file(w.mac.watch / name_of(w, "kiln"))
    second = export(w)
    assert second["acked"] == 2 and second["missing_in_vault"] == 2
    publish(w)
    third = export(w)
    assert (third["in_vault_current"], third["missing_in_vault"], third["lag_oldest_s"], third["sent"]) == (2, 0, 0, 0)


def test_gap_repair_requeues_an_acked_conversation_missing_from_the_vault_past_the_grace_once(w):
    export(w)
    w.mac.pull()
    (w.mac.watch / name_of(w, "glaze")).unlink()                   # lost on the Mac before any capture
    w.mac.capture()
    w.t += H / 2
    export(w)                                                      # reads the acks
    publish(w)
    w.t += 6 * H
    inside = export(w)
    assert inside["gap_requeued"] == 0 and inside["missing_in_vault"] == 1
    w.t += 7 * H                                                   # past the 12 h grace after the ack
    requeue = export(w)
    assert requeue["gap_requeued"] == 1 and requeue["sent"] == 1 and batch_names(w, requeue["batch"]) == [name_of(w, "glaze")]
    w.mac.pull()
    w.mac.capture()
    w.t += H / 2
    export(w)
    publish(w)
    final = export(w)
    assert final["in_vault_current"] == 2 and final["missing_in_vault"] == 0 and final["gap_requeued"] == 0


# ── the job script ──

def cli(w, *argv):
    return CLI.main(["--dest", w.dest, "--vault-root", str(w.vault), "--recipient", w.recipient, "--history", str(w.history.root),
                     "--age-bin", w.age, "--expect-age", "", "--jobs-root", str(w.tmp / "jobs"), "--_allow-non-root", *argv],
                    now=lambda: w.t)


def test_the_script_runs_writes_a_counts_only_status_and_refuses_when_busy(w, monkeypatch, capsys):
    assert cli(w, "run") == CLI.EXIT_REFUSED                       # plan-only without --execute
    assert cli(w, "run", "--execute") == CLI.EXIT_OK
    doc = json.loads((w.tmp / "jobs" / "vault-cos-export.json").read_text())
    assert doc["state"] == "ok" and doc["results"]["sent"] == "2" and doc["results"]["private"] == "3"
    assert not any(s in json.dumps(doc) for s in SECRETS)
    monkeypatch.setattr(CLI.alert, "send", lambda text: (_ for _ in ()).throw(AssertionError("no alert for busy")))
    before = (w.tmp / "jobs" / "vault-cos-export.json").read_bytes()
    with CX.admission(str(w.vault), owner_uid=os.geteuid()):
        assert cli(w, "run", "--execute") == CLI.EXIT_REFUSED
    assert (w.tmp / "jobs" / "vault-cos-export.json").read_bytes() == before
    assert "refused: cos_export_busy" in capsys.readouterr().err
    assert cli(w, "--environment", "qa", "run", "--execute") == CLI.EXIT_REFUSED


def test_the_plan_reads_nothing_but_counts(w, capsys):
    assert cli(w, "plan") == CLI.EXIT_OK
    plan = json.loads(capsys.readouterr().out)
    assert plan["history"] == "ok" and plan["history_conversations"] == 6 and plan["environment"] == "prod"
    assert not (w.sync / CX.STATE_NAME).exists()

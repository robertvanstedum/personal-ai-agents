"""``vault-mc-export`` after the adversarial review and Codex's admission blocker (17 Oct 2026).

Each test is a reviewer's repro turned round to assert the FIX, on the end-to-end Trip of tests/vault/test_mc_e2e.py (every job real:
the exporter in-process, the real ``vault-mc-pull``, ``vault-sync``, ``vault-apply``; ``file://`` for S3, an injected clock):

- Codex blocker: the job script takes the mc-export lock BEFORE its status start; a competitor refused at the lock writes nothing.
- HIGH-1: an unparseable ack is "no ack" for that batch only; its files go out again on the backoff; a failed download stays an error.
- MEDIUM-1: a conversation that keeps changing cannot hide a dead Mac (``behind_since``, ``oldest_unacked_s``).
- MEDIUM-2: gap repair re-sends a given (name, sha256) at most once; a conversation whose turns are all blank is never exported.
- MEDIUM-3: an unreadable serving index makes reconciliation unknown for the run; changed files are still sent."""
from __future__ import annotations

import importlib.util
import json
import os

import pytest

from core.memory_backup import backup as B
from core.memory_backup.backup import BackupError
from core.memory_sync import mc_batch as MB
from core.memory_sync import mc_export as MX
from tests.memory_shelf.test_mc_capture import PRINCIPAL, X
from tests.vault.test_mc_e2e import ROOT, t  # noqa: F401  (fixture)

H = 3600


def state(t) -> dict:
    return json.loads((t.vault.parent / "vault-sync" / MX.STATE_NAME).read_text())


def load_script():
    spec = importlib.util.spec_from_file_location("vault_mc_export_review", ROOT / "scripts" / "vault" / "vault_mc_export.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def cli(t, jobs) -> list:
    return ["--dest", t.dest, "--vault-root", str(t.vault), "--recipient", t.recipient, "--principal", PRINCIPAL, "--age-bin", t.age,
            "--expect-age", "", "--jobs-root", str(jobs), "--registry", str(t.tmp / "no-registry.json"), "--_allow-non-root",
            "run", "--execute"]


def exporter(t):
    def run(argv, stdout):
        return X.main(argv[argv.index(MX.EXPORTER) + 1:] + ["--guild-dir", str(t.portal.guild)], stream=stdout)
    return run


# ── Codex blocker: one admission across status start, work and status finish ──

def test_a_competitor_refused_at_the_lock_leaves_the_active_running_receipt_byte_identical(t, monkeypatch, capsys):
    vme = load_script()
    monkeypatch.setattr(vme.alert, "send", lambda text: (_ for _ in ()).throw(AssertionError("no alert for busy")))
    jobs = t.tmp / "ec2-jobs"
    receipt = jobs / "vault-mc-export.json"
    seen = {}
    real = exporter(t)

    def active(argv, stdout):                                       # runs inside the active run: after its status start, under its lock
        seen["receipt"] = receipt.read_bytes()
        capsys.readouterr()
        seen["exit"] = vme.main(cli(t, jobs), runner=lambda *a: pytest.fail("the competitor ran the exporter"), now=t.clock)
        seen["err"] = capsys.readouterr().err
        seen["after"] = receipt.read_bytes()
        return real(argv, stdout)
    assert vme.main(cli(t, jobs), runner=active, now=t.clock) == 0
    assert seen["exit"] == 2 and seen["err"].strip() == "refused: mc_export_busy"
    assert seen["after"] == seen["receipt"] and json.loads(seen["receipt"])["state"] == "running"
    final = json.loads(receipt.read_text())
    assert final["state"] == "ok" and final["run_id"] == json.loads(seen["receipt"])["run_id"] and final["results"]["sent"] == "2"
    # LOW-5: a busy run never touches the receipt, so it can never advance last_success_at.
    done = receipt.read_bytes()
    with MX.Admission(str(t.vault), owner_uid=os.geteuid()):
        assert vme.main(cli(t, jobs), runner=real, now=t.clock) == 2
    assert receipt.read_bytes() == done
    capsys.readouterr()


# ── HIGH-1: an unparseable ack ──

def test_an_unparseable_ack_is_no_ack_for_that_batch_only_and_its_files_go_out_again(t):
    first = t.export()
    ack = t.destdir / MB.ACK_PREFIX / f"{first['batch']}.json"
    ack.parent.mkdir(parents=True, exist_ok=True)
    ack.write_text("{not json")
    t.converse("new", ("owner", "NEW words"), ("mc", "NEW reply"))
    t.clock.advance(minutes=30)
    out = t.export()                                               # not wedged: the new conversation goes out
    assert out["ack_malformed"] == 1 and out["sent"] == 1 and out["changed"] == 1
    t.clock.advance(minutes=45)                                    # the first batch's 1 h backoff passes (not yet the second's)
    out = t.export()
    assert out["ack_malformed"] == 1 and out["sent"] == 2 and first["batch"] not in state(t)["pending"]
    t.clock.advance(minutes=10)
    assert t.export()["ack_malformed"] == 0                        # the old batch and its unreadable ack left the state
    t.approve()
    _, _, recon = t.carry()
    assert recon["in_vault_current"] == 3 and recon["pending_files"] == 0


def test_a_failed_ack_download_is_still_a_hard_error_and_changes_nothing(t, monkeypatch):
    t.export()
    before = state(t)
    real = B.Dest.get_optional

    def broken(self, key, local, *a, **k):
        if key.startswith(MB.ACK_PREFIX):
            raise BackupError("download_failed")
        return real(self, key, local, *a, **k)
    monkeypatch.setattr(B.Dest, "get_optional", broken)
    t.clock.advance(hours=2)
    with pytest.raises(BackupError) as exc:
        t.export()
    assert exc.value.code == "download_failed" and state(t) == before


# ── MEDIUM-1: a dead Mac behind active conversations ──

def test_active_conversations_cannot_hide_a_dead_mac(t, monkeypatch, capsys):
    t.approve()
    t.export()
    t.carry()
    t.clock.advance(minutes=30)
    _, _, recon = t.carry()
    assert recon["in_vault_current"] == 2 and recon["lag_oldest_s"] == 0 and recon["oldest_unacked_s"] == 0
    # The Mac dies here; the owner keeps talking in both conversations every 6 h for 3 days.
    out = None
    for i in range(12):
        t.clock.advance(hours=6)
        for key in ("kiln", "glaze"):
            t.portal.say(t.convs[key], ("owner", f"more {i}"))
        out = t.export()
        assert out["sent"] == 2
    assert out["acked"] == 0 and out["in_vault_current"] == 0
    assert out["lag_oldest_s"] == 66 * H and out["behind_past_grace"] == 2             # behind since the first change, 66 h ago
    assert out["oldest_unacked_s"] == 66 * H and out["unacked_past_grace"] == 2       # sent, never acknowledged, for 66 h
    vme = load_script()
    monkeypatch.setattr(vme.alert, "send", lambda text: "not_sent")
    jobs = t.tmp / "ec2-jobs"
    t.clock.advance(hours=1)
    assert vme.main(cli(t, jobs), runner=exporter(t), now=t.clock) == 0
    st = json.loads((jobs / "vault-mc-export.json").read_text())
    assert st["state"] == "warn" and st["results"]["oldest_unacked_s"] == str(67 * H) and st["results"]["lag_oldest_s"] == str(67 * H)
    t.carry()                                                      # the Mac is back: everything lands, both clocks clear
    t.clock.advance(minutes=30)
    _, _, recon = t.carry()
    assert (recon["in_vault_current"], recon["lag_oldest_s"], recon["oldest_unacked_s"]) == (2, 0, 0)
    assert state(t)["behind_since"] == {} and state(t)["unacked_since"] == {}
    capsys.readouterr()


# ── MEDIUM-2: gap repair at most once per (name, sha256) ──

def test_gap_repair_resends_the_same_bytes_at_most_once(t):
    t.export()
    t.pull()                                                       # placed and acknowledged; capture waits on approval
    t.clock.advance(minutes=30)
    t.export()
    requeues, after = [], []
    for _ in range(4):
        t.clock.advance(hours=13)
        out = t.export()
        requeues.append(out["gap_requeued"])
        after.append(out["behind_after_repair"])
        t.pull()                                                   # identical bytes: "unchanged", acknowledged placed again
        t.clock.advance(minutes=30)
        t.export()
    assert requeues == [2, 0, 0, 0] and after == [0, 2, 2, 2]
    assert len(MB.complete_batches(B.Dest(t.dest))) == 2           # the first send and the one re-send
    # New bytes for kiln: sent as a change, and they get their own single re-queue later; glaze stays repaired-and-behind.
    t.portal.say(t.convs["kiln"], ("owner", "KILN-SECRET later"))
    t.clock.advance(hours=1)
    assert t.export()["sent"] == 1
    t.pull()
    t.clock.advance(minutes=30)
    t.export()
    t.clock.advance(hours=13)
    out = t.export()
    assert (out["gap_requeued"], out["behind_after_repair"]) == (1, 1)


def test_a_conversation_of_blank_turns_is_empty_never_sent_and_never_resent(t):
    t.converse("blank", ("owner", "   "), ("owner", "\n\t"))      # kept notes, but the shelf would record no turn (mc.parse)
    t.approve()
    first = t.export()
    assert first["conversations"] == 2 and first["sent"] == 2
    _, _, recon = t.carry()
    t.clock.advance(minutes=30)
    _, _, recon = t.carry()
    assert recon["in_vault_current"] == 2 and recon["missing_in_vault"] == 0
    for _ in range(3):
        t.clock.advance(hours=13)
        out = t.export()
        assert (out["gap_requeued"], out["sent"], out["missing_in_vault"]) == (0, 0, 0)
    assert all(t.convs["blank"]["id"] not in name for name in state(t)["first_seen"])


# ── MEDIUM-3: an unreadable serving index ──

def test_an_unreadable_serving_index_makes_reconciliation_unknown_and_still_sends(t, monkeypatch, capsys):
    t.export()
    t.pull()                                                       # acked, not captured: gap repair would re-queue both past the grace
    t.clock.advance(minutes=30)
    t.export()
    behind = state(t)["behind_since"]
    idx = (t.vault / "current" / "shelf").resolve() / "_ledger" / "source_index.json"
    idx.chmod(0o600)
    idx.write_text("garbage")
    t.converse("new", ("owner", "NEW words"))
    t.clock.advance(hours=13)
    out = t.export()
    assert out["vault"] == "unreadable" and out["sent"] == 1 and out["changed"] == 1
    assert out["gap_requeued"] == 0 and out["in_vault_current"] is None and out["missing_in_vault"] is None
    assert state(t)["behind_since"] == behind and len(state(t)["acked"]) == 2           # nothing judged from an unknown Vault
    vme = load_script()
    monkeypatch.setattr(vme.alert, "send", lambda text: (_ for _ in ()).throw(AssertionError("not a failure")))
    jobs = t.tmp / "ec2-jobs"
    assert vme.main(cli(t, jobs), runner=exporter(t), now=t.clock) == 0
    st = json.loads((jobs / "vault-mc-export.json").read_text())
    assert st["state"] == "warn" and st["results"]["vault"] == "unreadable" and st["results"]["in_vault_current"] == "unknown"
    capsys.readouterr()

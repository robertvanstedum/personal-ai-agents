"""Rooms identity (draft 3): the binding store, the key file and the provisioning script. Access is data the owner controls; every refusal path
fails closed; nothing secret is printed."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from minimoi_portal import rooms_identity as RI

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "staging" / "rooms_identity.py"


def write(directory, doc):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "bindings.json").write_text(doc if isinstance(doc, str) else json.dumps(doc))


def row(account="account:robert", principal="robert", active=True):
    return {"account": account, "principal": principal, "active": active}


def test_no_file_means_nobody_has_access(tmp_path):
    d = RI.RoomsAccess(str(tmp_path / "none")).decide("account:robert")
    assert d.kind == "no_access"


def test_a_bound_active_account_acts_as_its_principal_and_only_it(tmp_path):
    write(tmp_path, {"version": 1, "bindings": [row(), row("account:isabella", "isabella", False)]})
    access = RI.RoomsAccess(str(tmp_path))
    d = access.decide("account:robert")
    assert d.ok and d.principal == "robert"
    assert access.decide("account:isabella").kind == "no_access"                    # withdrawn
    assert access.decide("account:test-agent-full").kind == "no_access"             # never bound
    assert access.decide("account:robert", account_active=False).kind == "no_access"  # the account itself is gone or changed
    assert access.decide(None).kind == "no_access"


@pytest.mark.parametrize("doc", ["{not json", "[]", '{"version": 2, "bindings": []}', '{"version": 1, "bindings": "x"}',
                                 '{"version": 1, "bindings": [{"account": "a"}]}',
                                 '{"version": 1, "bindings": [{"account": "a", "principal": "p", "active": "yes"}]}', ""])
def test_a_damaged_file_is_unavailable_never_a_grant(tmp_path, doc):
    write(tmp_path, doc)
    assert RI.RoomsAccess(str(tmp_path)).decide("account:robert").kind == "unavailable"


@pytest.mark.parametrize("principal", ["", "Robert", "robert extra", "../x", "a" * 61, "robert\n"])
def test_an_invalid_principal_in_a_binding_is_refused(tmp_path, principal):
    write(tmp_path, {"version": 1, "bindings": [row(principal=principal)]})
    assert RI.RoomsAccess(str(tmp_path)).decide("account:robert").kind == "unavailable"


def test_the_key_file_must_be_well_formed(tmp_path):
    good = tmp_path / "good.key"
    good.write_text("k1:" + "s" * 40 + "\n")
    assert RI.load_key(str(good)) == ("k1", "s" * 40)
    for text in ("", "nokid", "k1:short", ":" + "s" * 40):
        bad = tmp_path / "bad.key"
        bad.write_text(text)
        assert RI.load_key(str(bad)) is None
    assert RI.load_key(str(tmp_path / "missing")) is None and RI.load_key(None) is None


def test_the_seed_creates_the_owner_row_once_and_audits_it(tmp_path):
    access = RI.RoomsAccess(str(tmp_path / "rooms"))
    assert access.seed_owner("account:robert", "robert") is True
    assert access.seed_owner("account:robert", "robert") is False                     # never overwrites
    assert access.decide("account:robert").ok
    lines = (tmp_path / "rooms" / "audit.jsonl").read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["event"] == "seed"
    assert stat.S_IMODE((tmp_path / "rooms" / "bindings.json").stat().st_mode) == 0o600
    with pytest.raises(ValueError):
        RI.RoomsAccess(str(tmp_path / "other")).seed_owner("account:x", "Not Valid")


def test_the_provisioning_script_creates_the_key_and_the_binding_once_and_prints_no_secret(tmp_path):
    env = {**os.environ, "STAGING_ROOT": str(tmp_path)}
    run = lambda *a: subprocess.run([sys.executable, str(SCRIPT), *a], capture_output=True, text=True, env=env, timeout=60)
    first = run("provision")
    assert first.returncode == 0, first.stderr
    key = (tmp_path / "secrets" / "rooms-bridge" / "portal-bridge.key")
    secret = key.read_text().split(":", 1)[1].strip()
    assert secret not in first.stdout + first.stderr
    assert stat.S_IMODE(key.stat().st_mode) == 0o600 and stat.S_IMODE(key.parent.stat().st_mode) == 0o700
    again = run("provision")
    assert "already in place" in again.stdout and key.read_text().split(":", 1)[1].strip() == secret
    out = run("status")
    assert out.returncode == 0 and "account:robert -> robert (active)" in out.stdout and secret not in out.stdout
    assert RI.RoomsAccess(str(tmp_path / "data" / "guild" / "rooms")).decide("account:robert").ok


def test_a_grant_is_pending_until_completed_never_changes_its_principal_and_never_touches_the_owner(tmp_path):
    access = RI.RoomsAccess(str(tmp_path))
    access.seed_owner("account:robert", "robert")
    row = access.begin_grant("account:dana", "Dana", "robert")
    assert row["active"] is False and row["pending"] is True and row["principal"] == RI.principal_for("account:dana")
    assert access.decide("account:dana").kind == "no_access"                         # not usable while pending
    assert access.begin_grant("account:dana", "Dana", "robert")["principal"] == row["principal"]   # idempotent: the same person
    assert access.complete_grant("account:dana", "robert")["active"] is True
    assert access.decide("account:dana").principal == row["principal"] and row["principal"].startswith("u-dana-")
    assert access.withdraw("account:dana", "robert")["active"] is False
    assert access.decide("account:dana").kind == "no_access"
    with pytest.raises(ValueError):
        access.withdraw("account:robert", "robert")
    assert access.decide("account:robert").ok
    audit = [json.loads(l) for l in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert [a["event"] for a in audit] == ["seed", "grant_started", "grant_retry", "grant_completed", "withdrawn"]


def test_two_grants_at_once_make_one_person_and_one_row(tmp_path):
    import threading
    access = RI.RoomsAccess(str(tmp_path))
    access.seed_owner("account:robert", "robert")
    out = []
    barrier = threading.Barrier(6)

    def go():
        barrier.wait()
        out.append(access.begin_grant("account:dana", "Dana", "robert")["principal"])
    threads = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    rows = json.loads((tmp_path / "bindings.json").read_text())["bindings"]
    assert len({*out}) == 1 and [r["account"] for r in rows].count("account:dana") == 1


def test_a_derived_principal_is_always_u_prefixed_unique_per_account_and_valid():
    ids = {RI.principal_for(a) for a in ("account:dana", "account:Dana", "account:dana@x.com", "account:José Silva", "account:!!!")}
    assert len(ids) == 5 and all(i.startswith("u-") and RI.PRINCIPAL.fullmatch(i) for i in ids)
    assert not any(i in ("robert", "mc", "codex", "claude-code") for i in ids)

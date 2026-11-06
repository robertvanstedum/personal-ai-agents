"""scripts/operations/mc_prod_db_migrate.sh: every run ends with dispatcher 002 then 003, which the partnership worker needs.
Host runtime P1-a's supervision 003 comes only by the explicit --host-runtime, last in the one transaction; a plain run
never applies it. The script runs here against a stand-in ``docker`` that records every call and answers as production's
containers would; no container, database or production host is touched (the rehearse mode only: apply writes a backup
under /opt)."""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "operations" / "mc_prod_db_migrate.sh"
PARTNERSHIP = ["core/mc_delegation_dispatcher/sql/002_host_runtime_v2.sql",
               "core/mc_delegation_dispatcher/sql/003_task_fences.sql"]
HOST_RUNTIME = ["core/supervision/sql/003_runtime_decisions_and_watch.sql"]

DOCKER = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["DOCKER_LOG"], "a") as fh:
    fh.write(json.dumps(args) + "\n")
text = " ".join(args)
if args[:1] == ["inspect"]:
    print("postgres:18")
elif args[:1] == ["run"]:
    print("0123456789ab")
elif "grep -rliE" in text or (args[:1] == ["exec"] and "grep" in args and "-rliE" in args):
    sys.exit(1)                                        # no migration controls its own transaction
elif "select rolname" in text:
    print("minimoi")
elif "select tableowner" in text:
    print("minimoi")
elif "pg_get_userbyid" in text:
    print("postgres")
elif "has_database_privilege" in text:
    print("t")
elif "select count(*) from pg_tables" in text:
    print("50")
elif "column_name='queue_seq'" in text:
    print(os.environ.get("STUB_PARTNERSHIP_OBJECTS", "t"))
elif "supervision_decision_messages" in text:
    print("t")
elif "select count(*) from guild.mc_delegations" in text:
    print("3")
elif "select 1 from pg_namespace" in text:
    print("1")
'''


@pytest.fixture
def sandbox(tmp_path):
    if shutil.which("bash") is None:
        pytest.skip("no bash")
    mig = tmp_path / "mig"
    for line in SCRIPT.read_text().splitlines():
        line = line.strip()
        if line.endswith(".sql") and "/" in line and not line.startswith("#"):
            src = ROOT / line
            (mig / line).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, mig / line)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "docker").write_text(DOCKER.replace("#!/usr/bin/env python3", f"#!{sys.executable}"))
    (bin_ / "docker").chmod(0o755)
    return tmp_path, mig, bin_


def _run(sandbox, *args, **extra):
    tmp, mig, bin_ = sandbox
    log = tmp / f"docker-{len(list(tmp.glob('docker-*')))}.log"
    env = {"PATH": f"{bin_}:/usr/bin:/bin", "MC_MIGRATIONS_DIR": str(mig), "DOCKER_LOG": str(log), "LANG": "C", **extra}
    proc = subprocess.run(["/bin/bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)
    import json
    calls = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
    return proc, calls


def _applied(calls):
    (call,) = [c for c in calls if "--single-transaction" in c]
    return [a.replace("/mc-migrations/", "") for i, a in enumerate(call) if i and call[i - 1] == "-f"]


def test_a_plain_run_ends_with_the_workers_002_then_003_and_leaves_out_supervision_003(sandbox):
    proc, calls = _run(sandbox, "rehearse")
    assert proc.returncode == 0, proc.stderr
    applied = _applied(calls)
    assert applied[-2:] == PARTNERSHIP                                                  # 002's columns first, after all the rest
    assert not set(HOST_RUNTIME) & set(applied)
    assert applied.index("core/mc_delegation_dispatcher/sql/001_delegations.sql") < applied.index(PARTNERSHIP[0])
    assert len(applied) == len(set(applied))
    assert [c for c in calls if any("queue_seq" in a and "mc_task_fences" in a for a in c)]   # their objects are checked
    assert "dispatcher 002 and 003 objects present" in proc.stdout and "host runtime" not in proc.stdout
    (call,) = [c for c in calls if "--single-transaction" in c]
    assert call[call.index("SET ROLE minimoi") + 1:call.index("SET ROLE minimoi") + 3] == ["-c", "SET LOCAL lock_timeout = '5s'"]


def test_a_run_whose_objects_are_missing_is_refused(sandbox):
    proc, _ = _run(sandbox, "rehearse", STUB_PARTNERSHIP_OBJECTS="f")
    assert proc.returncode == 2 and "partnership objects missing" in proc.stderr


def test_apply_runs_the_same_list_and_the_same_check_as_the_rehearsal():
    """apply writes its backup under /opt, so it isn't run here. Both modes go through timed_apply and the one MIGRATIONS."""
    script = SCRIPT.read_text()
    assert 'timed_apply "$R"' in script and 'timed_apply "$PG"' in script
    assert script.count('"${MIGRATIONS[@]}"') == 2 and 'verify_partnership "$1"' in script


def test_the_mc_image_ships_every_listed_migration():
    """The Dockerfile copies whole folders to mc-migrations, so a listed file can't be left behind."""
    docker = (ROOT / "docker/mc-agent-container/Dockerfile").read_text()
    listed = {line.strip() for line in SCRIPT.read_text().splitlines() if line.strip().endswith(".sql") and "/" in line and not line.strip().startswith("#")}
    assert set(PARTNERSHIP) <= listed
    for rel in listed:
        folder = rel.rsplit("/", 1)[0] + "/"
        assert f"COPY --chown=root:root {folder} /opt/minimoi/mc-release/mc-migrations/{folder}" in docker, rel


def _statements(rel):
    text = (ROOT / rel).read_text()
    return re.sub(r"--[^\n]*", "", text).upper()


def test_the_workers_two_migrations_only_add_and_can_run_again():
    fences = _statements(PARTNERSHIP[1])
    assert not re.search(r"\b(DROP|DELETE|TRUNCATE|UPDATE|BEGIN|COMMIT)\b", fences)
    assert fences.count("CREATE TABLE IF NOT EXISTS") == fences.count("CREATE TABLE") == 2
    assert fences.count("ADD COLUMN IF NOT EXISTS") == fences.count("ADD COLUMN") == 2
    assert fences.count("CREATE UNIQUE INDEX IF NOT EXISTS") == fences.count("CREATE UNIQUE INDEX") == 1
    assert "SET LOCAL LOCK_TIMEOUT = '5S';" in fences
    v2 = _statements(PARTNERSHIP[0])
    # The one DROP replaces 001's narrower checks with wider ones in the same block, only while they lack the new values.
    assert re.findall(r"\bDROP\s+\w+", v2) == ["DROP CONSTRAINT"]
    assert not re.search(r"\b(DELETE|TRUNCATE)\b|UPDATE GUILD", v2)
    assert v2.count("ADD COLUMN IF NOT EXISTS") == v2.count("ADD COLUMN")
    assert v2.count("CREATE TABLE IF NOT EXISTS") == v2.count("CREATE TABLE")
    assert v2.count("CREATE INDEX IF NOT EXISTS") == v2.count("CREATE INDEX")


def test_the_opt_in_adds_supervision_003_last_and_checks_its_objects(sandbox):
    proc, calls = _run(sandbox, "rehearse", "--host-runtime")
    assert proc.returncode == 0, proc.stderr
    applied = _applied(calls)
    assert applied[-3:] == [*PARTNERSHIP, *HOST_RUNTIME]                                # supervision 003 last (C-N7)
    plain = _applied(_run(sandbox, "rehearse")[1])
    assert applied[:-1] == plain                                                        # everything else unchanged
    assert [c for c in calls if any("supervision_outbox" in a for a in c)]             # the objects are checked
    assert "host runtime opt-in" in proc.stdout and "supervision 003 objects present" in proc.stdout
    assert "the one transaction took" in proc.stdout
    (call,) = [c for c in calls if "--single-transaction" in c]
    role = call.index("SET ROLE minimoi")
    assert call[role + 1:role + 3] == ["-c", "SET LOCAL lock_timeout = '5s'"]          # every file's wait bounded (SF1)
    assert call.index("SET LOCAL lock_timeout = '5s'") < call.index("-f")


def test_the_version_names_a_copy_that_knows_the_opt_in(sandbox):
    proc, calls = _run(sandbox, "version")
    assert (proc.returncode, proc.stdout.strip(), calls) == (0, "mc-migrate partnership-1", [])
    proc, _ = _run(sandbox, "version", "--host-runtime")
    assert proc.returncode == 2


@pytest.mark.parametrize("args", [("rehearse", "--host"), ("rehearse", "--host-runtime", "x"), ("nope",)])
def test_anything_else_is_refused_before_any_container_is_touched(sandbox, args):
    proc, calls = _run(sandbox, *args)
    assert proc.returncode == 2 and calls == []


def test_002_and_003_each_bound_their_lock_wait_and_keep_their_transaction_lines_strippable():
    for rel in (PARTNERSHIP[0], *HOST_RUNTIME):
        lines = [x.strip() for x in (ROOT / rel).read_text().splitlines()]
        assert "BEGIN;" in lines and "COMMIT;" in lines and "SET LOCAL lock_timeout = '5s';" in lines
        assert lines.index("SET LOCAL lock_timeout = '5s';") == lines.index("BEGIN;") + 1

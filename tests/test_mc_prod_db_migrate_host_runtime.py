"""Host runtime P1-a: scripts/operations/mc_prod_db_migrate.sh applies dispatcher 002 and then supervision 003 only by
the explicit --host-runtime, last in its one transaction; a plain run (operations' run for other teams' migrations)
never does. The script runs here against a stand-in ``docker`` that records every call and answers as production's
containers would; no container, database or production host is touched (the rehearse mode only: apply writes a backup
under /opt)."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "operations" / "mc_prod_db_migrate.sh"
HOST_RUNTIME = ["core/mc_delegation_dispatcher/sql/002_host_runtime_v2.sql",
                "core/supervision/sql/003_runtime_decisions_and_watch.sql"]

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


def _run(sandbox, *args):
    tmp, mig, bin_ = sandbox
    log = tmp / f"docker-{len(list(tmp.glob('docker-*')))}.log"
    env = {"PATH": f"{bin_}:/usr/bin:/bin", "MC_MIGRATIONS_DIR": str(mig), "DOCKER_LOG": str(log), "LANG": "C"}
    proc = subprocess.run(["/bin/bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)
    import json
    calls = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
    return proc, calls


def _applied(calls):
    (call,) = [c for c in calls if "--single-transaction" in c]
    return [a.replace("/mc-migrations/", "") for i, a in enumerate(call) if i and call[i - 1] == "-f"]


def test_a_plain_run_never_applies_002_or_003(sandbox):
    proc, calls = _run(sandbox, "rehearse")
    assert proc.returncode == 0, proc.stderr
    applied = _applied(calls)
    assert applied and not set(HOST_RUNTIME) & set(applied)
    assert "core/mc_delegation_dispatcher/sql/001_delegations.sql" in applied
    assert not [c for c in calls if any("queue_seq" in a for a in c)]                   # no host runtime check either
    assert "host runtime" not in proc.stdout
    (call,) = [c for c in calls if "--single-transaction" in c]
    assert not [a for a in call if "lock_timeout" in a]                                 # a plain run's transaction as before


def test_the_opt_in_adds_002_then_003_last_and_checks_their_objects(sandbox):
    proc, calls = _run(sandbox, "rehearse", "--host-runtime")
    assert proc.returncode == 0, proc.stderr
    applied = _applied(calls)
    assert applied[-2:] == HOST_RUNTIME                                                 # 003 last (C-N7)
    plain = _applied(_run(sandbox, "rehearse")[1])
    assert applied[:-2] == plain                                                        # everything else unchanged
    assert [c for c in calls if any("queue_seq" in a for a in c)]                      # the objects are checked
    assert "host runtime opt-in" in proc.stdout and "002 and 003 objects present" in proc.stdout
    assert "the one transaction took" in proc.stdout
    (call,) = [c for c in calls if "--single-transaction" in c]
    role = call.index("SET ROLE minimoi")
    assert call[role + 1:role + 3] == ["-c", "SET LOCAL lock_timeout = '5s'"]          # every file's wait bounded (SF1)
    assert call.index("SET LOCAL lock_timeout = '5s'") < call.index("-f")


def test_the_version_names_a_copy_that_knows_the_opt_in(sandbox):
    proc, calls = _run(sandbox, "version")
    assert (proc.returncode, proc.stdout.strip(), calls) == (0, "mc-migrate host-runtime-1", [])
    proc, _ = _run(sandbox, "version", "--host-runtime")
    assert proc.returncode == 2


@pytest.mark.parametrize("args", [("rehearse", "--host"), ("rehearse", "--host-runtime", "x"), ("nope",)])
def test_anything_else_is_refused_before_any_container_is_touched(sandbox, args):
    proc, calls = _run(sandbox, *args)
    assert proc.returncode == 2 and calls == []


def test_002_and_003_each_bound_their_lock_wait_and_keep_their_transaction_lines_strippable():
    for rel in HOST_RUNTIME:
        lines = [x.strip() for x in (ROOT / rel).read_text().splitlines()]
        assert "BEGIN;" in lines and "COMMIT;" in lines and "SET LOCAL lock_timeout = '5s';" in lines
        assert lines.index("SET LOCAL lock_timeout = '5s';") == lines.index("BEGIN;") + 1

"""MC's read-only planning copy (#587): sparse rules, status file, the token's path, the read-only mount.

Runs scripts/operations/planning_mirror.sh against a local repository served over file:// (partial clone needs a
transport that honours --filter), so nothing reaches GitHub or AWS. Fake `aws` and `systemctl` stand in for the host.
"""
import base64
import importlib.util
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/operations/planning_mirror.sh"
REAL_GIT = shutil.which("git")
TOKEN = "github_pat_FAKE0587planningReadOnly0123456789abcdef"
GH_URL = "https://github.com/robertvanstedum/minimoi-planning.git"
NOW_MD = "initiatives/INIT-2026-0007-interaction-vision/NOW.md"
EXCLUDED = [
    "initiatives/INIT-2026-0007-interaction-vision/partner-interaction-2026-11-13/partner-test-set-TEST/scorers-only/key.md",
    "initiatives/INIT-2026-0007-interaction-vision/partner-interaction-2026-11-13/partner-test-set-TEST/scorers-only/sealed/k.md",
    "library/sources/career-cover-letter-vision/README.md",
    "operations/threads/START_CAREER_2026-10-28.md",
    "initiatives/INIT-2026-0007-interaction-vision/documents/resume-blurb-revision-notes-2026-10-18.md",
    "library/records/Cover-Letter-draft.md",
]
KEPT = [NOW_MD, "README.md", "initiatives/INIT-2026-0007-interaction-vision/partner-interaction-2026-11-13/partner-test-set-TEST/larkspur/q1.md"]


def git(*args, cwd=None, env=None):
    return subprocess.run([REAL_GIT, *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env=env or dict(os.environ, GIT_CONFIG_NOSYSTEM="1", HOME=str(cwd or "."))).stdout.strip()


def commit(src, files, msg):
    for rel, body in files.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    git("add", "-A", cwd=src)
    git("-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-qm", msg, cwd=src)
    return git("rev-parse", "HEAD", cwd=src)


@pytest.fixture
def host(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    git("init", "-q", "-b", "main", cwd=src)
    git("config", "uploadpack.allowFilter", "true", cwd=src)
    sha = commit(src, {**{p: f"sealed answer {i}\n" for i, p in enumerate(EXCLUDED)},
                       **{p: f"kept {i}\n" for i, p in enumerate(KEPT)}}, "one")
    home = tmp_path / "home"
    home.mkdir()
    # The script talks to GitHub over https; the test's git rewrites that URL to the local repository.
    (home / ".gitconfig").write_text(f'[url "file://{src}"]\n\tinsteadOf = {GH_URL}\n')
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    calls = tmp_path / "calls.jsonl"
    (bin_ / "aws").write_text(f'#!/bin/bash\nprintf "%s\\n" "aws $*" >> {tmp_path}/aws.argv\n'
                              f'[ -f {tmp_path}/no-token ] && exit 254\necho {TOKEN}\n')
    # Records every git command line and whether the auth header reached it through the environment, then runs git.
    (bin_ / "git").write_text(
        "#!/usr/bin/env python3\nimport json, os, sys\n"
        f"open({str(calls)!r}, 'a').write(json.dumps({{'argv': sys.argv[1:], "
        "'header': os.environ.get('GIT_CONFIG_VALUE_0', ''), 'key': os.environ.get('GIT_CONFIG_KEY_0', '')}) + '\\n')\n"
        f"os.execv({REAL_GIT!r}, [{REAL_GIT!r}] + sys.argv[1:])\n")
    for f in bin_.iterdir():
        f.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", HOME=str(home), GIT_CONFIG_NOSYSTEM="1",
               MINIMOI_PLANNING_ROOT=str(tmp_path / "planning"))
    env.pop("GIT_CONFIG_COUNT", None)
    return {"src": src, "sha": sha, "env": env, "root": tmp_path / "planning", "calls": calls, "tmp": tmp_path}


def refresh(h):
    return subprocess.run(["/bin/bash", str(SCRIPT), "refresh"], env=h["env"], capture_output=True, text=True)


def status(h):
    line = (h["root"] / "tree/PLANNING_STATUS.txt").read_text().strip()
    return dict(kv.split("=", 1) for kv in line.split())


def all_files(path):
    return [p for p in path.rglob("*") if p.is_file()]


def test_sparse_rules_exclude_scorers_only_and_career_and_their_blobs_never_arrive(host):
    done = refresh(host)
    assert done.returncode == 0, done.stderr
    tree = host["root"] / "tree"
    for rel in KEPT:
        assert (tree / rel).is_file(), rel
    for rel in EXCLUDED:
        assert not (tree / rel).exists(), rel
    assert not list(tree.rglob("scorers-only"))
    assert not (tree / ".git").exists()                       # git data is outside the mounted tree
    # Partial clone: the excluded files' contents were never downloaded to the host.
    have = git("--git-dir", str(host["root"] / "git"), "cat-file", "--batch-all-objects", "--batch-check", cwd=host["tmp"])
    for rel in EXCLUDED:
        blob = git("rev-parse", f"HEAD:{rel}", cwd=host["src"])
        assert blob not in have, rel


def test_rules_are_the_published_ones():
    rules = subprocess.run(["/bin/bash", str(SCRIPT), "rules"], capture_output=True, text=True, check=True).stdout
    assert rules.splitlines()[0] == "/*" and "!scorers-only/" in rules.splitlines()


def test_status_file_names_the_commit_and_a_new_planning_commit_is_picked_up(host):
    assert refresh(host).returncode == 0
    s = status(host)
    assert s["status"] == "ok" and s["commit"] == host["sha"] and s["fetched"].endswith("Z")
    assert oct((host["root"] / "tree/PLANNING_STATUS.txt").stat().st_mode & 0o777) == "0o644"
    new = commit(host["src"], {NOW_MD: "this week\n", "operations/new-plan.md": "x\n"}, "two")
    time.sleep(1.1)
    assert refresh(host).returncode == 0
    s2 = status(host)
    assert s2["commit"] == new and s2["fetched"] > s["fetched"]
    assert (host["root"] / "tree" / NOW_MD).read_text() == "this week\n"
    assert (host["root"] / "tree/operations/new-plan.md").is_file()
    # The status file is ignored by git, so the checkout never treats it as a change.
    assert "PLANNING_STATUS" not in git("--git-dir", str(host["root"] / "git"), "--work-tree", str(host["root"] / "tree"),
                                        "status", "--porcelain", cwd=host["tmp"])


def test_a_rules_change_reapplies_without_a_new_commit(host):
    assert refresh(host).returncode == 0
    g = ["--git-dir", str(host["root"] / "git"), "--work-tree", str(host["root"] / "tree")]
    (host["root"] / "git/info/sparse-checkout").write_text("/*\n")
    git(*g, "read-tree", "-mu", "HEAD", cwd=host["tmp"], env=host["env"])
    assert (host["root"] / "tree" / EXCLUDED[0]).is_file()   # an older, wider rule set
    assert refresh(host).returncode == 0
    assert not (host["root"] / "tree" / EXCLUDED[0]).exists()


def test_token_never_in_config_files_command_lines_or_output(host):
    done = refresh(host)
    assert done.returncode == 0, done.stderr
    b64 = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
    for secret in (TOKEN, b64):
        assert secret not in done.stdout + done.stderr
        for f in all_files(host["root"]):
            assert secret.encode() not in f.read_bytes(), f
        assert secret not in (host["tmp"] / "aws.argv").read_text()
    calls = [json.loads(l) for l in host["calls"].read_text().splitlines()]
    assert all(TOKEN not in " ".join(c["argv"]) and b64 not in " ".join(c["argv"]) for c in calls)
    fetches = [c for c in calls if "fetch" in c["argv"]]
    assert fetches and all(c["header"] == f"AUTHORIZATION: basic {b64}" and
                           c["key"] == "http.https://github.com/.extraheader" for c in fetches)
    url = git("--git-dir", str(host["root"] / "git"), "config", "remote.origin.url", cwd=host["tmp"])
    assert url == GH_URL                                      # no credential in the URL
    assert oct((host["root"] / "git").stat().st_mode & 0o777) == "0o700"


def test_no_token_means_skipped_and_the_last_copy_stays(host):
    assert refresh(host).returncode == 0
    (host["tmp"] / "no-token").write_text("")
    commit(host["src"], {NOW_MD: "newer\n"}, "two")
    done = refresh(host)
    assert done.returncode == 1
    s = status(host)
    assert s["status"] == "skipped" and s["reason"] == "token_unavailable" and s["commit"] == host["sha"]
    assert (host["root"] / "tree" / NOW_MD).read_text() == "kept 0\n"


def test_mc_host_reader_finds_nothing_at_a_scorers_only_path(host):
    """The live check is MC's own read on Dev; this is the same reader (scripts/mc_host/inspect.py) on the copy."""
    assert refresh(host).returncode == 0
    spec = importlib.util.spec_from_file_location("mc_inspect", ROOT / "scripts/mc_host/inspect.py")
    inspect = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inspect)
    tree = host["root"] / "tree"

    def read(path, op="read"):
        return inspect.run({"operation": op, "arguments": {"path": str(path)}, "expires_at": time.time() + 20,
                            "denied_paths": [], "forbidden_values": []})

    assert read(tree / NOW_MD)["status"] == "done"
    assert read(tree / EXCLUDED[0]) == {"status": "refused", "code": "host_not_found", "data": {}}
    listing = read(tree / Path(EXCLUDED[0]).parent.parent, op="list")
    assert listing["status"] == "done" and "scorers-only" not in json.dumps(listing["data"]["entries"])


def test_mc_mounts_only_the_files_read_only():
    compose = yaml.safe_load((ROOT / "docker-compose.mc.prod.yml").read_text())
    for name, svc in compose["services"].items():
        vols = [v for v in svc.get("volumes", []) if isinstance(v, str)]
        planning = [v for v in vols if v.startswith("/opt/minimoi/planning")]
        if name == "mc-agent":
            assert planning == ["/opt/minimoi/planning/tree:/minimoi/planning:ro"]
        else:
            assert planning == [], name


def test_shipped_and_installed_by_the_deploy(tmp_path):
    entries = {e["repo"]: e for e in json.loads((ROOT / "config/host_scripts.json").read_text())["entries"]}
    e = entries["scripts/operations/planning_mirror.sh"]
    assert e["ship"] is True and e["host"] == "/opt/minimoi/scripts/planning_mirror.sh" and e["mode"] == "0755"
    spec = importlib.util.spec_from_file_location("parity_pm", ROOT / "scripts/operations/host_script_parity.py")
    parity = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parity)
    assert parity.PLANNING_MIRROR == e["host"]
    assert parity.install_planning_mirror(str(tmp_path / "missing.sh")) == "not_shipped"


def test_install_ec2_writes_the_five_minute_cron(tmp_path):
    env = dict(os.environ, MINIMOI_ETC=str(tmp_path / "etc"))
    subprocess.run(["/bin/bash", str(SCRIPT), "install", "ec2"], env=env, check=True, capture_output=True)
    cron = (tmp_path / "etc/cron.d/minimoi-planning-mirror").read_text()
    assert "*/5 * * * * root /opt/minimoi/scripts/planning_mirror.sh refresh >> /opt/minimoi/logs/planning-mirror.log 2>&1" in cron
    subprocess.run(["/bin/bash", str(SCRIPT), "remove", "ec2"], env=env, check=True, capture_output=True)
    assert not (tmp_path / "etc/cron.d/minimoi-planning-mirror").exists()


def test_install_dev_writes_a_systemd_timer(tmp_path):
    calls = tmp_path / "systemctl.calls"
    fake = tmp_path / "systemctl"
    fake.write_text(f'#!/bin/sh\necho "$*" >> {calls}\n')
    fake.chmod(0o755)
    env = dict(os.environ, MINIMOI_ETC=str(tmp_path / "etc"), SYSTEMCTL=str(fake),
               MINIMOI_PLANNING_SCRIPT_DIR=str(tmp_path / "scripts"))
    subprocess.run(["/bin/bash", str(SCRIPT), "install", "dev"], env=env, check=True, capture_output=True)
    units = tmp_path / "etc/systemd/system"
    service = (units / "minimoi-planning-mirror.service").read_text()
    assert "MINIMOI_PLANNING_ROOT=/opt/minimoi-staging/planning" in service
    assert f"ExecStart={tmp_path}/scripts/planning_mirror.sh refresh" in service
    assert "OnCalendar=*:0/5" in (units / "minimoi-planning-mirror.timer").read_text()
    assert (tmp_path / "scripts/planning_mirror.sh").read_bytes() == SCRIPT.read_bytes()
    assert "enable --now minimoi-planning-mirror.timer" in calls.read_text()

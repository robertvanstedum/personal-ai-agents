"""FF-19 (#537): health on every host. The registry's new fields, the evidence kinds, severity and durations, the
watchdog's health report and alert switch, the daily check for MC, the host installer, and MC's direct health path.
Synthetic files in temp folders; the clock is injected; nothing reaches a network."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.jobs import alert, health, registry, status

REPO = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc)
H, D = timedelta(hours=1), timedelta(days=1)


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


wd = load("watchdog_h", "scripts/jobs/watchdog.py")
dh = load("daily_health_h", "scripts/jobs/daily_health.py")


def job(**kw):
    row = {"id": "j", "name": "Job J", "host": "ec2",
           "schedule": {"text": "daily", "kind": "daily", "at": "04:00", "tz": "UTC"},
           "expected_every_s": 86400, "missed_after_s": 180000, "stuck_after_s": 3600, "status_file": "j.json",
           "owner": "robert", "runbook": "x"}
    row.update(kw)
    return row


def reg(*rows):
    return registry.parse({"schema_version": 1, "jobs": list(rows)})


def put(root, j, *, state="ok", finished=NOW - 2 * H, success="auto"):
    started = finished - timedelta(minutes=1)
    suc = (finished if state in ("ok", "warn") else None) if success == "auto" else success
    status.write_status(root, j.status_file, status.build(
        job_id=j.id, host=j.host, run_id="r1", state=state, started_at=started,
        finished_at=None if state == "running" else finished, exit_code=None if state == "running" else 0,
        summary="x", last_success_at=suc))


# ---- registry ------------------------------------------------------------------------------------------------------

def test_shipped_list_covers_every_host_and_marks_rooms_archive_retired():
    r = registry.load()
    assert set(r.hosts()) >= {"mac", "ec2", "dev"}
    archive = r.get("rooms-archive")
    assert archive.retired and archive not in r.for_host("mac")
    assert r.get("mc-github-awareness").status_root == "mc-observations"
    assert {"jobs-watchdog-ec2", "jobs-watchdog-dev", "jobs-daily-health"} <= {j.id for j in r.jobs}


@pytest.mark.parametrize("evidence", [{"kind": "smoke"}, {"kind": "mtime"}, {"kind": "mtime", "path": "rel/x"},
                                      {"kind": "mtime", "path": "/a/../b"}, {"kind": "none"},
                                      {"kind": "mtime", "path": "/x", "fail_marker": "a\nb"}])
def test_bad_evidence_is_refused(evidence):
    with pytest.raises(registry.RegistryError):
        reg(job(evidence=evidence, status_file=None))


def test_status_file_optional_only_without_status_evidence():
    assert reg(job(status_file=None, evidence={"kind": "none", "why": "w"})).jobs[0].status_file is None
    with pytest.raises(registry.RegistryError):
        reg(job(status_file=None))
    with pytest.raises(registry.RegistryError):
        reg(job(retired="yes"))
    with pytest.raises(registry.RegistryError):
        reg(job(status_root="Bad Root"))


# ---- evidence and severity -----------------------------------------------------------------------------------------

def test_mtime_evidence_on_time_late_and_failed(tmp_path):
    log = tmp_path / "x.log"
    log.write_text("started\nall good\n")
    os.utime(log, ((NOW - 2 * H).timestamp(),) * 2)
    j = reg(job(status_file=None, evidence={"kind": "mtime", "path": str(log), "fail_marker": "ERROR"})).jobs[0]
    assert health.observe(j, {}, NOW)[1].code == "ok"
    os.utime(log, ((NOW - 3 * D).timestamp(),) * 2)
    assert health.observe(j, {}, NOW)[1].code == "missed"
    log.write_text("ERROR: repo not found\nRun: git clone\n")
    os.utime(log, ((NOW - 2 * H).timestamp(),) * 2)
    assert health.observe(j, {}, NOW)[1].code == "failed"
    missing = reg(job(status_file=None, active_from="2026-11-10T00:00:00Z",
                      evidence={"kind": "mtime", "path": str(tmp_path / "nope")})).jobs[0]
    assert health.observe(missing, {}, NOW)[1].code == "never_ran"


def test_none_evidence_is_not_tracked_never_healthy():
    j = reg(job(status_file=None, evidence={"kind": "none", "why": "w"})).jobs[0]
    v = health.observe(j, {}, NOW)[1]
    assert (v.state, v.code) == ("unknown", "not_tracked")


def test_status_root_maps_to_another_folder(tmp_path):
    j = reg(job(status_root="mc-observations")).jobs[0]
    put(tmp_path / "obs", j)
    assert health.observe(j, {"jobs": str(tmp_path / "jobs")}, NOW)[1].code == "not_due"       # not mapped: unknown
    assert health.observe(j, {"mc-observations": str(tmp_path / "obs")}, NOW)[1].code == "ok"


def test_severity_and_duration_words():
    assert health.severity({"code": "failed", "light": "red"}) == health.NEEDS_ACTION
    assert health.severity({"code": "late", "light": "yellow"}) == health.WATCH
    assert health.severity({"code": "report_stale", "light": "unknown"}) == health.WATCH
    assert health.severity({"code": "not_due", "light": "unknown"}) == health.FINE
    assert health.duration(2 * 86400 + 5) == "2 days" and health.duration(86400) == "24 h"
    assert health.duration(5 * 3600) == "5 h" and health.duration(30) == "1 min" and health.duration(None) == ""


def test_a_failing_job_is_off_since_its_last_success(tmp_path):
    """"failing 2 days", not just "failed"."""
    j = reg(job(missed_after_s=86400 * 5)).jobs[0]
    status.write_status(tmp_path, j.status_file, status.build(
        job_id="j", host="ec2", run_id="r2", state="failed", started_at=NOW - H, finished_at=NOW - H, exit_code=1,
        summary="x", last_success_at=NOW - 2 * D - H))
    report = health.host_report(reg(job(missed_after_s=86400 * 5)), "ec2", {"jobs": str(tmp_path)}, NOW)
    items = health.daily_items({"ec2": report}, NOW)
    assert items[0]["severity"] == health.NEEDS_ACTION and items[0]["off_for"] == "2 days"
    assert "not working for 2 days" in health.line(items[0])


def test_a_warning_carries_its_start_forward(tmp_path):
    r = reg(job())
    j = r.jobs[0]
    put(tmp_path, j, state="warn")
    first = health.host_report(r, "ec2", {"jobs": str(tmp_path)}, NOW)
    later = health.host_report(r, "ec2", {"jobs": str(tmp_path)}, NOW + 5 * H, previous=first)
    assert later["items"][0]["off_since"] == status.iso(NOW)
    assert health.daily_items({"ec2": later}, NOW + 5 * H)[0]["off_for"] == "5 h"


def test_missing_or_stale_host_reports_are_items_not_silence(tmp_path):
    r = reg(job())
    report = health.host_report(r, "ec2", {"jobs": str(tmp_path)}, NOW - 2 * D)
    items = health.daily_items({"ec2": report}, NOW, expected_hosts=("dev",))
    codes = {(i["host"], i["code"]) for i in items}
    assert ("ec2", "report_stale") in codes and ("dev", "report_missing") in codes


def test_words_all_green_is_one_line():
    assert health.words([], hosts=["ec2"]) == "All watched jobs are green on ec2."


# ---- watchdog ------------------------------------------------------------------------------------------------------

def registry_file(tmp_path, *rows):
    p = tmp_path / "reg.json"
    p.write_text(json.dumps({"schema_version": 1, "jobs": list(rows)}))
    return p


def test_ec2_watchdog_writes_health_for_mc_and_sends_nothing_with_alerts_off(tmp_path):
    jobs, obs = tmp_path / "jobs", tmp_path / "obs"
    gh = job(id="mc-github-awareness", status_file="mc-github-awareness.json", status_root="mc-observations")
    bad = job(id="broken", status_file="broken.json")
    path = registry_file(tmp_path, gh, bad)
    r = registry.load(path)
    put(obs / "jobs", r.get("mc-github-awareness"))
    put(jobs, r.get("broken"), state="failed", success=NOW - 3 * D)
    sent = []
    code = wd.run(jobs_root=jobs, host="ec2", registry_path=path, sender=lambda t: sent.append(t) or "sent",
                  now=lambda: NOW, out=io.StringIO(), err=io.StringIO(),
                  roots=wd.roots_from_env(jobs, {"MINIMOI_JOBS_ROOT_MC_OBSERVATIONS": str(obs / "jobs")}),
                  alerts=False, health_dir=obs / "health")
    assert code == 0 and sent == []
    own = status.read_status(jobs, "jobs-watchdog-ec2.json", "jobs-watchdog-ec2")
    assert own["results"] == {"mc-github-awareness": "ok", "broken": "failed"}
    shared = obs / "health" / "health-ec2.json"
    assert oct(shared.stat().st_mode & 0o777) == "0o644"
    report = health.read_report(obs / "health", "ec2")
    assert report["alerts"] == "off" and {i["id"]: i["code"] for i in report["items"]} == own["results"]
    assert (jobs / "health-ec2.json").is_file()


def test_watchdog_id_per_host():
    assert wd.watchdog_id("mac") == "jobs-watchdog" and wd.watchdog_id("dev") == "jobs-watchdog-dev"


# ---- daily check ---------------------------------------------------------------------------------------------------

def daily_setup(tmp_path, state="failed"):
    jobs, folder = tmp_path / "jobs", tmp_path / "health"
    path = registry_file(tmp_path, job(id="broken", status_file="broken.json"),
                         job(id="dev-thing", host="dev", status_file="dev-thing.json"))
    r = registry.load(path)
    put(jobs, r.get("broken"), state=state, success=NOW - 2 * D)
    report = health.host_report(r, "ec2", {"jobs": str(jobs)}, NOW)
    report["alerts"] = "off"
    health.publish(folder, "health-ec2.json", report)
    return jobs, folder, path


def run_daily(jobs, folder, path, sender, now=NOW, **kw):
    out = io.StringIO()
    code = dh.run(jobs_root=jobs, health_dir=folder, registry_path=path, sender=sender, now=lambda: now,
                  out=out, err=io.StringIO(), **kw)
    return code, out.getvalue()


def test_daily_check_reports_every_non_green_item_and_tells_telegram_once(tmp_path):
    jobs, folder, path = daily_setup(tmp_path)
    sent = []
    code, text = run_daily(jobs, folder, path, lambda t: sent.append(t) or "sent")
    assert code == 0 and len(sent) == 1 and "needs action" in sent[0] and "2 days" in sent[0]
    daily = json.loads((folder / "health-daily.json").read_text())
    assert daily["not_reporting"] == ["dev"] and daily["items"][0]["severity"] == "needs action"
    assert "Not reporting here: dev" in text
    run_daily(jobs, folder, path, lambda t: sent.append(t) or "sent", now=NOW + H)
    assert len(sent) == 1                                                       # the same condition: not again
    own = status.read_status(jobs, "jobs-daily-health.json", "jobs-daily-health")
    assert own["results"]["needs_action"] == "1"


def test_only_needs_action_reaches_telegram(tmp_path):
    jobs, folder, path = daily_setup(tmp_path, state="warn")
    sent = []
    code, text = run_daily(jobs, folder, path, lambda t: sent.append(t) or "sent")
    assert code == 0 and sent == [] and "[watch]" in text


def test_an_unsent_message_is_retried_and_the_run_warns(tmp_path):
    jobs, folder, path = daily_setup(tmp_path)
    code, _ = run_daily(jobs, folder, path, lambda t: "not_sent")
    assert code == 1
    sent = []
    run_daily(jobs, folder, path, lambda t: sent.append(t) or "sent", now=NOW + H)
    assert len(sent) == 1


def test_a_host_that_alerts_itself_is_not_told_twice(tmp_path):
    jobs, folder, path = daily_setup(tmp_path)
    doc = json.loads((folder / "health-ec2.json").read_text())
    doc["alerts"] = "on"
    (folder / "health-ec2.json").write_text(json.dumps(doc))
    sent = []
    run_daily(jobs, folder, path, lambda t: sent.append(t) or "sent")
    assert sent == []


def test_dev_runs_without_telegram(tmp_path):
    jobs, folder, path = daily_setup(tmp_path)
    sent = []
    code, _ = run_daily(jobs, folder, path, lambda t: sent.append(t) or "sent", notify_on=False, host="dev")
    assert code == 0 and sent == [] and status.read_status(jobs, "jobs-daily-health-dev.json", "jobs-daily-health-dev")


def test_alert_reads_the_token_from_ssm_when_named():
    posted = []
    result = alert.send("hi", env={"MINIMOI_TELEGRAM_SSM_PARAM": "/p", "TELEGRAM_CHAT_ID": "1"},
                        keychain=lambda a: None, ssm=lambda name: "tok" if name == "/p" else None,
                        post=lambda url, payload, t: posted.append(url) or 200)
    assert result == "sent" and posted and "tok" in posted[0]
    assert alert.send("hi", env={"TELEGRAM_CHAT_ID": "1"}, keychain=lambda a: None, ssm=lambda n: "tok") == "not_sent"


# ---- host installer ------------------------------------------------------------------------------------------------

def test_dev_installer_copies_the_tools_and_writes_two_timers(tmp_path):
    calls = tmp_path / "calls"
    fake = tmp_path / "systemctl"
    fake.write_text(f"#!/bin/sh\necho \"$@\" >> {calls}\n")
    fake.chmod(0o755)
    env = dict(os.environ, MINIMOI_ETC=str(tmp_path / "etc"), SYSTEMCTL=str(fake))
    script = (REPO / "scripts/jobs/install_host_watchdog.sh").read_text().replace(
        "tools=/opt/minimoi-staging/jobs-tools", f"tools={tmp_path / 'tools'}")
    copy = tmp_path / "install.sh"
    copy.write_text(script)
    done = subprocess.run(["/bin/sh", str(copy), "dev", "--from", str(REPO)], env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    units = tmp_path / "etc/systemd/system"
    assert "OnCalendar=*-*-* 09:10:00 America/Chicago" in (units / "minimoi-jobs-daily-health.timer").read_text()
    assert "dev watch" in (units / "minimoi-jobs-watchdog.service").read_text()
    assert (tmp_path / "tools/core/jobs/health.py").is_file() and (tmp_path / "tools/config/scheduled_jobs.json").is_file()
    assert "enable --now minimoi-jobs-watchdog.timer minimoi-jobs-daily-health.timer" in calls.read_text()


def test_ec2_installer_needs_the_shipped_tools(tmp_path):
    env = dict(os.environ, MINIMOI_ETC=str(tmp_path / "etc"))
    done = subprocess.run(["/bin/sh", str(REPO / "scripts/jobs/install_host_watchdog.sh"), "ec2"], env=env,
                          capture_output=True, text=True)
    assert done.returncode == 1 and "the deploy ships it" in done.stderr


def test_the_deploy_ships_every_tool_the_installer_copies():
    shipped = {e["repo"] for e in json.loads((REPO / "config/host_scripts.json").read_text())["entries"]
               if e["host"].startswith("/opt/minimoi/jobs-tools/") and e.get("ship")}
    text = (REPO / "scripts/jobs/install_host_watchdog.sh").read_text()
    listed = set(text.split('FILES="', 1)[1].split('"', 1)[0].split())
    assert listed <= shipped and "scripts/jobs/install_host_watchdog.sh" in shipped


def test_parity_install_cron_installs_jobs_health_when_shipped(tmp_path):
    parity = load("parity_h", "scripts/operations/host_script_parity.py")
    assert parity.install_jobs_health(str(tmp_path / "missing.sh")) == "not_shipped"
    ok = tmp_path / "ok.sh"
    ok.write_text("exit 0\n")
    assert parity.install_jobs_health(str(ok)) == "installed"


def test_tools_are_python39_syntax():
    """The hosts run Amazon Linux's python3 (3.9): no newer syntax in what the deploy ships."""
    import ast
    for rel in ("core/jobs/alert.py", "core/jobs/health.py", "core/jobs/judge.py", "core/jobs/registry.py",
                "core/jobs/schedule.py", "core/jobs/status.py", "scripts/jobs/watchdog.py", "scripts/jobs/daily_health.py"):
        ast.parse((REPO / rel).read_text(), rel, feature_version=(3, 9))


# ---- MC's direct health path ---------------------------------------------------------------------------------------

def mc_block(tmp_path, environ, principal="robert"):
    from minimoi_portal.guild_ui import jobs_health
    return jobs_health.block(principal, environ=environ, now=NOW, root=str(tmp_path / "health"),
                             registry_loader=lambda: registry.load(tmp_path / "reg.json"))


def test_mc_block_problems_first_with_durations(tmp_path):
    jobs, folder, path = daily_setup(tmp_path)
    text = mc_block(tmp_path, {"MINIMOI_SUPERVISION_OWNER": "robert"})
    assert text.startswith("[MiniMoi job health") and "[needs action] Job J (ec2)" in text
    assert "not working for 2 days" in text and "No report here from: dev" in text and len(text) <= 2400


def test_mc_block_owner_only_and_switchable(tmp_path):
    daily_setup(tmp_path)
    assert mc_block(tmp_path, {"MINIMOI_SUPERVISION_OWNER": "robert"}, principal="guest") == ""
    assert mc_block(tmp_path, {}) == ""
    assert mc_block(tmp_path, {"MINIMOI_SUPERVISION_OWNER": "robert", "MINIMOI_MC_JOBS_HEALTH": "0"}) == ""


def test_mc_block_never_fails_the_turn(tmp_path):
    from minimoi_portal.guild_ui import jobs_health

    def broken():
        raise RuntimeError("x")
    text = jobs_health.block("robert", environ={"MINIMOI_SUPERVISION_OWNER": "robert"}, registry_loader=broken)
    assert "unverified" in text


def test_mc_turn_context_carries_the_block():
    src = (REPO / "minimoi_portal/guild_ui/api.py").read_text()
    assert "jobs_health.block(principal, environ=feature_environ())" in src


# ---- across hosts: push, pull, silence (FF-19 follow-up) -----------------------------------------------------------

pull_mod = load("pull_health_h", "scripts/jobs/pull_health.py")


class FakeDest:
    puts = []

    def __init__(self, url):
        self.url = url

    def put(self, local, key, replace=False):
        FakeDest.puts.append((self.url, key, replace, json.loads(Path(local).read_text())["host"]))


def test_push_uses_the_pinned_vault_principal_when_present(tmp_path):
    FakeDest.puts = []
    report = tmp_path / "health-mac.json"
    report.write_text(json.dumps({"host": "mac"}))
    assert health.push(report, "s3://minimoi-backups/vault", "mac", dest_factory=FakeDest) == "ok"
    assert FakeDest.puts == [("s3://minimoi-backups/vault", "health/mac/health-mac.json", True, "mac")]

    class Broken(FakeDest):
        def put(self, *a, **k):
            raise RuntimeError("denied")
    assert health.push(report, "s3://b/v", "mac", dest_factory=Broken) == "failed"
    assert health.push(report, "http://elsewhere", "mac", dest_factory=FakeDest) == "failed"


def test_push_on_a_linux_host_uses_the_cli_with_sse(tmp_path):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)
    report = tmp_path / "health-dev.json"
    report.write_text("{}")
    # core.memory_backup is importable in this checkout, so hide it to take the Linux hosts' CLI path:
    import builtins
    real = builtins.__import__

    def no_backup(name, *a, **k):
        if name == "core.memory_backup":
            raise ImportError(name)
        return real(name, *a, **k)
    builtins.__import__ = no_backup
    try:
        calls.clear()
        assert health.push(report, "s3://minimoi-backups/vault", "dev", run=run) == "ok"
    finally:
        builtins.__import__ = real
    assert calls[0][:3] == ["aws", "s3", "cp"] and "s3://minimoi-backups/vault/health/dev/health-dev.json" in calls[0]
    assert "--sse" in calls[0] and "AES256" in calls[0]


def test_watchdog_push_failure_warns_but_still_reports(tmp_path):
    jobs = tmp_path / "jobs"
    path = registry_file(tmp_path, job(id="a", host="dev", status_file="a.json"))
    put(jobs, registry.load(path).get("a"))
    original = health.push
    health.push = lambda *a, **k: "failed"
    try:
        code = wd.run(jobs_root=jobs, host="dev", registry_path=path, sender=lambda t: "sent", now=lambda: NOW,
                      out=io.StringIO(), err=io.StringIO(), alerts=False, push_dest="s3://b/v")
    finally:
        health.push = original
    own = status.read_status(jobs, "jobs-watchdog-dev.json", "jobs-watchdog-dev")
    assert code == 1 and own["state"] == "warn" and own["results"]["health-push"] == "failed"
    assert own["summary"] == "1 jobs checked, report not sent to production"
    assert (jobs / "health-dev.json").is_file()


def report_for(host, checked):
    return {"schema_version": 1, "host": host, "checked_at": status.iso(checked), "worst": "green",
            "counts": {}, "items": []}


def test_pull_places_valid_reports_and_refuses_the_rest(tmp_path):
    folder = tmp_path / "health"
    remote = {"health/mac/health-mac.json": report_for("mac", NOW - H),
              "health/dev/health-dev.json": report_for("ec2", NOW)}           # claims another host: refused

    def fetch(key, local):
        if key not in remote:
            return False
        Path(local).write_text(json.dumps(remote[key]))
        return True
    path = registry_file(tmp_path, job(id="m", host="mac", status_file="m.json"), job(id="d", host="dev", status_file="d.json"),
                         job(id="e", status_file="e.json"), job(id="g", host="github", status_file=None,
                                                                evidence={"kind": "none", "why": "w"}))
    out = pull_mod.run(dest="s3://b/v", health_dir=folder, this_host="ec2", registry_path=path, now=NOW, fetch=fetch)
    assert out == {"mac": "ok", "dev": "invalid"}
    assert health.read_report(folder, "mac")["checked_at"] == status.iso(NOW - H)
    remote["health/mac/health-mac.json"] = report_for("mac", NOW + D)                                  # from the future
    assert health.pull("s3://b/v", "mac", folder, NOW, fetch=fetch) == "invalid"
    assert health.pull("s3://b/v", "nope", folder, NOW, fetch=fetch) == "missing"
    assert health.read_report(folder, "mac")["checked_at"] == status.iso(NOW - H)                      # the old copy stays


def test_a_silent_host_reads_no_report_for_n_hours_and_turns_needs_action_after_three_days():
    mac = report_for("mac", NOW - 40 * H)
    dev = report_for("dev", NOW - 5 * H)
    items = {i["host"]: i for i in health.daily_items({"mac": mac, "dev": dev, "ec2": report_for("ec2", NOW)}, NOW)}
    assert items["mac"]["code"] == "report_stale" and items["mac"]["severity"] == "watch"
    assert health.line(items["mac"]) == "[watch] Watchdog report from mac (mac): no new watchdog report, for 40 h"
    assert items["dev"]["severity"] == "watch" and "ec2" not in items          # hourly hosts: stale after 3 h
    gone = health.daily_items({"mac": report_for("mac", NOW - 4 * D)}, NOW)[0]
    assert gone["severity"] == "needs action" and gone["off_for"] == "4 days"
    assert health.daily_items({"mac": report_for("mac", NOW - 20 * H)}, NOW) == []                     # daily Mac: fine


def test_mc_sees_the_macs_pulled_report(tmp_path):
    folder = tmp_path / "health"
    health.publish(folder, "health-mac.json", {**report_for("mac", NOW - 2 * H), "items": [
        {"id": "private-repo-sync", "name": "Private repo sync", "host": "mac", "light": "red", "code": "failed",
         "off_since": status.iso(NOW - 3 * D)}]})
    registry_file(tmp_path, job(id="m", host="mac", status_file="m.json"))
    text = mc_block(tmp_path, {"MINIMOI_SUPERVISION_OWNER": "robert"})
    assert "[needs action] Private repo sync (mac): last run failed, not working for 3 days" in text


def test_the_mac_runtime_pushes_its_report():
    src = (REPO / "scripts/vault/runtime_install.py").read_text()
    assert "'MINIMOI_JOBS_HEALTH_PUSH': cfg['backup_dest']" in src

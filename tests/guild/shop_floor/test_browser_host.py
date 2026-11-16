"""The browser sweep stops only this runner's leftover Chrome."""
import json

import browser_host


def test_sweep_stops_orphans_and_recorded_pids_and_leaves_a_live_browser(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_host, "RECORD", tmp_path / "browser-runner.json")
    monkeypatch.setattr(browser_host, "RESULT", tmp_path / "browser-result.json")
    procs = [
        {"pid": 50, "ppid": 1, "cmd": "python pytest"},
        {"pid": 10, "ppid": 1, "cmd": "Google Chrome --disable-field-trial-config --headless"},
        {"pid": 11, "ppid": 50, "cmd": "Google Chrome --disable-field-trial-config --headless"},
        {"pid": 12, "ppid": 1, "cmd": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"},
        {"pid": 13, "ppid": 1, "cmd": "Google Chrome --disable-field-trial-config --headless"},
    ]
    monkeypatch.setattr(browser_host, "list_processes", lambda: procs)
    killed = []
    monkeypatch.setattr(browser_host, "stop_pids", lambda pids: killed.extend(pids) or list(pids))
    browser_host.RECORD.write_text(json.dumps({"parent": 99, "pids": [13, 12, 11]}))

    note = browser_host.prepare(lambda: (_ for _ in ()).throw(TimeoutError("slow")))

    assert note["status"] == "blocked"
    assert note["reason"] == "blocked: host cannot launch a browser promptly"
    assert sorted(killed) == [10, 13]
    saved = json.loads(browser_host.RESULT.read_text())
    assert saved["status"] == "blocked"
    assert "load" in saved["host"]
    assert saved["host"]["canary_seconds"] >= 0


def test_a_recorded_browser_stays_while_its_run_is_alive():
    procs = [
        {"pid": 50, "ppid": 1, "cmd": "python pytest"},
        {"pid": 11, "ppid": 50, "cmd": "Google Chrome --disable-field-trial-config --headless"},
    ]
    assert browser_host.pids_to_stop({"parent": 50, "pids": [11]}, procs) == []


def test_a_finished_run_records_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_host, "RECORD", tmp_path / "browser-runner.json")
    monkeypatch.setattr(browser_host, "RESULT", tmp_path / "browser-result.json")
    monkeypatch.setattr(browser_host, "list_processes", lambda: [])
    monkeypatch.setattr(browser_host, "stop_pids", lambda pids: [])
    note = browser_host.prepare(lambda: None)
    assert note["status"] == "ready"
    browser_host.finish_session(0)
    assert json.loads(browser_host.RESULT.read_text())["status"] == "pass"
    browser_host.finish_session(1)
    assert json.loads(browser_host.RESULT.read_text())["status"] == "fail"

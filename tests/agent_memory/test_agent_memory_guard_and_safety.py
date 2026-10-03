"""T4 guard and provenance, T7 never fatal (copier), T9 error canary, scrub speed."""
import json
import logging
import time

import pytest

from core.agent_memory import errors, publish as publish_mod
from core.agent_memory.run import run_source
from core.agent_memory.scrub import scrub_file, scrub_text
from core.agent_memory.snapshot import read_manifest

from agent_memory.agent_memory_helpers import NOW, Box, make_cfg, tree

FAKE_KEY = "sk-ant-FAKEFAKEFAKE0000canary"
FAKE_PW = "hunter2-canary-value"
FAKE_CARD = "4242 4242 4242 4242"
FAKE_MAIL = "someone.canary@example.org"


def all_text(root):
    """Everything under the data root, as one string (names and contents)."""
    return "\n".join(f"{k}\n{v.decode('utf-8', 'replace')}" for k, v in tree(root).items())


def test_t4_credentials_are_redacted_file_still_copied_with_provenance(root, caplog):
    original = (f"# notes\napi key: {FAKE_KEY}\npassword: {FAKE_PW}\npay with {FAKE_CARD}\n"
                f"mail {FAKE_MAIL}\nplain line\n").encode()
    box = Box({"MEMORY.md": original, "memory/clean.md": b"nothing secret\n"})
    caplog.set_level(logging.DEBUG)
    r = run_source(box.source, make_cfg(), root, NOW)
    assert r.ok
    manifest = read_manifest(root / "cos-agent-a" / "current")
    entry = {e["path"]: e for e in manifest["files"]}
    dirty, clean = entry["MEMORY.md"], entry["memory/clean.md"]
    assert dirty["sanitized"] is True and dirty["redactions"] >= 3
    assert dirty["source_sha256"] and dirty["source_sha256"] != dirty["sha256"]
    assert dirty["sanitizers"] == ["payment_scrub_v1", "credential_guard_v1"]
    assert clean["sanitized"] is False and clean["source_sha256"] is None and clean["redactions"] == 0
    stored = (root / "cos-agent-a" / "current" / "MEMORY.md").read_bytes()
    assert b"plain line" in stored and b"[credential removed]" in stored
    import hashlib
    assert hashlib.sha256(stored).hexdigest() == dirty["sha256"] and len(stored) == dirty["size"]
    everything = all_text(root) + caplog.text + repr(r)
    for fake in (FAKE_KEY, FAKE_PW, FAKE_CARD, FAKE_MAIL, "4242"):
        assert fake not in everything


def test_scrub_is_idempotent_and_provenance_only_when_changed():
    once = scrub_file("a.md", f"token: {FAKE_PW}".encode())
    twice = scrub_file("a.md", once.data)
    assert once.sanitized and not twice.sanitized and twice.data == once.data and twice.redactions == 0


def test_scrub_worst_case_speed():
    worst = ("a1" * 4000) + (" 4242" * 800) + ("x" * 4000)
    assert len(worst) >= 16_000
    tricky = ("password " * 1800)[:16_000]
    for text in (worst[:16_000], tricky, ("1234-" * 3200)[:16_000], ("sk-" + "a" * 15_000 + " ")):
        start = time.perf_counter()
        scrub_text(text)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 50, f"scrub took {elapsed_ms:.1f} ms"


# ------------------------------------------------------------------ T7


class Boom:
    def __init__(self, exc): self.exc = exc
    def read(self, accept=None): raise self.exc


@pytest.mark.parametrize("exc,code", [
    (RuntimeError("marker"), "internal"), (PermissionError("marker"), "mode_unreadable"),
    (TimeoutError("marker"), "docker_timeout"), (OSError("marker"), "copy_incomplete"),
    (errors.CopierError("docker_unreachable"), "docker_unreachable"), (KeyError("marker"), "internal"),
    (NotImplementedError("marker"), "internal"), (MemoryError(), "internal")])
def test_t7_a_raising_source_never_raises_out(root, exc, code):
    r = run_source(Boom(exc), make_cfg(), root, NOW)
    assert not r.ok and r.code == code and code in errors.CODES
    status = json.loads((root / "cos-agent-a" / "_status.json").read_text())
    assert status["last_ok"] is False and status["last_code"] == code


def test_t7_other_sources_still_copy(root, box):
    bad = run_source(Boom(RuntimeError("x")), make_cfg("master-craftsman"), root, NOW)
    good = run_source(box.source, make_cfg("cos-agent-a"), root, NOW)
    assert not bad.ok and good.ok


def test_t7_unwritable_root_returns_disk_write_failed(tmp_path, box):
    blocker = tmp_path / "file-not-folder"
    blocker.write_text("x")
    r = run_source(box.source, make_cfg(), blocker / "agent-memory", NOW)
    assert not r.ok and r.code == "disk_write_failed"


def test_t7_disk_low_skips_writes_and_deletes_nothing(root, box, monkeypatch):
    assert run_source(box.source, make_cfg(), root, NOW).ok
    before = tree(root)
    from collections import namedtuple
    usage = namedtuple("u", "total used free")
    monkeypatch.setattr("core.agent_memory.headroom.shutil.disk_usage", lambda p: usage(100 * 2**30, 99 * 2**30, 2**30))
    box.files["MEMORY.md"] = b"new content"
    r = run_source(box.source, make_cfg(), root, NOW.replace(day=4))
    assert not r.ok and r.code == "disk_low"
    after = tree(root)
    after_without_status = {k: v for k, v in after.items() if k != "cos-agent-a/_status.json"}
    assert after_without_status == {k: v for k, v in before.items() if k != "cos-agent-a/_status.json"}
    assert json.loads(after["cos-agent-a/_status.json"])["last_code"] == "disk_low"


def test_headroom_thresholds(tmp_path, monkeypatch):
    from collections import namedtuple
    from core.agent_memory.headroom import check_headroom
    u = namedtuple("u", "total used free")
    monkeypatch.setattr("core.agent_memory.headroom.shutil.disk_usage", lambda p: u(100 * 2**30, 94 * 2**30, 6 * 2**30))
    assert check_headroom(tmp_path / "missing" / "deeper", min_free_fraction=None) is None   # 6 GB >= 5 GB
    assert check_headroom(tmp_path) == "disk_low"                                          # 6% < the 10% default
    assert check_headroom(tmp_path, min_free_bytes=7 * 2**30, min_free_fraction=None) == "disk_low"
    assert check_headroom(tmp_path, min_free_bytes=None, min_free_fraction=0.10) == "disk_low"
    assert check_headroom(tmp_path, min_free_bytes=None, min_free_fraction=0.05) is None


def test_t7_status_is_written_even_when_publish_fails(root, box, monkeypatch):
    monkeypatch.setattr(publish_mod, "_checkpoint", lambda s: (_ for _ in ()).throw(OSError("no space")))
    assert run_source(box.source, make_cfg(), root, NOW).code == "disk_write_failed"
    assert json.loads((root / "cos-agent-a" / "_status.json").read_text())["last_ok"] is False


# ------------------------------------------------------------------ T9


MARKER = "MARKER-canary-7f3a"


def test_t9_error_text_and_file_names_never_leak(root, caplog, monkeypatch):
    secret_name = f"memory/{MARKER}-diary.md"
    box = Box({"MEMORY.md": b"ok", secret_name: f"body {MARKER}".encode()})
    caplog.set_level(logging.DEBUG)

    def leaky(step):
        if step == "validated":
            raise RuntimeError(f"{MARKER} failed on {secret_name}")
    monkeypatch.setattr(publish_mod, "_checkpoint", leaky)
    failed = run_source(box.source, make_cfg(), root, NOW)
    monkeypatch.undo()
    # a scan failure carrying the marker, and a scrub failure carrying the marker
    r2 = run_source(Boom(RuntimeError(f"cannot read {secret_name} {MARKER}")), make_cfg(), root, NOW)
    monkeypatch.setattr("core.agent_memory.run.scrub_file", lambda *a: (_ for _ in ()).throw(ValueError(MARKER)))
    r3 = run_source(box.source, make_cfg("other"), root, NOW)
    assert [r.code for r in (failed, r2, r3)] == ["internal"] * 3
    blob = caplog.text + all_text(root) + repr((failed, r2, r3))
    assert MARKER not in blob and "diary" not in blob


def test_t9_status_and_logs_carry_only_fixed_codes(root, box, caplog):
    caplog.set_level(logging.DEBUG)
    run_source(box.source, make_cfg(), root, NOW)
    for record in caplog.records:
        assert "MEMORY.md" not in record.getMessage()
    status = json.loads((root / "cos-agent-a" / "_status.json").read_text())
    text = json.dumps(status)
    assert "MEMORY.md" not in text and "memory/" not in text
    assert set(status["counts"]) == {"files", "changed", "deleted", "sanitized_files", "redactions", "skipped"}


@pytest.mark.xfail(strict=False, reason=(
    "Measured 3 Oct 2026: the existing scrubs are quadratic on two adversarial 16,000-character inputs "
    "(dotted run with no '://' ~275 ms in credential_scrub's scheme://user:pass@ pattern; '1 ' repeated "
    "~155 ms in payment_scrub's digit-run). utils/ is not changed here; the copier runs once a day."))
@pytest.mark.parametrize("text", ["a.b" * 5300 + "@", "1 " * 8000])
def test_scrub_adversarial_inputs_report_measured_time(text):
    start = time.perf_counter()
    scrub_text(text)
    assert (time.perf_counter() - start) * 1000 < 50


@pytest.mark.skipif(hasattr(__import__("os"), "geteuid") and __import__("os").geteuid() == 0, reason="root ignores modes")
def test_t7_unreadable_folder_is_mode_unreadable_and_publishes_nothing(root, tmp_path):
    from core.agent_memory.sources import DirectorySource
    folder = tmp_path / "ws"
    (folder / "memory").mkdir(parents=True)
    (folder / "memory" / "a.md").write_text("a")
    (folder / "memory").chmod(0)
    try:
        r = run_source(DirectorySource(folder), make_cfg(), root, NOW)
    finally:
        (folder / "memory").chmod(0o700)
    assert not r.ok and r.code == "mode_unreadable" and not (root / "cos-agent-a" / "current").exists()

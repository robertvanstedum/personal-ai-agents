import builtins
from pathlib import Path

from scripts.tools.tour_capture.runner import CaptureRunner, _retain_last_capture_diagnostic
from scripts.tools.tour_capture.scenario import DEVICE_PROFILES


def test_postprocessing_failure_retains_last_raw_capture(tmp_path):
    diagnostics = tmp_path / "diagnostics"
    diagnostics.mkdir()
    raw = tmp_path / "raw.png"
    raw.write_bytes(b"raw capture")

    artifacts = _retain_last_capture_diagnostic(diagnostics, [(1, {}, raw)])

    assert artifacts == ["last-captured.png"]
    assert (diagnostics / "last-captured.png").read_bytes() == b"raw capture"


class _FakePage:
    def __init__(
        self,
        name,
        *,
        url="https://dev.minimoi.ai/guild",
        interaction_at=0,
        repeat_identical=False,
        visibility="hidden",
    ):
        self.name = name
        self.url = url
        self.interaction_at = interaction_at
        self.repeat_identical = repeat_identical
        self.visibility = visibility
        self.closed = False
        self.screenshot_calls: list[str] = []
        self.brought_to_front_before_last_screenshot = False
        self._front = False

    def _check_alive(self):
        if self.closed:
            raise RuntimeError("Target page, context or browser has been closed")

    def title(self):
        self._check_alive()
        return f"title:{self.name}"

    def bring_to_front(self):
        self._check_alive()
        self._front = True

    def wait_for_timeout(self, _ms):
        self._check_alive()

    def evaluate(self, expression):
        self._check_alive()
        if "document.visibilityState" in expression:
            return self.visibility
        if "__minimoiTourCaptureLastInteraction || 0" in expression:
            return self.interaction_at
        return None

    def screenshot(self, *, path, **kwargs):
        self._check_alive()
        self.brought_to_front_before_last_screenshot = self._front
        self.screenshot_calls.append(path)
        suffix = 1 if self.repeat_identical else len(self.screenshot_calls)
        Path(path).write_bytes(f"fake png:{self.name}:{suffix}".encode())


class _FakeContext:
    def __init__(self, pages):
        self.pages = pages


def _make_runner(tmp_path):
    return CaptureRunner(
        scenario={"id": "guild-desktop", "domain": "guild", "device_profile": "desktop"},
        base_url="https://dev.minimoi.ai",
        output_root=tmp_path,
    )


def test_free_capture_loop_captures_single_open_tab_per_enter(tmp_path, monkeypatch):
    runner = _make_runner(tmp_path)
    page = _FakePage("original", visibility="visible")
    context = _FakeContext([page])
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    captured_specs = []

    responses = iter(["", "caption", "done"])
    monkeypatch.setattr(builtins, "input", lambda *_args: next(responses))

    final_order = runner._run_free_capture_loop(
        context,
        {"prefix": "explore", "instructions": "Browse naturally."},
        2,
        DEVICE_PROFILES["desktop"],
        raw_dir,
        captured_specs,
    )

    assert final_order == 4
    assert len(page.screenshot_calls) == 2
    assert [spec[1]["title"] for spec in captured_specs] == [
        "Captured moment 1",
        "caption",
    ]


def test_free_capture_loop_captures_only_last_interacted_tab(tmp_path, monkeypatch):
    runner = _make_runner(tmp_path)
    older = _FakePage("older", interaction_at=10)
    selected = _FakePage("selected", interaction_at=30)
    other = _FakePage("other", interaction_at=20)
    context = _FakeContext([older, selected, other])
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    captured_specs = []

    responses = iter(["current", "done"])
    monkeypatch.setattr(builtins, "input", lambda *_args: next(responses))
    final_order = runner._run_free_capture_loop(
        context,
        {"prefix": "explore", "instructions": "Browse naturally."},
        2,
        DEVICE_PROFILES["desktop"],
        raw_dir,
        captured_specs,
    )

    assert final_order == 3
    assert older.screenshot_calls == []
    assert len(selected.screenshot_calls) == 1
    assert other.screenshot_calls == []


def test_free_capture_prefers_visible_new_tab(tmp_path, monkeypatch):
    runner = _make_runner(tmp_path)
    original = _FakePage("original", interaction_at=100, visibility="hidden")
    new_tab = _FakePage("new", interaction_at=10, visibility="visible")
    context = _FakeContext([original, new_tab])
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    captured_specs = []

    responses = iter(["", "done"])
    monkeypatch.setattr(builtins, "input", lambda *_args: next(responses))
    runner._run_free_capture_loop(
        context,
        {"prefix": "explore", "instructions": "Browse naturally."},
        0,
        DEVICE_PROFILES["desktop"],
        raw_dir,
        captured_specs,
        preferred_page=original,
    )

    assert original.screenshot_calls == []
    assert len(new_tab.screenshot_calls) == 1


def test_free_capture_skips_identical_capture(tmp_path, monkeypatch):
    runner = _make_runner(tmp_path)
    page = _FakePage("same", repeat_identical=True, visibility="visible")
    context = _FakeContext([page])
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    captured_specs = []

    responses = iter(["", "", "done"])
    monkeypatch.setattr(builtins, "input", lambda *_args: next(responses))
    final_order = runner._run_free_capture_loop(
        context,
        {"prefix": "explore", "instructions": "Browse naturally."},
        2,
        DEVICE_PROFILES["desktop"],
        raw_dir,
        captured_specs,
    )

    assert final_order == 3
    assert len(page.screenshot_calls) == 2
    assert len(captured_specs) == 1
    assert not (raw_dir / ".pending-capture.png").exists()


def test_free_capture_ignores_closed_tab(tmp_path, monkeypatch):
    runner = _make_runner(tmp_path)
    open_tab = _FakePage("open", visibility="visible")
    closed_tab = _FakePage("closed")
    closed_tab.closed = True
    context = _FakeContext([open_tab, closed_tab])
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    captured_specs = []

    responses = iter(["", "done"])
    monkeypatch.setattr(builtins, "input", lambda *_args: next(responses))
    runner._run_free_capture_loop(
        context,
        {"prefix": "explore", "instructions": "Browse naturally."},
        0,
        DEVICE_PROFILES["desktop"],
        raw_dir,
        captured_specs,
    )

    assert len(open_tab.screenshot_calls) == 1
    assert closed_tab.screenshot_calls == []

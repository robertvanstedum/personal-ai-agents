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
    def __init__(self, name, *, url="https://dev.minimoi.ai/guild"):
        self.name = name
        self.url = url
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

    def screenshot(self, *, path, **kwargs):
        self._check_alive()
        self.brought_to_front_before_last_screenshot = self._front
        self.screenshot_calls.append(path)
        Path(path).write_bytes(b"fake png")


class _FakeContext:
    def __init__(self, pages):
        self.pages = pages


def _make_runner(tmp_path):
    return CaptureRunner(
        scenario={"id": "guild-baseline", "domain": "guild", "device_profile": "mobile"},
        base_url="https://dev.minimoi.ai",
        output_root=tmp_path,
    )


def test_free_capture_loop_captures_the_single_open_tab_per_enter(tmp_path, monkeypatch):
    runner = _make_runner(tmp_path)
    context = _FakeContext([_FakePage("original")])
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    captured_specs: list = []
    profile = DEVICE_PROFILES["mobile"]

    responses = iter(["", "a caption", "done"])
    monkeypatch.setattr(builtins, "input", lambda *_args: next(responses))

    final_order = runner._run_free_capture_loop(
        context,
        {"prefix": "explore", "instructions": "Browse naturally."},
        order=2,
        profile=profile,
        raw_dir=raw_dir,
        captured_specs=captured_specs,
    )

    assert final_order == 4
    assert len(context.pages[0].screenshot_calls) == 2
    assert [spec[0] for spec in captured_specs] == [3, 4]
    assert captured_specs[0][1]["title"] == "Captured moment 1"
    assert captured_specs[1][1]["title"] == "a caption"


def test_free_capture_loop_captures_every_open_tab_in_one_enter(tmp_path, monkeypatch):
    """The core fix: open as many tabs as you want (Build Log, a spec,
    GitHub), press Enter once, and every one of them gets captured — no
    guessing which single tab is "the active one" required."""
    runner = _make_runner(tmp_path)
    build_log = _FakePage("build-log", url="https://dev.minimoi.ai/guild/build")
    spec_tab = _FakePage("spec", url="https://dev.minimoi.ai/guild/build/spec/x.md")
    github_tab = _FakePage("github", url="https://github.com/robertvanstedum/repo/issues")
    context = _FakeContext([build_log, spec_tab, github_tab])
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    captured_specs: list = []
    profile = DEVICE_PROFILES["mobile"]

    responses = iter(["all three tabs", "done"])
    monkeypatch.setattr(builtins, "input", lambda *_args: next(responses))

    final_order = runner._run_free_capture_loop(
        context,
        {"prefix": "explore", "instructions": "Browse naturally."},
        order=2,
        profile=profile,
        raw_dir=raw_dir,
        captured_specs=captured_specs,
    )

    assert final_order == 5
    assert len(build_log.screenshot_calls) == 1
    assert len(spec_tab.screenshot_calls) == 1
    assert len(github_tab.screenshot_calls) == 1
    assert len(captured_specs) == 3
    # Each tab must be brought to front before its own screenshot — a
    # background tab that was never activated doesn't get painted by
    # Chromium's compositor, so this is required for a correct capture,
    # not just a nice-to-have.
    assert build_log.brought_to_front_before_last_screenshot is True
    assert spec_tab.brought_to_front_before_last_screenshot is True
    assert github_tab.brought_to_front_before_last_screenshot is True
    assert all(spec[1]["title"] == "all three tabs" for spec in captured_specs)


def test_free_capture_loop_supports_multiple_rounds_with_different_tab_counts(
    tmp_path, monkeypatch
):
    """Round 1: just the original tab. The operator then opens a second tab.
    Round 2: both tabs get captured."""
    runner = _make_runner(tmp_path)
    original_tab = _FakePage("original")
    context = _FakeContext([original_tab])
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    captured_specs: list = []
    profile = DEVICE_PROFILES["mobile"]

    responses = iter(["round one", "open-second-tab", "done"])

    def fake_input(*_args):
        value = next(responses)
        if value == "open-second-tab":
            context.pages.append(_FakePage("second"))
            return "round two"
        return value

    monkeypatch.setattr(builtins, "input", fake_input)

    runner._run_free_capture_loop(
        context,
        {"prefix": "explore", "instructions": "Browse naturally."},
        order=2,
        profile=profile,
        raw_dir=raw_dir,
        captured_specs=captured_specs,
    )

    assert len(original_tab.screenshot_calls) == 2  # captured both rounds
    second_tab = context.pages[1]
    assert len(second_tab.screenshot_calls) == 1  # only existed for round 2
    assert len(captured_specs) == 3


def test_free_capture_loop_recovers_from_a_closed_tab_instead_of_crashing(
    tmp_path, monkeypatch
):
    """Closing a tab used to crash the whole run with 'Target page, context
    or browser has been closed'. A closed tab should just be skipped."""
    runner = _make_runner(tmp_path)
    original_tab = _FakePage("original")
    closing_tab = _FakePage("closing")
    closing_tab.closed = True  # already closed before the loop looks at it
    context = _FakeContext([original_tab, closing_tab])
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    captured_specs: list = []
    profile = DEVICE_PROFILES["mobile"]

    responses = iter(["", "done"])
    monkeypatch.setattr(builtins, "input", lambda *_args: next(responses))

    runner._run_free_capture_loop(
        context,
        {"prefix": "explore", "instructions": "Browse naturally."},
        order=2,
        profile=profile,
        raw_dir=raw_dir,
        captured_specs=captured_specs,
    )

    assert len(original_tab.screenshot_calls) == 1
    assert len(closing_tab.screenshot_calls) == 0


def test_free_capture_loop_skips_a_tab_that_closes_mid_round_without_crashing(
    tmp_path, monkeypatch
):
    """A tab still open when the round starts but closed by the time its
    own screenshot is attempted should be skipped, not crash the round."""
    runner = _make_runner(tmp_path)
    original_tab = _FakePage("original")
    flaky_tab = _FakePage("flaky")
    context = _FakeContext([original_tab, flaky_tab])
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    captured_specs: list = []
    profile = DEVICE_PROFILES["mobile"]

    def flaky_screenshot(*, path, **kwargs):
        raise RuntimeError("Target page, context or browser has been closed")

    flaky_tab.screenshot = flaky_screenshot

    responses = iter(["go", "done"])
    monkeypatch.setattr(builtins, "input", lambda *_args: next(responses))

    final_order = runner._run_free_capture_loop(
        context,
        {"prefix": "explore", "instructions": "Browse naturally."},
        order=2,
        profile=profile,
        raw_dir=raw_dir,
        captured_specs=captured_specs,
    )

    assert len(original_tab.screenshot_calls) == 1
    assert len(captured_specs) == 1
    assert final_order == 3

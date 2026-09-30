import builtins
from pathlib import Path

from scripts.tools.tour_capture.runner import (
    CAPTURE_STYLE,
    CLEAN_WEB_STYLE,
    CaptureRunner,
    _clean_web_route_patterns,
    _retain_last_capture_diagnostic,
)
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
        focused=False,
    ):
        self.name = name
        self.url = url
        self.interaction_at = interaction_at
        self.repeat_identical = repeat_identical
        self.visibility = visibility
        self.focused = focused
        self.closed = False
        self.screenshot_calls: list[str] = []
        self.brought_to_front_before_last_screenshot = False
        self.last_screenshot_kwargs = {}
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
        if "document.hasFocus()" in expression:
            return self.focused
        if "__minimoiTourCaptureLastInteraction || 0" in expression:
            return self.interaction_at
        return None

    def screenshot(self, *, path, **kwargs):
        self._check_alive()
        self.brought_to_front_before_last_screenshot = self._front
        self.screenshot_calls.append(path)
        self.last_screenshot_kwargs = kwargs
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

    responses = iter(["current", "2", "done"])
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

    responses = iter(["", "2", "done"])
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


def test_free_capture_prefers_focused_popup_over_original_tab(tmp_path, monkeypatch):
    runner = _make_runner(tmp_path)
    original = _FakePage(
        "original",
        interaction_at=100,
        visibility="visible",
        focused=False,
    )
    popup = _FakePage(
        "source",
        url="https://rollingstone.com/article",
        interaction_at=10,
        visibility="visible",
        focused=True,
    )
    context = _FakeContext([original, popup])
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    captured_specs = []

    responses = iter(["", "2", "done"])
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
    assert len(popup.screenshot_calls) == 1


def test_free_capture_can_override_wrong_tab_suggestion(tmp_path, monkeypatch):
    runner = _make_runner(tmp_path)
    internal = _FakePage(
        "Leitura",
        interaction_at=100,
        visibility="visible",
    )
    external = _FakePage(
        "Rolling Stone",
        url="https://rollingstone.com/article",
        interaction_at=10,
        visibility="visible",
    )
    context = _FakeContext([internal, external])
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    captured_specs = []

    responses = iter(["", "2", "done"])
    monkeypatch.setattr(builtins, "input", lambda *_args: next(responses))
    runner._run_free_capture_loop(
        context,
        {"prefix": "explore", "instructions": "Browse naturally."},
        0,
        DEVICE_PROFILES["desktop"],
        raw_dir,
        captured_specs,
        preferred_page=internal,
    )

    assert internal.screenshot_calls == []
    assert len(external.screenshot_calls) == 1


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


def test_clean_web_request_patterns_only_target_ad_hosts():
    patterns = _clean_web_route_patterns()

    assert "**://doubleclick.net/**" in patterns
    assert "**://*.doubleclick.net/**" in patterns
    assert "**://googlesyndication.com/**" in patterns
    assert "**://*.googlesyndication.com/**" in patterns
    assert "**/*" not in patterns
    assert all("rollingstone.com" not in pattern for pattern in patterns)


def test_clean_web_style_only_applies_to_external_pages(tmp_path, monkeypatch):
    runner = CaptureRunner(
        scenario={
            "id": "portuguese-desktop",
            "domain": "portuguese",
            "device_profile": "desktop",
        },
        base_url="https://dev.minimoi.ai",
        output_root=tmp_path,
        clean_web=True,
    )
    monkeypatch.setattr(runner, "_prepare_clean_web", lambda _page: None)

    internal = _FakePage("internal", url="https://dev.minimoi.ai/app/portuguese")
    external = _FakePage("external", url="https://rollingstone.com/article")
    runner._take_screenshot(internal, tmp_path / "internal.png")
    runner._take_screenshot(external, tmp_path / "external.png")

    assert internal.last_screenshot_kwargs["style"] == CAPTURE_STYLE
    assert CLEAN_WEB_STYLE not in internal.last_screenshot_kwargs["style"]
    assert CLEAN_WEB_STYLE in external.last_screenshot_kwargs["style"]


def test_loopback_captures_refuse_every_other_host():
    from scripts.tools.tour_capture.runner import local_only_request
    assert local_only_request("http://127.0.0.1:8791/guild")
    assert local_only_request("http://localhost:8791/static/app.css")
    assert local_only_request("data:image/png;base64,AAAA")
    assert not local_only_request("https://fonts.googleapis.com/css2?family=Inter")
    assert not local_only_request("https://dev.minimoi.ai/guild")
    assert not local_only_request("http://127.0.0.1.evil.example/x")


class _Route:
    def __init__(self):
        self.calls = []

    def fulfill(self, **kwargs):
        self.calls.append(("fulfill", kwargs))

    def abort(self):
        self.calls.append(("abort", {}))


def test_route_steps_stub_an_answer_or_abort():
    from scripts.tools.tour_capture.runner import _route_handler
    route = _Route()
    _route_handler({"url": "**/floor", "status": 503, "body": {"error": "down"}})(route)
    assert route.calls == [("fulfill", {"status": 503, "body": '{"error": "down"}', "content_type": "application/json"})]
    route = _Route()
    _route_handler({"url": "**/floor", "abort": True})(route)
    assert route.calls == [("abort", {})]


class _Response:
    def __init__(self, ok, status=200, text=""):
        self.ok, self.status, self._text = ok, status, text

    def text(self):
        return self._text


class _Request:
    def __init__(self, response):
        self.response, self.posts = response, []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return self.response


class _SamplePage:
    def __init__(self, response):
        self.request = _Request(response)


def _local_runner(tmp_path):
    scenario = {"id": "local-review", "domain": "guild", "device_profile": "desktop", "auth_profile": "none",
                "start_path": "/guild", "steps": []}
    return CaptureRunner(scenario, "http://127.0.0.1:8791", tmp_path)


def test_a_sample_step_posts_its_arguments_to_the_sample_server(tmp_path):
    page = _SamplePage(_Response(True))
    _local_runner(tmp_path)._sample(page, "mc", {"mode": "stream"})
    url, kwargs = page.request.posts[0]
    assert url == "http://127.0.0.1:8791/__tour_sample/mc"
    assert kwargs["data"] == '{"mode": "stream"}'


def test_a_refused_sample_step_fails_the_run(tmp_path):
    import pytest
    from scripts.tools.tour_capture.runner import CaptureRunError
    page = _SamplePage(_Response(False, 400, '{"error": "unknown sample action"}'))
    with pytest.raises(CaptureRunError, match="sample server refused"):
        _local_runner(tmp_path)._sample(page, "nope", {})

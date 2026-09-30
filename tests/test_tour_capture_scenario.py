import copy
from pathlib import Path

import pytest

from scripts.tools.tour_capture.runner import (
    CaptureRunError,
    CaptureRunner,
    validate_auth_for_base_url,
    validate_base_url,
)
from scripts.tools.tour_capture.scenario import (
    ScenarioValidationError,
    load_scenario,
    output_filename,
    resolve_device_profile,
    validate_scenario,
)


ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / "scripts" / "tools" / "tour_capture" / "scenarios" / "portuguese_reading.json"
DESKTOP_SCENARIOS = tuple(
    ROOT / "scripts" / "tools" / "tour_capture" / "scenarios" / f"{domain}_desktop.json"
    for domain in ("curator", "german", "portuguese", "guild", "cos")
)


def test_portuguese_scenario_is_valid_and_operator_assisted():
    scenario = load_scenario(SCENARIO)
    assert scenario["auth_profile"] == "owner_session"
    assert scenario["_summary"] == {"screenshots": 5, "operator_pauses": 3}


def test_portuguese_templates_expose_additive_capture_selectors():
    template_dir = ROOT / "domains" / "portuguese" / "templates"
    landing = (template_dir / "portuguese_landing.html").read_text()
    reading = (template_dir / "portuguese_leitura.html").read_text()

    assert 'data-tour-capture="pt-landing"' in landing
    for selector in (
        'data-tour-capture="reading-categories"',
        'data-tour-capture="article-list"',
        'data-tour-capture="article-body"',
        'data-tour-capture="translation-result"',
        "row.dataset.tourCapture = 'article-row'",
        "textEl.dataset.tourReady = 'complete'",
    ):
        assert selector in reading

    scenario = load_scenario(SCENARIO)
    article_wait = next(
        step["wait_for"]
        for step in scenario["steps"]
        if isinstance(step.get("wait_for"), dict)
        and "data-tour-ready='complete'" in step["wait_for"]["selector"]
    )
    assert article_wait["text_selector"] == "#reading-text"


def test_desktop_capture_scenarios_cover_every_domain():
    scenarios = {scenario["domain"]: scenario for scenario in (
        load_scenario(path) for path in DESKTOP_SCENARIOS
    )}

    assert set(scenarios) == {"curator", "german", "portuguese", "guild", "cos"}
    for domain, scenario in scenarios.items():
        assert scenario["device_profile"] == "desktop", domain
        assert scenario["auth_profile"] == "owner_session", domain
        assert scenario["_summary"] == {"screenshots": 0, "operator_pauses": 1}, domain
        assert any("free_capture" in step for step in scenario["steps"]), domain


@pytest.mark.parametrize("path", ["/guild", "/guild/build"])
def test_owner_only_guild_start_paths_are_allowed(path):
    scenario = load_scenario(
        ROOT / "scripts" / "tools" / "tour_capture" / "scenarios" / "guild_desktop.json"
    )
    clean = {key: value for key, value in scenario.items() if not key.startswith("_")}
    clean["start_path"] = path
    assert validate_scenario(clean)["start_path"] == path


def test_filename_maps_directly_to_scene_order():
    assert output_filename(1, "portuguese", "landing", "mobile", "png") == (
        "01-portuguese-landing-mobile.png"
    )
    assert output_filename(5, "portuguese", "translation", "mobile", ".webp") == (
        "05-portuguese-translation-mobile.webp"
    )


def test_duplicate_scene_is_rejected():
    scenario = load_scenario(SCENARIO)
    clean = {key: value for key, value in scenario.items() if not key.startswith("_")}
    duplicate = copy.deepcopy(clean)
    screenshot = next(step for step in duplicate["steps"] if "screenshot" in step)
    duplicate["steps"].append(copy.deepcopy(screenshot))
    with pytest.raises(ScenarioValidationError, match="duplicate screenshot"):
        validate_scenario(duplicate)


def test_free_capture_step_is_valid_and_counts_as_an_operator_pause():
    scenario = validate_scenario({
        "id": "loop-example",
        "domain": "guild",
        "device_profile": "mobile",
        "auth_profile": "owner_session",
        "start_path": "/guild",
        "steps": [
            {"goto": "/guild"},
            {"screenshot": "landing", "title": "t", "description": "d", "alt": "a"},
            {"free_capture": {"prefix": "explore"}},
        ],
    })
    assert scenario["_summary"] == {"screenshots": 1, "operator_pauses": 1}


def test_scroll_to_requires_a_non_empty_selector():
    with pytest.raises(ScenarioValidationError, match="scroll_to"):
        validate_scenario({
            "id": "loop-example",
            "domain": "guild",
            "device_profile": "mobile",
            "auth_profile": "owner_session",
            "start_path": "/guild",
            "steps": [
                {"scroll_to": "   "},
                {"screenshot": "s", "title": "t", "description": "d", "alt": "a"},
            ],
        })


def test_free_capture_requires_a_slug_prefix():
    with pytest.raises(ScenarioValidationError, match="prefix"):
        validate_scenario({
            "id": "loop-example",
            "domain": "guild",
            "device_profile": "mobile",
            "auth_profile": "owner_session",
            "start_path": "/guild",
            "steps": [{"free_capture": {"prefix": "Not A Slug"}}],
        })


def test_free_capture_prefix_cannot_collide_with_a_declared_screenshot():
    with pytest.raises(ScenarioValidationError, match="collides"):
        validate_scenario({
            "id": "loop-example",
            "domain": "guild",
            "device_profile": "mobile",
            "auth_profile": "owner_session",
            "start_path": "/guild",
            "steps": [
                {"screenshot": "explore", "title": "t", "description": "d", "alt": "a"},
                {"free_capture": {"prefix": "explore"}},
            ],
        })


def test_screenshot_without_description_is_rejected():
    scenario = load_scenario(SCENARIO)
    clean = {key: value for key, value in scenario.items() if not key.startswith("_")}
    missing_description = copy.deepcopy(clean)
    screenshot = next(step for step in missing_description["steps"] if "screenshot" in step)
    screenshot.pop("description")

    with pytest.raises(ScenarioValidationError, match="requires non-empty description"):
        validate_scenario(missing_description)


@pytest.mark.parametrize(
    "url",
    [
        "https://minimoi.ai",
        "https://www.minimoi.ai",
        "https://example.com",
        "http://dev.minimoi.ai",
    ],
)
def test_production_or_unknown_capture_origins_are_rejected(url):
    with pytest.raises(CaptureRunError, match="production capture is refused"):
        validate_base_url(url)


@pytest.mark.parametrize(
    "url",
    ["https://dev.minimoi.ai", "http://localhost:8000", "http://127.0.0.1:5000"],
)
def test_dev_and_local_capture_origins_are_allowed(url):
    assert validate_base_url(url) == url


def _local_scenario(**overrides):
    scenario = {
        "id": "prototype-review",
        "domain": "prototype",
        "device_profile": "desktop",
        "auth_profile": "none",
        "start_path": "/index.html",
        "steps": [
            {"goto": "/prototype/page.html"},
            {"wait_for": "body"},
            {"screenshot": "landing", "title": "t", "description": "d", "alt": "a"},
        ],
    }
    scenario.update(overrides)
    return scenario


def test_auth_none_accepts_any_absolute_start_and_goto_paths():
    scenario = validate_scenario(_local_scenario())
    assert scenario["_summary"] == {"screenshots": 1, "operator_pauses": 0}


@pytest.mark.parametrize("path", ["index.html", "//evil.example/x", "https://example.com/"])
def test_auth_none_rejects_relative_or_host_start_paths(path):
    with pytest.raises(ScenarioValidationError, match="start_path must be an absolute path"):
        validate_scenario(_local_scenario(start_path=path))


@pytest.mark.parametrize("path", ["page.html", "//evil.example/x", "http://127.0.0.1:1/"])
def test_auth_none_rejects_non_absolute_goto_targets(path):
    scenario = _local_scenario()
    scenario["steps"][0] = {"goto": path}
    with pytest.raises(ScenarioValidationError, match="goto must be an absolute path"):
        validate_scenario(scenario)


@pytest.mark.parametrize("path", ["/", "/index.html", "/prototype/"])
def test_owner_session_keeps_strict_start_path_rule(path):
    with pytest.raises(ScenarioValidationError, match="portal-proxied"):
        validate_scenario(_local_scenario(auth_profile="owner_session", start_path=path))


def test_owner_session_still_accepts_app_start_path():
    scenario = validate_scenario(
        _local_scenario(auth_profile="owner_session", start_path="/app/portuguese")
    )
    assert scenario["start_path"] == "/app/portuguese"


@pytest.mark.parametrize("url", ["http://localhost:18895", "http://127.0.0.1:18895"])
def test_auth_none_is_allowed_on_loopback(url):
    validate_auth_for_base_url("none", url)
    runner = CaptureRunner(validate_scenario(_local_scenario()), url, Path("/nonexistent"))
    assert runner.authenticated is False


def test_auth_none_is_refused_on_dev():
    with pytest.raises(CaptureRunError, match="only for localhost or 127.0.0.1"):
        validate_auth_for_base_url("none", "https://dev.minimoi.ai")
    with pytest.raises(CaptureRunError, match="only for localhost or 127.0.0.1"):
        CaptureRunner(
            validate_scenario(_local_scenario()), "https://dev.minimoi.ai", Path("/nonexistent")
        )


def test_owner_session_is_unaffected_by_local_only_rule():
    validate_auth_for_base_url("owner_session", "https://dev.minimoi.ai")


def test_viewport_override_builds_a_named_profile():
    scenario = validate_scenario(
        _local_scenario(
            device_profile="laptop",
            viewport={"width": 1280, "height": 800, "device_scale_factor": 2},
        )
    )
    profile = resolve_device_profile(scenario)
    assert (profile.name, profile.output_dimensions) == ("laptop", (2560, 1600))
    assert resolve_device_profile(_local_scenario()).output_dimensions == (2880, 1800)


@pytest.mark.parametrize(
    "viewport",
    [{"width": 1280, "height": 800}, {"width": 0, "height": 800, "device_scale_factor": 2}, []],
)
def test_invalid_viewport_is_rejected(viewport):
    with pytest.raises(ScenarioValidationError, match="viewport"):
        validate_scenario(_local_scenario(device_profile="laptop", viewport=viewport))


def test_unknown_profile_without_viewport_is_still_rejected():
    with pytest.raises(ScenarioValidationError, match="unknown device profile"):
        validate_scenario(_local_scenario(device_profile="laptop"))


def _local(steps, **extra):
    return {"id": "local-review", "domain": "guild", "device_profile": "desktop", "auth_profile": "none",
            "start_path": "/guild", "steps": steps + [
                {"screenshot": "one", "title": "One", "description": "One.", "alt": "One"}], **extra}


LOCAL_STEPS = [
    {"sample": "reset"},
    {"sample": "mc", "args": {"mode": "stream", "script": ["Hi", "WAIT"]}},
    {"fill": "#mc-input", "value": "Where do things stand?"},
    {"press": "Enter", "selector": "#mc-input"},
    {"press": "Escape"},
    {"evaluate": "document.dispatchEvent(new Event('visibilitychange'))"},
    {"route": {"url": "**/api/v1/floor", "status": 503, "body": {"error": "unavailable"}}},
    {"route": {"url": "**/api/v1/workshop*", "abort": True}},
    {"unroute": "**/api/v1/floor"},
    {"wait_for": {"selector": "[data-mc-thread]", "text": "Hi"}},
]


def test_local_review_actions_are_valid_with_auth_none():
    scenario = validate_scenario(_local(copy.deepcopy(LOCAL_STEPS), mobile_emulation=True))
    assert scenario["_summary"] == {"screenshots": 1, "operator_pauses": 0}


@pytest.mark.parametrize("step", [s for s in LOCAL_STEPS if next(iter(s)) in
                                  ("sample", "fill", "evaluate", "route", "unroute")],
                         ids=lambda s: next(iter(s)))
def test_local_only_actions_are_refused_with_the_owner_session(step):
    scenario = _local([copy.deepcopy(step)])
    scenario["auth_profile"] = "owner_session"
    with pytest.raises(ScenarioValidationError, match="only with auth_profile 'none'"):
        validate_scenario(scenario)


def test_press_is_allowed_with_the_owner_session():
    scenario = _local([{"press": "Escape"}])
    scenario["auth_profile"] = "owner_session"
    validate_scenario(scenario)


@pytest.mark.parametrize("step", [
    {"fill": "#mc-input"},
    {"fill": "", "value": "x"},
    {"press": "Escape; rm"},
    {"evaluate": "  "},
    {"route": {"status": 503}},
    {"route": {"url": "**/x", "status": 99}},
    {"route": {"url": "**/x", "abort": True, "status": 200}},
    {"route": {"url": "**/x", "status": 200, "headers": {}}},
    {"sample": "Drop Tables"},
    {"sample": "mc", "args": ["stream"]},
    {"wait_for": {"selector": "body", "text": ""}},
], ids=repr)
def test_malformed_local_steps_are_rejected(step):
    with pytest.raises(ScenarioValidationError):
        validate_scenario(_local([step]))


def test_mobile_emulation_must_be_a_boolean():
    with pytest.raises(ScenarioValidationError, match="mobile_emulation"):
        validate_scenario(_local([], mobile_emulation="yes"))


GUILD_REVIEW = ROOT / "scripts" / "tools" / "tour_capture" / "scenarios"


@pytest.mark.parametrize("path", sorted(GUILD_REVIEW.glob("guild_1_1_*.json")), ids=lambda p: p.name)
def test_guild_review_scenarios_are_valid_local_captures(path):
    scenario = load_scenario(path)
    assert scenario["auth_profile"] == "none"
    assert scenario["_summary"]["operator_pauses"] == 0
    assert scenario["steps"][0] == {"sample": "reset"}
    for step in scenario["steps"]:
        if "screenshot" in step:
            assert "sample" in step["description"].lower() or "stand-in" in step["description"].lower() \
                or "scripted" in step["description"].lower(), step["screenshot"]


def test_a_click_may_declare_that_it_navigates():
    validate_scenario(_local([{"click": "[data-conv-new]", "navigates": True}]))
    with pytest.raises(ScenarioValidationError, match="navigates"):
        validate_scenario(_local([{"click": "[data-conv-new]", "navigates": "yes"}]))

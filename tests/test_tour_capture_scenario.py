import copy
from pathlib import Path

import pytest

from scripts.tools.tour_capture.runner import CaptureRunError, validate_base_url
from scripts.tools.tour_capture.scenario import (
    ScenarioValidationError,
    load_scenario,
    output_filename,
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

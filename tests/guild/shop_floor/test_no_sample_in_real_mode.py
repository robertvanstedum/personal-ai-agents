"""B1 (b), written first: the real Shop floor contains and shows no sample data.

Binding rule B2 ("unknown, never sample, never zero") and conflicts C4, C19,
C20: the lifted package has no fixtures, no scenario, no sample adapter, no
scripted conversation and no simulated clock, and no page or API response
carries the prototype's sample strings unless the real queue has them.
"""
from __future__ import annotations

import importlib
import pkgutil
import re
from pathlib import Path

import pytest
from floor_helpers import load_portal, staging  # noqa: F401  (pytest fixtures)


REPO = Path(__file__).resolve().parents[3]
PACKAGE = REPO / "minimoi_portal" / "guild_ui"

# Words and markers that only the prototype's scenario and fixtures produce.
FORBIDDEN_TEXT = re.compile(
    r"\bsamples?\b|simulat|Sat 9:1\d|Mon 8:\d\d|\br-44\d\d\b|#158\b|\bs-02\d\b|scenario|fixture",
    re.IGNORECASE,
)
FORBIDDEN_FILES = {
    "scenario.json", "usage.py", "evidence.py", "scenario.js", "capture.js", "proposals.js",
    "world.js",  # the prototype's clock and receipt numbering (C19)
}


def _package_files():
    return [p for p in PACKAGE.rglob("*") if p.is_file() and "__pycache__" not in p.parts]


def test_package_exists_with_templates_and_assets():
    names = {p.name for p in _package_files()}
    assert "__init__.py" in names and "floor.html" in names and "components.css" in names


def test_package_has_no_fixture_scenario_or_prototype_only_file():
    for path in _package_files():
        rel = path.relative_to(PACKAGE)
        assert "fixtures" not in rel.parts, rel
        assert path.name not in FORBIDDEN_FILES, rel
        assert not path.name.endswith(".sample.json"), rel


def test_no_package_file_mentions_sample_or_prototype_scenario_text():
    offenders = []
    for path in _package_files():
        text = path.read_text(encoding="utf-8")
        for match in FORBIDDEN_TEXT.finditer(text):
            offenders.append(f"{path.relative_to(REPO)}: {match.group(0)!r}")
    assert not offenders, "\n".join(offenders)


def test_no_sample_class_or_function_is_importable():
    import minimoi_portal.guild_ui as pkg
    seen = []
    for info in pkgutil.walk_packages(pkg.__path__, prefix="minimoi_portal.guild_ui."):
        module = importlib.import_module(info.name)
        for name in dir(module):
            if re.search(r"sample|scenario|fixture|simulat", name, re.IGNORECASE):
                seen.append(f"{info.name}.{name}")
    assert not seen, seen


def test_prototype_mode_does_not_exist_in_the_real_package():
    from flask import Flask
    from minimoi_portal.guild_ui import GuildBindingError, register_guild_ui
    with pytest.raises(GuildBindingError, match="prototype mode lives in prototype-lab"):
        register_guild_ui(Flask("x"), prototype=True, owner_guard=lambda f: f,
                          current_user=lambda: None, url_prefix="/x", blueprint_name="x",
                          services=None)


def test_package_never_imports_the_prototype():
    for path in PACKAGE.rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            code = line.strip()
            if code.startswith(("import ", "from ")):
                assert "proto" not in code and "guild_interaction" not in code, (path, code)
            assert "sys.path" not in code and "spec_from_file_location" not in code, (path, code)


PAGES = ["/guild-next/guild/build", "/guild-next/guild/build/bench", "/guild-next/guild/build/queue",
         "/guild-next/guild/build/items/12", "/guild-next/guild/build/items/31",
         "/guild-next/guild/operate", "/guild-next/guild/operate?tile=systems", "/guild-next/guild/build/postits"]
API = ["/guild-next/api/v1/session", "/guild-next/api/v1/floor", "/guild-next/api/v1/queue",
       "/guild-next/api/v1/queue/items/12", "/guild-next/api/v1/queue/items/12/history",
       "/guild-next/api/v1/notes", "/guild-next/api/v1/postits", "/guild-next/api/v1/postits/bin",
       "/guild-next/api/v1/continue"]
ASSETS = ["tokens.css", "components.css", "js/main.js", "js/api.js", "js/conversation.js",
          "js/floor.js", "js/bench.js", "js/queue.js", "js/actions.js", "js/operate.js",
          "js/state.js", "js/dom.js", "js/postits.js", "js/continue.js", "js/zones.js"]


def test_no_page_api_or_asset_response_shows_sample_text(staging):
    client = staging.owner()
    urls = PAGES + API + [f"/guild-next/guild/ui-assets/{a}" for a in ASSETS]
    for url in urls:
        response = client.get(url)
        assert response.status_code == 200, (url, response.status_code)
        body = response.get_data(as_text=True)
        match = FORBIDDEN_TEXT.search(body)
        assert match is None, (url, match.group(0) if match else None)


def test_a_queue_item_with_a_prototype_like_id_is_shown_only_because_the_queue_has_it(load_portal):
    portal = load_portal(items=[{"id": 158, "spec_title": "Real item", "status": "in_build"}])
    body = portal.owner().get("/guild-next/guild/build/queue").get_data(as_text=True)
    assert "#158" in body or "158" in body
    empty = load_portal(items=[])
    body = empty.owner().get("/guild-next/guild/build/queue").get_data(as_text=True)
    assert "158" not in body


def test_real_floor_says_master_craftsman_is_off_and_filing_is_off(staging):
    body = staging.owner().get("/guild-next/guild/build").get_data(as_text=True)
    assert "Master Craftsman is off" in body
    assert "Filing is off until the Record is specified (#235). Nothing is filed." in body
    assert "Inviting agents needs Rooms; not connected" in body
    assert "Hold to talk" not in body
    assert "Prototype" not in body and "Reset fixtures" not in body

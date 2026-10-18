"""Compact MC controls (owner, 18 Oct 2026): message, attachment, Send and Voice are visible; transcript, provider and Private sit
under one small menu with few words; record confirmation and the Private chip stay in view. The Voice choice offers OpenAI and shows
Grok (xAI) plainly as not available until this host can run it. Server render only; no provider, microphone or network."""
from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from floor_helpers import load_portal, write_headers  # noqa: F401
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401
from test_mc_streaming import _fresh_dispatch_state, streaming  # noqa: F401
from test_mc_turn_capture import mc_dir, mc_lines  # noqa: F401
from test_mc_turns import API, FakeRuntime, _keep, _openclaw, _turn_on, turned  # noqa: F401
from test_mc_voice import _on, _page, provider  # noqa: F401


def _composer(client):
    html = _page(client).get_data(as_text=True)
    return BeautifulSoup(html, "html.parser").select_one("[data-mc-composer]")


def test_voice_sits_with_send_and_settings_sit_in_one_menu(turned, provider, monkeypatch):
    _on(monkeypatch)
    form = _composer(turned.owner())
    row = form.select_one(".mc-row")
    controls = [el for el in row.find_all(True) if el.has_attr("data-mc-send") or el.has_attr("data-mc-voice-btn")]
    assert [("send" if el.has_attr("data-mc-send") else "voice") for el in controls] == ["send", "voice"]   # Voice right after Send
    assert row.select_one("[data-mc-input]") is not None
    menu = form.select_one("details[data-mc-options]")
    assert menu is not None and menu.summary["aria-label"] == "Conversation options"
    for hook in ("data-mc-managed-settings", "data-mc-record", "data-mc-voice-provider", "data-mc-voice-transcript-choice"):
        assert menu.find(attrs={hook: True}) is not None, hook
    tools = form.select_one(".mc-tools")
    assert tools.find(attrs={"data-mc-voice-btn": True}) is None
    for state in ("data-mc-record-confirm", "data-private-chip"):          # states stay visible, outside the menu
        node = form.find(attrs={state: True})
        assert node is not None and node.find_parent("details") is None, state


def test_the_voice_choice_is_truthful_about_grok(turned, provider, monkeypatch):
    _on(monkeypatch)
    select = _composer(turned.owner()).select_one("[data-mc-voice-provider]")
    options = {o["value"]: o for o in select.find_all("option")}
    assert set(options) == {"openai", "xai"}
    assert not options["openai"].has_attr("disabled")
    assert options["xai"].has_attr("disabled") and "not available" in options["xai"].get_text()


def test_menu_labels_are_short(turned, provider, monkeypatch):
    _on(monkeypatch)
    body = _composer(turned.owner()).select_one(".mc-options-body")
    words = body.get_text(" ", strip=True)
    for long in ("Show live transcript", "Download saved voice transcript", "Voice provider "):
        assert long not in words
    assert len(words.split()) <= 16


def test_without_voice_the_menu_still_holds_private_and_no_voice_controls_render(turned, monkeypatch):
    form = _composer(turned.owner())
    assert form.find(attrs={"data-mc-voice-btn": True}) is None
    assert form.find(attrs={"data-mc-voice-provider": True}) is None
    assert form.select_one("details[data-mc-options]").find(attrs={"data-mc-record": True}) is not None


def test_voice_styles_follow_the_button_to_the_composer_row():
    from pathlib import Path
    css = (Path(__file__).resolve().parents[3] / "minimoi_portal/guild_ui/static/components.css").read_text()
    assert ".mc-tools .mc-voice" not in css                           # the button left .mc-tools; no rule may be left behind
    assert ".mc-composer .mc-voice {" in css and ".mc-row .mc-input { min-width: 0; }" in css


def test_send_and_voice_never_shrink_or_break_their_labels():
    """Codex rendered check, 18 Oct, 390 px floor: Send wrapped as "Sen"/"d" (flex-shrink 1 + body overflow-wrap anywhere)."""
    from pathlib import Path
    css = (Path(__file__).resolve().parents[3] / "minimoi_portal/guild_ui/static/components.css").read_text()
    assert ".mc-row .mc-voice, .mc-row [data-mc-send], .mc-row .mc-attach { flex: 0 0 auto; }" in css
    assert ".mc-row [data-mc-send], .mc-row .mc-voice { white-space: nowrap; overflow-wrap: normal; }" in css


def test_the_page_wires_the_options_menu_tested_in_mc_options_menu_mjs():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[3] / "minimoi_portal/guild_ui/static/js/conversation.js").read_text()
    init = js[js.index("export function initConversation(p) {"):]
    assert "wireOptionsMenu($('[data-mc-options]'), document);" in init     # behaviour: tests/mc_options_menu.mjs

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
    for hook in ("data-mc-managed-settings", "data-mc-record", "data-partner-voice", "data-partner-model", "data-mc-voice-transcript-choice"):
        assert menu.find(attrs={hook: True}) is not None, hook
    tools = form.select_one(".mc-tools")
    assert tools.find(attrs={"data-mc-voice-btn": True}) is None
    for state in ("data-mc-record-confirm", "data-private-chip"):          # states stay visible, outside the menu
        node = form.find(attrs={state: True})
        assert node is not None and node.find_parent("details") is None, state


def test_the_menu_is_the_shared_one_in_the_shared_order(turned, provider, monkeypatch):
    """#661: MC's ⋯ body is founders_ui's partner menu: Dictate, Voice (OpenAI, Grok), Model, Private, the footnote."""
    _on(monkeypatch)
    menu = _composer(turned.owner()).select_one("[data-partner-menu]")
    assert menu is not None and menu["data-partner"] == "mc"
    options = [o["value"] for o in menu.select_one("[data-partner-voice]").find_all("option")]
    assert options == ["openai", "xai"]                                     # no Claude in Voice
    hooks = [el for el in menu.find_all(True) if el.has_attr("data-dictate") or el.has_attr("data-partner-voice")
             or el.has_attr("data-partner-model") or el.has_attr("data-mc-record")]
    order = ["dictate" if h.has_attr("data-dictate") else "voice" if h.has_attr("data-partner-voice")
             else "model" if h.has_attr("data-partner-model") else "private" for h in hooks]
    assert order in (["dictate", "voice", "model", "private"], ["voice", "model", "private"])   # Dictate only where it is on
    assert menu.select_one(".partner-menu-foot").get_text(strip=True) == "Voice is AI-generated."
    assert menu.find(attrs={"data-mc-voice-provider": True}) is None and menu.find(attrs={"data-mc-route": True}) is None


def test_menu_labels_are_short(turned, provider, monkeypatch):
    _on(monkeypatch)
    body = _composer(turned.owner()).select_one(".mc-options-body")
    for state in body.select(".partner-menu-lock[hidden], .mc-walk-note[hidden]"):   # state lines, shown only when true
        state.decompose()
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


def test_the_walk_menu_never_promises_a_grok_call(turned, provider, monkeypatch):
    """Owner, 26 Nov ("B but keep the walk"): production's MC voice is the walk, OpenAI only. With Grok chosen the menu says
    the call stays on OpenAI, and the call says it again; typed MC still follows the choice."""
    from pathlib import Path
    _on(monkeypatch)
    note = _composer(turned.owner()).select_one("[data-mc-walk-note]")
    assert note is not None and note.has_attr("hidden") and note.get_text(strip=True) == "Calls stay on OpenAI."
    js = (Path(__file__).resolve().parents[3] / "minimoi_portal/guild_ui/static/js/mc_voice.js").read_text()
    assert "note.hidden = !(walk && $('[data-partner-voice]')?.value === 'xai')" in js
    assert "const WALK_OPENAI = 'MC voice calls stay on OpenAI.';" in js and "if (walk) { await startWalk(mine, current); return; }" in js

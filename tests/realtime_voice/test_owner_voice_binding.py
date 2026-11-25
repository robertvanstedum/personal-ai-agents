"""#659/#662 (Lane V): a direct call is minted on the owner's Voice, kept by MiniMoi, with the card's voice for it; a page
asking for another provider (a change on another device) is refused before anything is minted. Both twins' cards name a
voice per provider, and their sessions carry the clock."""
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).parent))
from test_direct_host import BASE, ENV, OWNER, Host, card_release  # noqa: E402

from core.realtime_voice.budget import VoiceBudget  # noqa: E402
from core.realtime_voice.direct_host import clock_line, install_direct_voice  # noqa: E402
from core.realtime_voice.voice_card import load_pinned  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


class Kept(Host):
    saved = None

    def saved_voice(self, user_id):
        assert user_id == "robert"
        return self.saved


@pytest.fixture
def rig(monkeypatch, tmp_path):
    card_release(tmp_path / "card", "1.0.0", {"card": "Test Person.", "language": "pt-BR", "accent": "plain",
                                               "voices": {"openai": "cedar", "xai": "rex"}})
    app, host, mints = Flask("owner-voice"), Kept(tmp_path / "card"), []

    def mint(provider):
        def go(**kw):
            mints.append((provider, kw.get("voice")))
            return {"provider": provider, "client_secret": "ek", "ephemeral_token": "t", "expires_at": 1, "model": "m",
                    "session_config": {}}
        return go
    monkeypatch.setattr("core.realtime_voice.bootstrap.openai_realtime.mint_ephemeral_credential", mint("openai"))
    monkeypatch.setattr("core.realtime_voice.bootstrap.xai_voice.mint_ephemeral_credential", mint("xai"))
    monkeypatch.setattr("core.realtime_voice.bootstrap._rate_limit_state", {})
    budget = VoiceBudget("sqlite:///" + str(tmp_path / "budget.db"), cap_usd=lambda: 20)
    budget.ensure_schema()
    install_direct_voice(app, host, url_prefix="/tp/voice", environ=dict(ENV), budget=budget, clock=time.time)
    return SimpleNamespace(client=app.test_client(), host=host, mints=mints)


def _start(r, **body):
    return r.client.post(BASE + "/bootstrap", headers=OWNER, json={"persona": "test_person", "conversation_id": "owner", **body})


@pytest.mark.parametrize("voice,expected", [("xai", "rex"), ("openai", "cedar")])
def test_the_call_is_minted_on_the_owners_voice_with_the_cards_voice(rig, voice, expected):
    rig.host.saved = voice
    answer = _start(rig)                                         # the page names nothing: the server's choice binds
    assert answer.status_code == 200, answer.get_json()
    assert rig.mints == [(voice, expected)]


def test_a_stale_page_asking_for_another_provider_is_refused_and_nothing_is_minted(rig):
    rig.host.saved = "xai"
    answer = _start(rig, provider="openai")
    assert answer.status_code == 409 and answer.get_json()["error"] == "Voice changed on another device. Reload to use it."
    assert rig.mints == []
    assert _start(rig, provider="xai").status_code == 200 and rig.mints == [("xai", "rex")]


def test_without_a_kept_choice_the_page_choice_applies_as_before(rig):
    rig.host.saved = None
    assert _start(rig, provider="xai").status_code == 200 and rig.mints == [("xai", "rex")]


@pytest.mark.parametrize("root,voices", [
    ("services/mc_operations/personas/mc-chat", {"openai": "marin", "xai": "leo"}),
    ("domains/cos/voice", {"openai": "ballad", "xai": "ara"}),
])
def test_both_twins_cards_name_a_voice_for_each_provider(root, voices):
    """#662: MC on Grok is leo, never OpenAI's marin; CoS likewise has her own per provider."""
    card = load_pinned(ROOT / root)
    assert card["voices"] == voices and sorted(card["named"]) == ["openai", "xai"]


def test_the_clock_is_in_every_direct_session():
    from minimoi_portal.guild_ui.mc_voice_direct import _MasterCraftsman  # noqa: F401  (MC's adapter composes direct_rules)
    from core.realtime_voice.direct_host import direct_rules
    rules = direct_rules({"key": "x", "name": "X", "short": "X", "domain": "x", "notes": ()}, "the owner", False)
    assert "Clock: it is " in rules and "Central time in Chicago" in clock_line(0)


def test_both_adapters_read_the_owners_voice_from_the_partner_settings():
    for path in ("minimoi_portal/guild_ui/mc_voice_direct.py", "domains/cos/direct_voice.py"):
        text = (ROOT / path).read_text()
        assert "def saved_voice(self, user_id):" in text and "bound_voice(" in text, path

"""#661: one ⋯ menu for MC and CoS, from core/founders_ui, with the same controls, order and words on both pages."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, PrefixLoader, select_autoescape
from markupsafe import Markup

ROOT = Path(__file__).resolve().parents[2]
SHARED = ROOT / "core/founders_ui"


def _render(partner):
    env = Environment(loader=PrefixLoader({"founders_ui": FileSystemLoader(SHARED / "templates")}),
                      autoescape=select_autoescape(["html"]))
    macro = env.get_template("founders_ui/_partner_menu.html").module.partner_menu
    return str(macro(partner, dictate=Markup("<button data-dictate>Dictate</button>"),
                     private=Markup("<button data-private>Private</button>")))


def test_the_partial_is_the_same_for_both_twins_but_its_partner():
    mc, cos = _render("mc"), _render("cos")
    assert mc.replace('data-partner="mc"', "") == cos.replace('data-partner="cos"', "")
    order = [m.group(0) for m in re.finditer(r"data-dictate|data-partner-voice(?![-\w])|data-partner-model(?![-\w])|data-private|"
                                             r"data-partner-last|Voice is AI-generated\.", mc)]
    assert order == ["data-dictate", "data-partner-voice", "data-partner-model", "data-private", "data-partner-last",
                     "Voice is AI-generated."]
    assert re.findall(r'<option value="(\w+)"', mc) == ["openai", "xai"]          # no Claude in Voice
    assert "plan-" not in mc and "api-" not in mc and "route" not in mc.lower()


def test_both_pages_use_the_partial_and_load_the_one_module():
    mc = (ROOT / "minimoi_portal/guild_ui/templates/guild_floor/_mc_conversation.html").read_text()
    cos = (ROOT / "domains/cos/templates/cos_ui.html").read_text()
    for page, partner in ((mc, "mc"), (cos, "cos")):
        assert '{% from "founders_ui/_partner_menu.html" import partner_menu %}' in page
        assert f'partner_menu("{partner}", dictate=dictate_btn, private=private_btn)' in page
    assert "/founders-ui/partner-menu.js" in (ROOT / "minimoi_portal/guild_ui/static/js/conversation.js").read_text()
    assert "/founders-ui/partner-menu.js" in cos
    for base in ("minimoi_portal/guild_ui/templates/guild_floor/_base.html", "domains/cos/templates/cos_ui.html"):
        assert "/founders-ui/partner-menu.css?v={{ founders_ui_version }}" in (ROOT / base).read_text()
    for gone in ("minimoi_portal/guild_ui/static/js/route_picker.js", "domains/cos/static/cos-route-picker.js"):
        assert not (ROOT / gone).exists()


def test_no_voice_choice_is_kept_in_the_browser():
    for path in ("minimoi_portal/guild_ui/static/js/mc_voice.js", "domains/cos/static/confer-voice.js",
                 "domains/cos/templates/cos_ui.html", "core/founders_ui/static/partner-menu.js"):
        text = (ROOT / path).read_text()
        assert "minimoi.voice.provider" not in text and "minimoi.cos.voice.provider" not in text, path


def test_the_menu_script_for_both_twins():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js unavailable")
    run = subprocess.run([node, str(Path(__file__).with_name("partner_menu.test.mjs"))], capture_output=True, text=True,
                         timeout=30, env=dict(os.environ), check=False)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "partner-menu checks passed for MC and CoS" in run.stdout



def test_a_locked_voice_says_why_and_phones_get_new_menu_code_and_an_unclipped_menu():
    """Staging, 26 Nov: on iPhone the thread hid Dictate, the greyed Voice gave no reason, and the menu's code (imported by
    its plain path, kept 4 hours by Cloudflare) could stay old after a deploy."""
    js = (ROOT / "minimoi_portal/guild_ui/static/js/conversation.js").read_text()
    assert "import(`/founders-ui/partner-menu.js${revision ? `?v=${encodeURIComponent(revision)}` : ''}`)" in js
    assert "import { initPartnerMenu } from '/founders-ui/partner-menu.js'" not in js
    base = (ROOT / "minimoi_portal/guild_ui/templates/guild_floor/_base.html").read_text()
    assert 'data-founders-ui="{{ founders_ui_version }}"' in base
    css = (SHARED / "static/founders.css").read_text()
    assert "body.gu[data-page=floor] .mc-composer{z-index:45}" in css
    assert ".founders-cos .input-row{position:relative;z-index:45;" in css

"""CoS's model choice lives in the ••• options beside ＋, as MC's, never loose on the page (Robert, 7 Nov 2026)."""
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VOID = {"input", "br", "img", "link", "meta", "hr"}


class Finder(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.found = [], None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id") == "cos-route-control":
            self.found = (tag, attrs, [a for a in self.stack if a is not None])
        if tag not in VOID:
            self.stack.append("data-cos-options" if "data-cos-options" in attrs else None)

    def handle_endtag(self, tag):
        if tag not in VOID and self.stack:
            self.stack.pop()


def test_the_model_choice_sits_inside_the_options_menu_and_starts_hidden():
    finder = Finder()
    finder.feed((ROOT / "domains/cos/templates/cos_ui.html").read_text(encoding="utf-8"))
    assert finder.found, "the Model control is gone"
    _, attrs, ancestors = finder.found
    assert "data-cos-options" in ancestors
    assert "hidden" in attrs


def test_the_menu_row_stays_hidden_while_routes_are_off():
    # The row's display:flex would otherwise beat the hidden attribute, and show an empty Model on hosts without routes.
    css = (ROOT / "core/founders_ui/static/founders.css").read_text(encoding="utf-8")
    assert ".founders-cos .conv-menu-route[hidden]{display:none}" in css


def test_voice_starts_from_one_place_and_the_options_hold_dictate_and_voice():
    template = (ROOT / "domains/cos/templates/cos_ui.html").read_text(encoding="utf-8")
    assert "data-voice-start" not in template and "Talk with CoS" not in template
    body = template[template.index("data-cos-options"):template.index("</details>", template.index("data-cos-options"))]
    assert 'id="btn-dictate"' in body and 'id="voice-provider"' in body
    assert 'id="btn-voice"' in template.split('class="input-row"')[1]

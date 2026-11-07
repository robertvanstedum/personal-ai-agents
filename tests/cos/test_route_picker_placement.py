"""CoS's model choice lives in the chat's ⋯ menu, never loose on the page (Robert, 7 Nov 2026)."""
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
            self.stack.append("data-conv-menu" if "data-conv-menu" in attrs else None)

    def handle_endtag(self, tag):
        if tag not in VOID and self.stack:
            self.stack.pop()


def test_the_model_choice_sits_inside_the_conversation_menu_and_starts_hidden():
    finder = Finder()
    finder.feed((ROOT / "domains/cos/templates/cos_ui.html").read_text(encoding="utf-8"))
    assert finder.found, "the Model control is gone"
    _, attrs, ancestors = finder.found
    assert "data-conv-menu" in ancestors
    assert "hidden" in attrs


def test_the_menu_row_stays_hidden_while_routes_are_off():
    # The row's display:flex would otherwise beat the hidden attribute, and show an empty Model on hosts without routes.
    css = (ROOT / "core/founders_ui/static/founders.css").read_text(encoding="utf-8")
    assert ".founders-cos .conv-menu-route[hidden]{display:none}" in css

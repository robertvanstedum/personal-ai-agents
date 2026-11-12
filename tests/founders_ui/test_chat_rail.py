"""FF-01: one way into the chat list. The rail's ☰ opens it and search is the filter box inside it; no ⌕ on any rail,
and no second ☰ in MC's header wherever the rail shows. The browser behaviour is in browser_checks_b1.py."""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SIDEBAR = (REPO / "core/founders_ui/static/chat-sidebar.js").read_text()
CHAT_CSS = (REPO / "core/founders_ui/static/chat.css").read_text()
COS = (REPO / "domains/cos/templates/cos_ui.html").read_text()


def test_the_shared_rail_builds_open_and_new_only():
    assert "⌕" not in SIDEBAR and "Search chats" not in SIDEBAR
    assert "button('Open chat list', '☰'" in SIDEBAR


def test_the_cos_rail_has_no_search_button():
    rail = COS[COS.index('<nav class="cos-rail"'):COS.index("</nav>", COS.index('<nav class="cos-rail"'))]
    assert "data-list-open" in rail and "data-list-search" not in rail and "⌕" not in rail


def test_the_mc_header_menu_button_is_a_phones_only_where_the_rail_shows():
    rule = re.search(r"@media\(min-width:640px\)\{([^{}]*\.mc-floorbar \[data-history-toggle\][^{}]*)\{display:none\}\}", CHAT_CSS)
    assert rule, "MC header ☰ must hide from 640 px, where the rail's ☰ shows"
    # …and the rail itself is hidden only below 640 px, so the two never leave a width with no way in.
    assert re.search(r"@media\(max-width:639px\)\{[^@]*\.mm-chat-rail\{display:none!important\}", CHAT_CSS)

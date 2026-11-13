"""#566: on the phone, Craft's and Confer's art is a full-width band, never a letterboxed thumbnail.

The rendered checks are browser_checks_beta_ui.py (Craft) and tests/cos/browser_checks_confer_phone_art.py (Confer)."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PAGES = {
    'craft': (ROOT / 'minimoi_portal/guild_ui/static/css/calm-craft.css', 'body.gu[data-page=floor] .calm-craft-picture'),
    'confer': (ROOT / 'domains/cos/static/calm-confer.css', '.founders-cos .confer-picture'),
}


def _phone_block(css):
    start = css.index('@media(max-width:639px){')
    depth, i = 0, css.index('{', start)
    for j in range(i, len(css)):
        depth += {'{': 1, '}': -1}.get(css[j], 0)
        if depth == 0:
            return css[i:j]
    raise AssertionError('unclosed phone block')


def _rule(block, selector):
    m = re.search(re.escape(selector) + r'\{([^}]*)\}', block)
    assert m, selector
    return m.group(1)


@pytest.mark.parametrize('page', sorted(PAGES))
def test_phone_art_fills_the_band_edge_to_edge(page):
    path, picture = PAGES[page]
    phone = _phone_block(path.read_text())
    assert 'calc(20dvh - 16px)' not in phone and '130px' not in phone      # the old thumbnail cap
    box = _rule(phone, picture)
    assert 'padding:0' in box and 'background:none' in box
    img = _rule(phone, picture + ' img')
    assert 'object-fit:cover' in img and 'height:100%' in img and 'max-height:none' in img and 'object-position:' in img
    workspace = _rule(phone, picture.replace('-picture', '-workspace'))
    assert re.search(r'grid-template-rows:clamp\(\d+px,3\ddvh,\d+px\) minmax\(0,1fr\)', workspace), workspace


@pytest.mark.parametrize('page', sorted(PAGES))
def test_desktop_art_keeps_the_whole_picture(page):
    path, picture = PAGES[page]
    css = path.read_text()
    desktop = css[:css.index('@media')]
    assert 'object-fit:contain' in _rule(desktop, picture + ' img')
